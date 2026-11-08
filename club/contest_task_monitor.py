"""Private execution records and conservative restart detection; no UI changes."""
import logging
import os
import threading
import uuid
from datetime import timedelta

from celery.signals import before_task_publish, task_received, task_prerun, task_postrun, task_failure, worker_init, heartbeat_sent, worker_shutdown
from django.core.cache import caches
from django.db import close_old_connections, connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)
TRACKED = {'club.tasks.discover_contest_progress', 'club.tasks.hydrate_contest_progress'}
_heartbeats = {}


@worker_init.connect
def worker_started(**kwargs):
    # Inherited by prefork children; changes even when the hostname is reused.
    os.environ['CLUB_CONTEST_WORKER_EPOCH'] = uuid.uuid4().hex


def worker_key(epoch):
    return 'contest-worker:v1:' + epoch


@heartbeat_sent.connect
def worker_heartbeat(**kwargs):
    epoch = os.environ.get('CLUB_CONTEST_WORKER_EPOCH')
    if epoch:
        try:caches['platform_requests'].set(worker_key(epoch), 'alive', 90)
        except Exception:logger.warning('Contest worker heartbeat unavailable', exc_info=True)


@worker_shutdown.connect
def worker_stopped(**kwargs):
    epoch = os.environ.get('CLUB_CONTEST_WORKER_EPOCH')
    if epoch:
        try:caches['platform_requests'].set(worker_key(epoch), 'stopped', 86400)
        except Exception:logger.warning('Contest worker shutdown marker unavailable', exc_info=True)


def run_identity(name, args, kwargs):
    from .models import ContestParticipation
    token = args[1] if len(args) > 1 else kwargs.get('token')
    pk = args[0] if args else kwargs.get('user_id' if name.endswith('discover_contest_progress') else 'contest_id')
    if name.endswith('hydrate_contest_progress'):
        pk = ContestParticipation.objects.filter(pk=pk).values_list('user_id', flat=True).first()
    return pk, token


def register(task_id, name, args, kwargs):
    from .models import ContestSyncState, ContestTaskRun
    uid, token = run_identity(name, args, kwargs)
    sync = ContestSyncState.objects.filter(user_id=uid, token=token, status='pending').first()
    if sync:
        ContestTaskRun.objects.get_or_create(task_id=task_id,
            defaults={'sync':sync, 'token':token, 'task_name':name})


@before_task_publish.connect
def published(sender=None, headers=None, body=None, **kwargs):
    if sender not in TRACKED or not headers or not body:return
    try:register(headers['id'], sender, body[0], body[1])
    except Exception:logger.exception('Unable to register contest task; legacy timeout remains available')


@task_received.connect
def received(request=None, **kwargs):
    if not request or request.name not in TRACKED:return
    try:
        from .models import ContestTaskRun
        register(request.id,request.name,request.args,request.kwargs)
        ContestTaskRun.objects.filter(task_id=request.id,status='queued').update(
            status='reserved',worker_epoch=os.environ.get('CLUB_CONTEST_WORKER_EPOCH',''),heartbeat_at=timezone.now())
    except Exception:logger.exception('Unable to record reserved contest task')


def pulse(task_id):
    from .models import ContestTaskRun, ContestSyncState
    now = timezone.now()
    row = ContestTaskRun.objects.filter(task_id=task_id,status='running').first()
    if not row:return
    ContestTaskRun.objects.filter(task_id=task_id,status='running').update(heartbeat_at=now,missing_since=None)
    ContestSyncState.objects.filter(pk=row.sync_id,token=row.token,status='pending').update(progressed_at=now)


def pulse_loop(task_id, stop):
    while not stop.wait(20):
        try:
            close_old_connections()
            pulse(task_id)
        except Exception:logger.exception('Contest execution heartbeat failed')
        finally:connections.close_all()


@task_prerun.connect
def running(task_id=None, task=None, args=(), kwargs=None, **extra):
    if not task_id or task.name not in TRACKED:return
    try:
        from .models import ContestTaskRun
        register(task_id,task.name,args,kwargs or {})
        now = timezone.now()
        changed = ContestTaskRun.objects.filter(task_id=task_id,status__in=['queued','reserved']).update(
            status='running',worker_epoch=os.environ.get('CLUB_CONTEST_WORKER_EPOCH',''),started_at=now,heartbeat_at=now)
        if changed:
            pulse(task_id)
            stop=threading.Event()
            _heartbeats[task_id]=stop
            threading.Thread(target=pulse_loop,args=(task_id,stop),daemon=True).start()
    except Exception:logger.exception('Unable to start contest tracking')


@task_postrun.connect
def finished(task_id=None, task=None, state=None, **kwargs):
    if not task_id or task.name not in TRACKED:return
    stop=_heartbeats.pop(task_id,None)
    if stop:stop.set()
    try:
        from .models import ContestTaskRun
        row=ContestTaskRun.objects.filter(task_id=task_id).first()
        if not row:return
        if state == 'FAILURE':
            from .tasks import fail_contest_sync
            fail_contest_sync(row.sync.user_id,row.token,'比赛后台任务执行中断，已有进度已保留')
        ContestTaskRun.objects.filter(task_id=task_id).exclude(status='interrupted').update(status='finished',finished_at=timezone.now())
    except Exception:logger.exception('Unable to finish contest tracking')


@task_failure.connect
def execution_failed(sender=None, task_id=None, **kwargs):
    # Hard timeouts/worker-child loss may be reported by the parent process.
    if sender and sender.name in TRACKED:
        finished(task_id=task_id,task=sender,state='FAILURE')


def recover_interrupted():
    from .models import ContestTaskRun
    from .tasks import _lock_contest_sync_state, finish_contest_sync
    now=timezone.now()
    recovered=0
    # A queue record is written before publication, so it cannot prove that the
    # broker still holds the message. Bound queue waiting separately from worker
    # death, and leave chains with recent execution progress alone.
    queue_cutoff = now - timedelta(hours=1)
    progress_cutoff = now - timedelta(minutes=30)
    queued = ContestTaskRun.objects.filter(status='queued', queued_at__lt=queue_cutoff)
    for candidate in queued.select_related('sync'):
        with transaction.atomic():
            sync = _lock_contest_sync_state(candidate.sync.user_id, candidate.token)
            if not sync:
                continue
            row = ContestTaskRun.objects.select_for_update().get(pk=candidate.pk)
            if row.status != 'queued' or row.queued_at >= queue_cutoff:
                continue
            progress = sync.progressed_at or sync.requested_at
            if progress and progress >= progress_cutoff:
                continue
            sync.has_errors = True
            sync.messages = {**sync.messages, 'system': '比赛任务排队超时，已有进度已保留'}
            finish_contest_sync(sync)
            sync.save()
            row.status = 'interrupted'
            row.finished_at = now
            row.save(update_fields=['status', 'finished_at'])
            recovered += 1
    candidates=ContestTaskRun.objects.filter(status__in=['running','reserved'],heartbeat_at__lt=now-timedelta(seconds=120)).exclude(worker_epoch='')
    for candidate in candidates.select_related('sync'):
        try:worker_status=caches['platform_requests'].get(worker_key(candidate.worker_epoch))
        except Exception:
            # An unavailable cache is not evidence that a worker has died.
            continue
        if worker_status == 'alive':
            ContestTaskRun.objects.filter(pk=candidate.pk).update(missing_since=None)
            continue
        # Require persistent absence on two separate checks, even after a restart.
        with transaction.atomic():
            sync=_lock_contest_sync_state(candidate.sync.user_id,candidate.token)
            if not sync:continue
            row=ContestTaskRun.objects.select_for_update().get(pk=candidate.pk)
            if row.status not in ('running','reserved') or not row.heartbeat_at or row.heartbeat_at >= now-timedelta(seconds=120):continue
            if not row.missing_since:
                row.missing_since=now;row.save(update_fields=['missing_since']);continue
            if row.missing_since > now-timedelta(seconds=60):continue
            sync.has_errors=True
            sync.messages={**sync.messages,'system':'检测到执行服务中断，已有进度已保留'}
            finish_contest_sync(sync)
            sync.save()
            row.status='interrupted';row.finished_at=now;row.save(update_fields=['status','finished_at'])
            recovered+=1
    ContestTaskRun.objects.exclude(status__in=['queued','reserved','running']).filter(finished_at__lt=now-timedelta(days=7)).delete()
    from django.db.models import F
    ContestTaskRun.objects.filter(queued_at__lt=now-timedelta(days=7)).exclude(token=F('sync__token')).delete()
    return recovered
