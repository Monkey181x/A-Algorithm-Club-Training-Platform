"""Personal training activity, using stored evidence only; never syncs platforms."""
from collections import defaultdict
from datetime import date, timedelta

from django.http import JsonResponse
from django.utils import timezone

from .models import (Submission, SubmissionPart, SolvedProblemCredit,
                     ContestProblemProgress, PlatformBinding, CheckinRecord, MakeupPermission)
from .views import api_login_required, api_error, membership_for, active_semester


@api_login_required
def training_activity(request):
    if request.method != 'GET':
        return api_error('仅支持 GET', 405)
    member = membership_for(request)
    if not member:
        return api_error('未加入当前团队', 403)
    today = timezone.localdate()
    try:
        start = date.fromisoformat(request.GET.get('start', today.replace(day=1).isoformat()))
        end = date.fromisoformat(request.GET.get('end', today.isoformat()))
        if start > end or (end - start).days > 399 or start.year < 2000 or end.year > 2200:
            raise ValueError
    except ValueError:
        return api_error('请选择有效日期范围（最多 400 天）')
    semester = active_semester(member.team)
    events, accepted = defaultdict(list), {}
    # Same identity/source convention as the existing lifetime solved ranking.
    # Repeated proof submissions and multiple teams never inflate AC totals.
    proofs = Submission.objects.filter(member__user=request.user, is_valid=True,
        problem__isnull=False, parts__kind=SubmissionPart.PROOF,
        parts__status=SubmissionPart.APPROVED).select_related('problem')
    for row in proofs:
        key = ('submission', row.problem.identity)
        old = accepted.get(key)
        if old is None or row.record_date < old['date']:
            accepted[key] = {'date': row.record_date, 'title': row.problem.title or row.problem.url,
                             'url': row.problem.url, 'source': '本站通过记录'}
    bindings = set(PlatformBinding.objects.filter(user=request.user).values_list('platform', 'account'))
    contests = ContestProblemProgress.objects.filter(participation__user=request.user,
        accepted=True).select_related('participation')
    for row in contests:
        p = row.participation
        if p.source_data.get('demo') or (p.platform, p.account) not in bindings:
            continue
        key = ('contest', f'{p.platform}:{p.contest_key}:{row.problem_key}')
        day = timezone.localdate(row.accepted_at) if row.accepted_at else None
        old = accepted.get(key)
        if old is None or day and (old['date'] is None or day < old['date']):
            accepted[key] = {'date': day, 'title': row.title or row.label,
                             'url': row.url, 'source': p.platform + ' · 比赛 AC'}
    for source, key in SolvedProblemCredit.objects.filter(user=request.user).values_list('source', 'problem_key'):
        accepted.setdefault((source, key), {'date': None})
    undated = sum(item['date'] is None for item in accepted.values())
    ac_by_date = defaultdict(int)
    for item in accepted.values():
        day = item['date']
        if day is not None and day <= today:
            ac_by_date[day] += 1
            if start <= day <= end:
                events[day].append(dict(item, date=day.isoformat(), kind='ac'))
    daily_by_date = defaultdict(int)
    # Daily mode is current-team valid training submissions, not check-in rewards.
    for row in Submission.objects.filter(member=member, is_valid=True,
        record_date__lte=today).select_related('problem'):
        daily_by_date[row.record_date] += 1
        if start <= row.record_date <= end:
            events[row.record_date].append({'date': row.record_date.isoformat(), 'kind': 'daily',
                'title': row.problem.title or row.problem.url if row.problem_id else '训练提交',
                'url': row.problem.url if row.problem_id else '', 'source': '本站有效提交'})
    checks = dict(CheckinRecord.objects.filter(member=member).values_list('checkin_date', 'is_makeup'))
    permissions = {p.record_date: p for p in MakeupPermission.objects.filter(member=member, semester=semester)} if semester else {}
    weeks = list(semester.weeks.all()) if semester else []
    days = []
    now = timezone.now()
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        week = next((w for w in weeks if timezone.localdate(w.starts_at) <= day < timezone.localdate(w.ends_at)), None)
        permission = permissions.get(day)
        completed = bool(checks.get(day) or permission and permission.completed_at)
        daily_count = daily_by_date.get(day, 0)
        submitted = daily_count > 0 or day in checks
        allowed = bool(member.pk and semester and week and semester.is_active and not semester.archived_at
            and semester.starts_on <= day <= min(today, semester.ends_on))
        if allowed:
            allowed = (not week.is_closed and week.starts_at <= now < week.ends_at) if day == today else not submitted and not completed
        if getattr(request,'admin_test_mode',False): allowed=day<=today and day.year>=2000
        days.append({'date': day.isoformat(), 'ac': ac_by_date.get(day, 0), 'daily': daily_count,
            'submitted': submitted, 'checkedIn': day in checks, 'hasSubmission': daily_count > 0,
            'isMakeup': completed, 'makeupOpen': bool(permission and not permission.completed_at),
            'canSubmit': allowed, 'weekId': week.pk if week else None, 'isToday': day == today,
            'records': events.get(day, [])})
    def summary(counts):
        cursor = today if counts.get(today) else today - timedelta(days=1)
        streak = 0
        while counts.get(cursor):
            streak += 1
            cursor -= timedelta(days=1)
        return {'rangeTotal': sum(counts.get(date.fromisoformat(d['date']), 0) for d in days),
                'activeDays': sum(counts.get(date.fromisoformat(d['date']), 0) > 0 for d in days), 'streak': streak}
    display_semester=active_semester(request.test_target_team) if getattr(request,'admin_test_mode',False) else semester
    response = JsonResponse({'today': today.isoformat(), 'start': start.isoformat(), 'end': end.isoformat(),
        'semester': {'name': display_semester.name, 'start': display_semester.starts_on.isoformat(), 'end': display_semester.ends_on.isoformat()} if display_semester else None,
        'days': days, 'ac': dict(summary(ac_by_date), total=len(accepted), undated=undated),
        'daily': dict(summary(daily_by_date), total=sum(daily_by_date.values()))})
    response['Cache-Control'] = 'no-store'
    return response
