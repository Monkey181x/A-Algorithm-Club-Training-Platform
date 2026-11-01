"""Lifetime solved totals: durable contest ACs + archived/live approved problems."""
from collections import defaultdict
from django.utils import timezone
from django.db import transaction
from .models import SolvedProblemCredit, DatedSolvedProblemCredit, Submission, SubmissionPart, PlatformBinding


def archive_submission_credits(semester):
    rows = Submission.objects.filter(semester=semester, is_valid=True, problem__isnull=False,
        parts__kind=SubmissionPart.PROOF, parts__status=SubmissionPart.APPROVED
    ).values_list('member__user_id', 'problem__identity').distinct()
    SolvedProblemCredit.objects.bulk_create([
        SolvedProblemCredit(user_id=uid, source='submission', problem_key=key) for uid, key in rows
    ], ignore_conflicts=True)
    dated = Submission.objects.filter(semester=semester, is_valid=True, problem__isnull=False,
        parts__kind=SubmissionPart.PROOF, parts__status=SubmissionPart.APPROVED
    ).values_list('member__user_id', 'problem__identity', 'record_date').distinct()
    DatedSolvedProblemCredit.objects.bulk_create([
        DatedSolvedProblemCredit(user_id=uid, source='submission', problem_key=key, solved_on=day)
        for uid, key, day in dated
    ], ignore_conflicts=True, batch_size=500)


@transaction.atomic
def remember_contest_ac(problem):
    contest = problem.participation
    if not problem.accepted or contest.source_data.get('demo'):
        return
    if not PlatformBinding.objects.filter(user_id=contest.user_id, platform=contest.platform, account=contest.account).exists():
        return
    SolvedProblemCredit.objects.get_or_create(user_id=contest.user_id, source='contest',
        problem_key=f'{contest.platform}:{contest.contest_key}:{problem.problem_key}')
    key = f'{contest.platform}:{contest.contest_key}:{problem.problem_key}'
    day = timezone.localdate(problem.accepted_at or contest.starts_at)
    # A later verified timestamp supersedes the earlier contest-date fallback.
    DatedSolvedProblemCredit.objects.filter(user_id=contest.user_id, source='contest',
        problem_key=key).exclude(solved_on=day).delete()
    DatedSolvedProblemCredit.objects.update_or_create(user_id=contest.user_id, source='contest',
        problem_key=key, solved_on=day, defaults={'time_verified':problem.accepted_at is not None})


def solved_evidence(user_ids):
    """One ledger for total, semester and heatmap; unknown dates remain numeric only."""
    from datetime import date
    from .models import PlatformSolvedBaseline, ContestProblemProgress
    from .platform_solved import contest_key, problem_url
    today = timezone.localdate()
    items = defaultdict(dict)
    unknown = defaultdict(int)
    baseline_unknown = {}
    baselines = {(b.binding.user_id,b.binding.platform):b for b in
        PlatformSolvedBaseline.objects.filter(binding__user_id__in=user_ids,total__isnull=False,
            captured_at__isnull=False).select_related('binding')}
    def covered(uid,source,key):
        return source=='contest' and (uid,key.split(':',1)[0]) in baselines
    def add(uid,key,day=None,**meta):
        if day and day>today:
            return
        previous=items[uid].get(key)
        if previous is None or day and (previous['date'] is None or day<=previous['date']):
            items[uid][key]={'date':day,**meta}
    for uid,source,key in SolvedProblemCredit.objects.filter(user_id__in=user_ids).values_list('user_id','source','problem_key'):
        if not covered(uid,source,key):
            add(uid,(source,key))
    for uid,source,key,day in DatedSolvedProblemCredit.objects.filter(user_id__in=user_ids,
            time_verified=True).values_list('user_id','source','problem_key','solved_on'):
        if not covered(uid,source,key):
            add(uid,(source,key),day,title=key,url='',source='归档过题记录')
    proofs=Submission.objects.filter(member__user_id__in=user_ids,is_valid=True,problem__isnull=False,
        parts__kind=SubmissionPart.PROOF,parts__status=SubmissionPart.APPROVED).select_related('member','problem')
    for row in proofs:
        add(row.member.user_id,('submission',row.problem.identity),row.record_date,
            title=row.problem.title or row.problem.url,url=row.problem.url,source='本站通过记录')
    for (uid,platform),base in baselines.items():
        dated={}
        for key,value in base.dated_problems.items():
            try:
                day=date.fromisoformat(value)
            except (ValueError,TypeError):
                continue
            if day<=today:
                dated[key]=day
        unknown[uid]+=max(0,base.total-len(dated))
        baseline_unknown[(uid,platform)]=max(0,base.total-len(dated))
        for key,day in dated.items():
            add(uid,('platform',platform+':'+key),day,title=key,
                url=problem_url(platform,key,base.binding.account),source=platform+' · 绑定时过题')
    bindings=set(PlatformBinding.objects.filter(user_id__in=user_ids).values_list('user_id','platform','account'))
    for row in ContestProblemProgress.objects.filter(participation__user_id__in=user_ids,accepted=True).select_related('participation'):
        c=row.participation
        if c.source_data.get('demo') or (c.user_id,c.platform,c.account) not in bindings:
            continue
        base=baselines.get((c.user_id,c.platform))
        day=timezone.localdate(row.accepted_at) if row.accepted_at else None
        if base:
            key=contest_key(row)
            identity=('platform',c.platform+':'+key)
            if row.accepted_at and row.accepted_at<=base.captured_at:
                # An old contest may reveal the date of a baseline solve, but never adds to its total.
                if identity not in items[c.user_id] and baseline_unknown[(c.user_id,c.platform)]>0 and (
                        not base.ids_complete or key in base.problem_ids):
                    baseline_unknown[(c.user_id,c.platform)]-=1
                    unknown[c.user_id]-=1
                    add(c.user_id,identity,day,title=row.title or row.label,url=row.url,source=c.platform+' · 基线日期补全')
                continue
            # Old contests discovered after binding are already part of the baseline.
            if not row.accepted_at or row.accepted_at<=base.captured_at or key in base.problem_ids:
                continue
            # With aggregate-only baselines, old contests might repeat previously solved tasks.
            if not base.ids_complete and c.starts_at<=base.captured_at:
                continue
        else:
            identity=('contest',f'{c.platform}:{c.contest_key}:{row.problem_key}')
        add(c.user_id,identity,day,title=row.title or row.label,url=row.url,source=c.platform+' · 比赛 AC')
    return {uid:{'items':list(items[uid].values()),'undated':unknown[uid]+sum(v['date'] is None for v in items[uid].values()),
                 'total':unknown[uid]+len(items[uid])} for uid in user_ids}


def lifetime_solved_counts(user_ids):
    return {uid:data['total'] for uid,data in solved_evidence(user_ids).items()}


def semester_solved_counts(user_ids, semester):
    if not semester:
        return {}
    end=min(semester.ends_on,timezone.localdate())
    return {uid:sum(item['date'] is not None and semester.starts_on<=item['date']<=end
                    for item in data['items']) for uid,data in solved_evidence(user_ids).items()}
