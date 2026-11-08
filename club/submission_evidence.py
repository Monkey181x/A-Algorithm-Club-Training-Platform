"""Precise remote submissions, separate from first-AC aggregates and reward ledgers."""
from datetime import datetime, timedelta, timezone as utc
from django.utils import timezone
from .models import PlatformBinding, PlatformSubmissionEvidence, PlatformEvidenceCoverage


def normalize(platform, account, rows):
    from .platform_sync import SyncError
    result=[]
    valid=True
    for row in rows:
        if platform=='codeforces':
            if not row.get('author',{}).get('members'):
                valid=False;continue
            if not any(m.get('handle','').casefold()==account.casefold() for m in row.get('author',{}).get('members',[])):
                raise SyncError('提交证据账号不匹配')
            problem=row.get('problem') or {}
            contest=row.get('contestId') or problem.get('contestId')
            key=f"{problem.get('contestId',contest)}:{problem.get('index','')}".lower()
            stamp=row.get('creationTimeSeconds'); verdict=row.get('verdict','TESTING')
            source='codeforces-api'
        else:
            if not row.get('user_id'):
                valid=False;continue
            if str(row.get('user_id','')).casefold()!=account.casefold():
                raise SyncError('提交证据账号不匹配')
            contest=row.get('contest_id');key=row.get('problem_id','').lower()
            stamp=row.get('epoch_second');verdict=row.get('result','WJ');source='atcoder-problems'
        try:
            if not row.get('id') or not key or not contest or not stamp: raise ValueError()
            submitted=datetime.fromtimestamp(float(stamp),utc.utc)
            if submitted>timezone.now():raise ValueError()
        except (ValueError,TypeError,OverflowError,OSError):
            valid=False;continue
        from .contest_rewards import REWARD_START
        if submitted < REWARD_START: continue
        result.append(dict(submission_id=str(row['id']),problem_key=key,contest_key=str(contest),
            submitted_at=submitted.isoformat(),verdict=verdict,source=source))
    return result,valid


def store_page(binding, rows):
    account=binding.account.casefold()
    merged={row['submission_id']:row for row in rows}
    objects=[PlatformSubmissionEvidence(binding=binding,account=account,**row) for row in merged.values()]
    PlatformSubmissionEvidence.objects.bulk_create(objects,update_conflicts=True,
        unique_fields=['binding','account','submission_id'],
        update_fields=['problem_key','contest_key','submitted_at','verdict','source','observed_at'])


def finish_coverage(binding,state):
    if not state.get('evidence_valid') or not state.get('evidence_until'): return
    from .contest_rewards import REWARD_START
    start=max(REWARD_START.timestamp(), 0 if state.get('full') else max(0,state.get('cursor',0)-86400))
    # Allow indexing lag; the newest ten minutes never certify completeness.
    end=state['evidence_until']-600
    if end<=start:return
    PlatformEvidenceCoverage.objects.update_or_create(binding=binding,account=binding.account.casefold(),
        starts_at=datetime.fromtimestamp(start,utc.utc),ends_at=datetime.fromtimestamp(end,utc.utc))
    PlatformEvidenceCoverage.objects.filter(binding=binding,checked_at__lt=timezone.now()-timedelta(days=2)).delete()


def contest_rows(contest):
    """None means fetch remotely; [] is a completely checked empty interval."""
    if not getattr(contest,'user_id',None):return None
    binding=PlatformBinding.objects.filter(user_id=contest.user_id,platform=contest.platform,
        account=contest.account).first()
    if not binding:return None
    covered=PlatformEvidenceCoverage.objects.filter(binding=binding,account=binding.account.casefold(),
        starts_at__lte=contest.starts_at,ends_at__gte=contest.ends_at,
        checked_at__gte=timezone.now()-timedelta(minutes=30)).exists()
    if not covered:return None
    rows=list(PlatformSubmissionEvidence.objects.filter(binding=binding,account=binding.account.casefold(),
        contest_key=contest.contest_key,submitted_at__gte=contest.starts_at,submitted_at__lt=contest.ends_at))
    if any(r.verdict in {'TESTING','WJ','WR','Judging'} for r in rows):return None
    return rows


def discovery_snapshot(user_id,platform,account):
    """Reuse a recent complete post-cutoff history, still fetching its newest tail."""
    from .contest_rewards import REWARD_START
    if type(user_id) is not int:return [],None
    binding=PlatformBinding.objects.filter(user_id=user_id,platform=platform,account=account).first()
    if not binding:return [],None
    coverage=PlatformEvidenceCoverage.objects.filter(binding=binding,account=account.casefold(),
        starts_at__lte=REWARD_START,checked_at__gte=timezone.now()-timedelta(minutes=30)).order_by('-ends_at').first()
    if not coverage:return [],None
    rows=list(PlatformSubmissionEvidence.objects.filter(binding=binding,account=account.casefold(),
        submitted_at__gte=REWARD_START,submitted_at__lt=coverage.ends_at))
    if any(r.verdict in {'TESTING','WJ','WR','Judging'} for r in rows):return [],None
    return rows,int(coverage.ends_at.timestamp())
