import uuid
from datetime import timedelta
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from .views import api_login_required, api_error, membership_for, body
from .models import ContestParticipation, ContestSyncState, PlatformBinding, ContestProblemClaim, UserProfile
from .contest_rewards import serialize_participation, claim_problems
from .profile_views import lock_active_member


def enqueue(user_id, token, contest_url=None):
    from .tasks import discover_contest_progress
    try:
        discover_contest_progress.delay(user_id, token, contest_url)
    except Exception:
        ContestSyncState.objects.filter(user_id=user_id, token=token).update(
            status='failed', messages={'system':'同步服务暂不可用，请稍后重试'})


@api_login_required
@require_http_methods(['GET', 'POST'])
def progress(request):
    member = membership_for(request)
    if not member or not member.pk or request.user.is_superuser:
        return api_error('比赛进度仅供团队成员使用', 403)
    if request.method == 'POST':
        contest_url = body(request).get('contest_url') if request.body else None
        if contest_url is not None:
            from .contest_progress_sources import parse_contest_url
            try: platform, _, _ = parse_contest_url(contest_url)
            except ValueError as exc: return api_error(str(exc),400)
            if not PlatformBinding.objects.filter(user=request.user,platform=platform).exists():
                return api_error('请先绑定该平台账号',400)
        with transaction.atomic():
            if not lock_active_member(request.user.pk,member.pk):
                return api_error('账号或团队身份已失效，不能同步比赛历史',403)
            state, _ = ContestSyncState.objects.select_for_update().get_or_create(user=request.user)
            if state.status == 'pending' and state.requested_at and (state.progressed_at or state.requested_at) > timezone.now()-timedelta(minutes=30):
                if contest_url:
                    return api_error('当前正在同步，本次链接尚未受理，请完成后重试',409)
                return JsonResponse({'status': 'pending'}, status=202)
            if not PlatformBinding.objects.filter(user=request.user).exists():
                return api_error('请先在个人信息页绑定竞赛平台账号', 400)
            state.token = uuid.uuid4(); state.status = 'pending'; state.requested_at = state.progressed_at = timezone.now()
            state.messages = {}; state.remaining = 0; state.total = 0; state.save()
            token = str(state.token)
            transaction.on_commit(lambda: enqueue(request.user.pk, token, contest_url))
        return JsonResponse({'status': 'pending'}, status=202)
    state = ContestSyncState.objects.filter(user=request.user).first()
    rows = ContestParticipation.objects.filter(user=request.user).prefetch_related('problems').order_by('-starts_at')
    status = state.status if state else 'idle'
    last_progress=(state.progressed_at or state.requested_at) if state else None
    if state and status == 'pending' and (not last_progress or last_progress < timezone.now()-timedelta(minutes=30)):
        status = 'failed'
    context = {'profile': UserProfile.objects.filter(user=request.user).first(),
               'bindings': set(PlatformBinding.objects.filter(user=request.user).values_list('platform','account'))}
    return JsonResponse({'contests': [serialize_participation(c, member, context) for c in rows],
                         'status': status, 'messages': state.messages if state else {},
                         'auto_checked_on': state.auto_checked_on.isoformat() if state and state.auto_checked_on else None,
                         'auto_finished_at': state.auto_finished_at.isoformat() if state and state.auto_finished_at else None,
                         'remaining': state.remaining if state else 0, 'total': state.total if state else 0})


@api_login_required
@require_http_methods(['POST'])
def claim(request):
    member = membership_for(request)
    if not member or not member.pk or request.user.is_superuser:
        return api_error('仅团队成员可以领取奖励', 403)
    data = body(request)
    ids = data.get('ids')
    if ids is not None and (not isinstance(ids, list) or not ids or len(ids) > 5000 or any(type(i) is not int for i in ids)):
        return api_error('题目编号无效', 400)
    try:
        return JsonResponse(claim_problems(member, ids))
    except ValueError as exc:
        return api_error(str(exc),400)
