import uuid
from datetime import timedelta
from unittest.mock import patch
from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase
from .test_contest_nightly import NightlyContestTests
from .models import ContestSyncState, ContestTaskRun
from .contest_task_monitor import recover_interrupted, worker_key, register, pulse, execution_failed
from .tasks import check_contests_nightly


class ContestTaskMonitorTests(TestCase):
    def setUp(self):
        NightlyContestTests.setUp(self)
        self.gate=LocMemCache('contest-monitor-tests',{})
        self.gate.clear()
        self.cache_patch=patch('club.contest_task_monitor.caches',{'platform_requests':self.gate})
        self.cache_patch.start();self.addCleanup(self.cache_patch.stop)
        check_contests_nightly()
        self.state=ContestSyncState.objects.get(user=self.author)

    def run_record(self,status='running',epoch='old-worker'):
        return ContestTaskRun.objects.create(task_id=str(uuid.uuid4()),sync=self.state,token=self.state.token,
            task_name='club.tasks.discover_contest_progress',status=status,worker_epoch=epoch,
            started_at=self.now-timedelta(minutes=4),heartbeat_at=self.now-timedelta(minutes=3))

    def test_missing_worker_requires_two_checks(self):
        row=self.run_record()
        self.assertEqual(recover_interrupted(),0)
        self.state.refresh_from_db();self.assertEqual(self.state.status,'pending')
        self.clock_mock.return_value=self.now+timedelta(seconds=61)
        self.assertEqual(recover_interrupted(),1)
        self.state.refresh_from_db();row.refresh_from_db()
        self.assertEqual(self.state.status,'failed')
        self.assertEqual(self.state.auto_attempts,1)
        self.assertIsNotNone(self.state.auto_retry_at)
        self.assertEqual(row.status,'interrupted')

    def test_queued_task_is_not_interrupted_by_legacy_timeout(self):
        self.run_record('queued')
        ContestSyncState.objects.filter(pk=self.state.pk).update(progressed_at=self.now-timedelta(hours=1),requested_at=self.now-timedelta(hours=1))
        self.assertEqual(recover_interrupted(),0)
        self.assertEqual(check_contests_nightly()['status'],'waiting')
        self.state.refresh_from_db();self.assertEqual(self.state.status,'pending')

    def test_cache_outage_is_not_worker_death(self):
        self.run_record()
        with patch.object(self.gate,'get',side_effect=ConnectionError()):
            self.assertEqual(recover_interrupted(),0)
        self.assertFalse(ContestTaskRun.objects.exclude(missing_since=None).exists())

    def expired_queue(self):
        row = self.run_record('queued', epoch='')
        old = self.now - timedelta(hours=2)
        ContestTaskRun.objects.filter(pk=row.pk).update(queued_at=old)
        ContestSyncState.objects.filter(pk=self.state.pk).update(requested_at=old, progressed_at=old)
        return row

    def test_lost_queued_message_retries_and_late_delivery_is_ignored(self):
        from .tasks import discover_contest_progress
        row = self.expired_queue()
        old_token = self.state.token
        self.assertEqual(recover_interrupted(), 1)
        self.state.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual((self.state.status, row.status), ('failed', 'interrupted'))
        self.assertEqual(self.state.auto_attempts, 1)
        self.clock_mock.return_value = self.state.auto_retry_at
        self.assertEqual(check_contests_nightly()['status'], 'started')
        self.state.refresh_from_db()
        self.assertEqual(self.state.auto_attempts, 2)
        with patch('club.contest_progress_sources.discover') as fetch:
            discover_contest_progress(self.author.pk, str(old_token))
        fetch.assert_not_called()
        self.state.refresh_from_db()
        self.assertEqual(self.state.status, 'pending')

    def test_queue_timeout_preserves_daily_cap(self):
        self.expired_queue()
        ContestSyncState.objects.filter(pk=self.state.pk).update(auto_attempts=3)
        self.assertEqual(recover_interrupted(), 1)
        self.state.refresh_from_db()
        self.assertIsNone(self.state.auto_retry_at)
        self.assertEqual(check_contests_nightly()['status'], 'complete')

    def test_old_queue_does_not_interrupt_progress_or_new_token(self):
        self.expired_queue()
        ContestSyncState.objects.filter(pk=self.state.pk).update(progressed_at=self.now)
        self.assertEqual(recover_interrupted(), 0)
        ContestSyncState.objects.filter(pk=self.state.pk).update(
            progressed_at=self.now-timedelta(hours=2), token=uuid.uuid4())
        self.assertEqual(recover_interrupted(), 0)

    def test_queue_recovery_rechecks_received_state_under_lock(self):
        from .tasks import _lock_contest_sync_state
        row = self.expired_queue()
        def receive_then_lock(*args):
            ContestTaskRun.objects.filter(pk=row.pk).update(status='reserved', heartbeat_at=self.now)
            return _lock_contest_sync_state(*args)
        with patch('club.tasks._lock_contest_sync_state', side_effect=receive_then_lock):
            self.assertEqual(recover_interrupted(), 0)
        self.state.refresh_from_db()
        self.assertEqual(self.state.status, 'pending')

    def test_reserved_task_survives_live_worker_but_recovers_dead_worker(self):
        row=self.run_record('reserved')
        self.gate.set(worker_key('old-worker'),'alive')
        self.assertEqual(recover_interrupted(),0)
        self.gate.clear()
        self.assertEqual(recover_interrupted(),0)
        self.clock_mock.return_value=self.now+timedelta(minutes=2)
        self.assertEqual(recover_interrupted(),1)
        row.refresh_from_db();self.assertEqual(row.status,'interrupted')

    def test_late_heartbeat_cancels_recovery(self):
        row=self.run_record()
        recover_interrupted()
        self.clock_mock.return_value=self.now+timedelta(minutes=2)
        pulse(row.pk)
        self.assertEqual(recover_interrupted(),0)
        self.state.refresh_from_db();self.assertEqual(self.state.status,'pending')

    def test_live_worker_and_fresh_task_are_not_interrupted(self):
        row=self.run_record()
        self.gate.set(worker_key('old-worker'),'alive')
        self.assertEqual(recover_interrupted(),0)
        self.gate.clear()
        pulse(row.pk)
        self.assertEqual(recover_interrupted(),0)

    def test_worker_reappearing_resets_missing_observation(self):
        row=self.run_record()
        recover_interrupted()
        self.gate.set(worker_key('old-worker'),'alive')
        self.clock_mock.return_value=self.now+timedelta(minutes=1)
        self.assertEqual(recover_interrupted(),0)
        row.refresh_from_db();self.assertIsNone(row.missing_since)

    def test_new_token_cannot_be_failed_by_old_run(self):
        self.run_record()
        recover_interrupted()
        self.state.token=uuid.uuid4();self.state.save()
        self.clock_mock.return_value=self.now+timedelta(minutes=2)
        self.assertEqual(recover_interrupted(),0)
        self.state.refresh_from_db();self.assertEqual(self.state.status,'pending')

    def test_restarted_worker_does_not_revive_old_epoch_and_cap_applies(self):
        self.state.auto_attempts=3;self.state.save()
        self.run_record()
        self.gate.set(worker_key('new-worker'),'alive')
        recover_interrupted()
        self.clock_mock.return_value=self.now+timedelta(minutes=2)
        self.assertEqual(recover_interrupted(),1)
        self.state.refresh_from_db()
        self.assertIsNone(self.state.auto_retry_at)
        self.assertEqual(self.state.auto_attempts,3)

    def test_publication_is_queued_and_old_token_is_ignored(self):
        register('queued-id','club.tasks.discover_contest_progress',[self.author.pk,str(self.state.token)],{})
        register('old-id','club.tasks.discover_contest_progress',[self.author.pk,str(uuid.uuid4())],{})
        self.assertEqual(ContestTaskRun.objects.get(pk='queued-id').status,'queued')
        self.assertFalse(ContestTaskRun.objects.filter(pk='old-id').exists())

    def test_hard_failure_enters_bounded_retry(self):
        from .tasks import discover_contest_progress
        row=self.run_record()
        execution_failed(sender=discover_contest_progress,task_id=row.pk)
        self.state.refresh_from_db()
        self.assertEqual(self.state.status,'failed')
        self.assertIsNotNone(self.state.auto_retry_at)
