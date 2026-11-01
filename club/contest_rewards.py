"""Verified contest progress and exactly-once team coin claims."""
from datetime import datetime
import math
from zoneinfo import ZoneInfo
from django.db import transaction, IntegrityError
from django.utils import timezone
from .grades import grade_on
from .models import (ContestProblemClaim, ContestProblemProgress, PlatformBinding,
                     TeamMembership, UserProfile, CurrencyTransaction, default_problem_rewards)

REWARD_START = datetime(2026, 11, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
THRESHOLDS = {
    'leetcode': {'beginner': (600,1200), 'experienced': (1000,1500)},
    'codeforces': {'beginner': (1000,1600), 'experienced': (1400,2000)},
    'atcoder': {'beginner': (400,1000), 'experienced': (800,1600)},
    'nowcoder': {'beginner': (1100,1600), 'experienced': (1600,2100)},
    'luogu': {'beginner': (2,4), 'experienced': (4,6)},
}


def difficulty_value(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value >= 0 else None


def reward_for(problem, member, profile=None):
    contest = problem.participation
    if contest.source_data.get('demo') is True:
        return 0, '', '演示记录不参与奖励'
    now = timezone.now()
    if not member.team.currency_enabled:
        return 0, '', '当前团队未启用 RBC'
    if contest.platform == 'qoj':
        from .reward_rules import group_for, amount_for
        if not problem.accepted:return 0, '', '尚无 AC 记录'
        if problem.accepted_at is not None and (problem.accepted_at<REWARD_START or problem.accepted_at>now):
            return 0, '', '当前记录不参与奖励'
        day=timezone.localdate(problem.accepted_at) if problem.accepted_at else timezone.localdate()
        group=group_for(member.user,day,profile)
        if group is None:return 0, '', '请完善年级资料'
        source='contest' if problem.official_ac and problem.accepted_at is not None and contest.starts_at<=problem.accepted_at<contest.ends_at else 'practice'
        amount=amount_for(member.team,'qoj',group,source=source,day=day)
        return amount,group,'' if amount else '当前奖励为 0'
    if contest.starts_at < REWARD_START:
        return 0, '', '2026-11-01 前的比赛不发放奖励'
    if contest.starts_at > now:
        return 0, '', '比赛尚未开始'
    timing_unknown = problem.accepted and problem.accepted_at is None
    if not contest.detail_complete and not timing_unknown:
        return 0, '', '比赛进度尚未完整核验'
    if not problem.accepted:
        return 0, '', '尚无可靠 AC 记录'
    if problem.accepted_at is not None and problem.accepted_at < REWARD_START:
        return 0, '', '2026-11-01 前的 AC 不发放奖励'
    # User policy: a proven AC with an unavailable timestamp is eligible.
    # Known non-contest/out-of-window timestamps never take this fallback.
    if problem.accepted_at is not None and not problem.official_ac:
        return 0, '', '已知通过记录不属于正式比赛'
    if problem.accepted_at is not None and (not contest.starts_at <= problem.accepted_at < contest.ends_at or problem.accepted_at > now):
        return 0, '', '通过时间不在正式比赛时段内'
    profile = profile or UserProfile.objects.filter(user=member.user).first()
    level = grade_on(profile, timezone.localtime(contest.starts_at).date())
    if level is None:
        return 0, '', '入学年份资料缺失，请联系系统管理员核对'
    from .reward_rules import amount_for, group_for
    day=timezone.localtime(contest.starts_at).date()
    group=group_for(None,day,profile)
    amount = amount_for(member.team,contest.platform,group,problem.difficulty,day=day,level=level)
    return amount, group, '' if amount else '当前档位奖励为 0'


def claimed(problem, member):
    contest = problem.participation
    from django.db.models import Q
    from .platform_rewards import platform_claimed
    return platform_claimed(problem,member) or ContestProblemClaim.objects.filter(team=member.team, platform=contest.platform,
        contest_key=contest.contest_key, problem_key=problem.problem_key).filter(
        Q(user=member.user) | Q(member=member) | Q(account=contest.account.casefold())).exists()


def serialize_participation(contest, member, context=None):
    bound = ((contest.platform, contest.account) in context['bindings']) if context is not None else PlatformBinding.objects.filter(user=member.user, platform=contest.platform, account=contest.account).exists()
    rows = []
    for p in contest.problems.all():
        amount, group, reason = reward_for(p, member, context['profile'] if context else None)
        taken = ((contest.platform, contest.contest_key, p.problem_key) in context['member_claims'] or (contest.platform, contest.account.casefold(), contest.contest_key, p.problem_key) in context['account_claims']) if context else claimed(p, member)
        if context and not taken:
            from .platform_rewards import platform_claimed
            taken=platform_claimed(p,member)
        if not bound:
            reason = '当前未绑定该参赛账号'
        rows.append({'id': p.pk, 'label': p.label, 'title': p.title, 'url': p.url,
            'accepted': p.accepted, 'official_ac': p.official_ac, 'timing_relaxed': bool(p.accepted and p.accepted_at is None), 'difficulty': difficulty_value(p.difficulty),
            'difficulty_source': p.difficulty_source, 'reward': amount, 'claimed': taken,
            'claimable': bool(amount and not taken and bound and not reason), 'reason': reason})
    return {'id': 'participation-' + str(contest.pk), 'platform': contest.platform,
        'is_demo': contest.source_data.get('demo') is True,
        'name': contest.title, 'start': contest.starts_at.timestamp(), 'end': contest.ends_at.timestamp(),
        'url': contest.url, 'account': contest.account, 'problems': rows,
        'sync_error': contest.sync_error, 'detail_complete': contest.detail_complete,
        'claimable_count': sum(p['claimable'] for p in rows)}


@transaction.atomic
def claim_problems(member, ids=None):
    from .currency_services import add_currency
    from django.contrib.auth.models import User
    User.objects.select_for_update().get(pk=member.user_id)
    # All balance operations for these awards lock membership first, then balance.
    member = TeamMembership.objects.select_for_update(of=('self',)).select_related('team','user').get(pk=member.pk)
    if not member.is_active:
        raise ValueError('当前成员权限已失效')
    qs = ContestProblemProgress.objects.select_for_update(of=('self',)).select_related('participation').filter(participation__user=member.user)
    if ids is not None:
        qs = qs.filter(pk__in=ids)
    result = []
    for p in qs.order_by('pk'):
        c = p.participation
        if not PlatformBinding.objects.filter(user=member.user, platform=c.platform, account=c.account).exists():
            continue
        amount, group, reason = reward_for(p, member)
        if not amount or reason or claimed(p, member):
            continue
        try:
            with transaction.atomic():
                from .models import PlatformProblemClaim
                from .platform_rewards import contest_identity
                platform,key=contest_identity(p)
                PlatformProblemClaim.objects.create(team=member.team,user=member.user,member=member,
                    platform=platform,account=c.account.casefold(),problem_key=key,amount=amount,group=group,source='contest')
                ContestProblemClaim.objects.create(member=member, user=member.user, team=member.team, platform=c.platform,
                    account=c.account.casefold(), contest_key=c.contest_key, problem_key=p.problem_key,
                    amount=amount, group=group)
        except IntegrityError:
            continue
        mode='AC 奖励（时间不可核验，宽松兜底）' if p.accepted_at is None else '赛内 AC 奖励'
        add_currency(member, amount, CurrencyTransaction.CONTEST_REWARD,
                     f'{c.title[:115]} · {p.label} {mode}')
        result.append({'id': p.pk, 'contest': c.title, 'label': p.label, 'amount': amount})
    return {'claims': result, 'total': sum(p['amount'] for p in result)}
