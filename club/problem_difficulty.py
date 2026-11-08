"""One difficulty value/provenance for Tracker and unclaimed rewards.

Normalization only applies to raw upstream values, never to already converted
values. QOJ deliberately remains outside this service.
"""
import math
from django.utils import timezone

SOURCES = {'codeforces': 'Codeforces rating', 'atcoder': 'AtCoder Problems',
           'nowcoder': '牛客题目难度', 'luogu': '洛谷难度等级', 'leetcode': 'zerotrac difficulty rating'}


def normalize(platform, raw):
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(value):
        return None
    if platform == 'atcoder' and value < 400:
        return 400 * math.exp((value - 400) / 400)
    if platform == 'nowcoder':
        value = {1:800,2:1200,3:1600,4:2000,5:2400,6:2800,7:3000,8:3200,9:3400,10:3500}.get(value, value)
    return value if value >= 0 else None


def publish(platform, rows):
    """Rows contain normalized values, identity aliases and source attribution."""
    if platform == 'qoj':
        return
    from .models import SharedProblemDifficulty
    now = timezone.now()
    values = {}
    for row in rows:
        value = row.get('difficulty')
        if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
            continue  # Missing upstream ratings must not erase known ratings.
        for key in [row['key']] + row.get('aliases', []):
            key = key.lower()
            values[key] = SharedProblemDifficulty(platform=platform, problem_key=key, value=value,
                source=row.get('difficulty_source') or SOURCES.get(platform, ''), updated_at=now)
    if values:
        SharedProblemDifficulty.objects.bulk_create(list(values.values()), update_conflicts=True,
            unique_fields=['platform', 'problem_key'], update_fields=['value', 'source', 'updated_at'], batch_size=1000)


def shared_map(platform):
    if platform == 'qoj':
        return {}
    from .models import SharedProblemDifficulty
    return {r.problem_key: {'difficulty': r.value, 'difficulty_source': r.source,
            'difficulty_updated_at': r.updated_at.isoformat(), 'difficulty_known': r.value is not None}
            for r in SharedProblemDifficulty.objects.filter(platform=platform)}


def for_problem(problem, info=None):
    """Return common metadata, retaining a verified legacy value until refreshed."""
    from .models import ContestProblemProgress
    fallback = {'difficulty': problem.difficulty, 'difficulty_source': getattr(problem, 'difficulty_source', ''),
                'difficulty_updated_at': None, 'difficulty_known': problem.difficulty is not None}
    if not isinstance(problem, ContestProblemProgress) or problem.participation.platform == 'qoj':
        return fallback
    from .platform_solved import contest_key
    if info is None:
        from .ac_evidence import metadata
        info = metadata(problem.participation.platform)
    row = info.get(contest_key(problem))
    if row is None or row.get('difficulty') is None:
        return fallback
    return {field:row.get(field, fallback[field]) for field in fallback}
