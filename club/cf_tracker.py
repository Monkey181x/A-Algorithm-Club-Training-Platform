"""Complete Codeforces Tracker rosters from the standings of finished rounds."""
import re
from collections import Counter, defaultdict
from django.utils import timezone

from .contest_progress_sources import cf
from .platform_sync import SyncError
from .tracker_sources import problem


def retain_roster(event, previous, ratings):
    """A partial problemset snapshot can refresh ratings on a verified roster."""
    if previous.get('roster_verified') or previous.get('roster_needs_check'):
        known = {p['key'] for p in previous['problems']}
        added = [p for p in event['problems'] if p['key'] not in known]
        retained = []
        for p in previous['problems']:
            key = p.get('canonical_key', p['key'])
            difficulty = ratings.get(key, ratings.get(p['key'], p['difficulty']))
            retained.append(dict(p, difficulty=difficulty))
        event['problems'] = retained + added
        if added or previous.get('roster_needs_check'):
            event['roster_needs_check'] = True
        else:
            event['roster_verified'] = True
    for field in ('roster_retry_at', 'roster_checked_at', 'roster_recheck_at', 'paired_with'):
        if field in previous:
            event[field] = previous[field]


def round_groups(events):
    """Pair only opposite divisions of the same round with the same start."""
    rounds = defaultdict(list)
    singles = []
    for event in events:
        division = event['category']
        if division in ('Div. 1', 'Div. 2') and event['starts_at']:
            title = event['title'].casefold()
            number = re.search(r'\bround\s*#?\s*(\d+)\b', title)
            family = number[1] if number else title.replace(division.casefold(), 'div.')
            rounds[event['starts_at'], family].append(event)
        else:
            singles.append([event])
    groups = []
    for siblings in rounds.values():
        if len(siblings) == 2 and {e['category'] for e in siblings} == {'Div. 1', 'Div. 2'}:
            groups.append(sorted(siblings, key=lambda e: e['category']))
        else:
            singles.extend([e] for e in siblings)
    return groups + singles


def pair_confirmed(group):
    first, second = group
    return (first.get('roster_verified') and second.get('roster_verified')
            and first.get('paired_with') == second['id'] and second.get('paired_with') == first['id'])


def roster_queue(events, now):
    def due(group):
        if len(group) == 2 and not pair_confirmed(group):
            return True
        return any(e.get('roster_retry_at') or not e.get('roster_checked_at') or not e.get('roster_verified')
                   or 0 < e.get('roster_recheck_at', 0) <= now for e in group)

    pending = [g for g in round_groups(events) if due(g)
               and all(e.get('roster_retry_at', 0) <= now for e in g)]
    pending.sort(key=lambda g: (g[0]['starts_at'] > now - 7 * 86400, len(g), g[0]['starts_at']), reverse=True)
    # Bound each scheduled refresh; completed groups drop out of subsequent runs.
    return [[e['id'] for e in group] for group in pending[:32]]


def verified_rosters(remote, group):
    events = []
    now = timezone.now().timestamp()
    for event in group:
        cid = event['id']
        data = cf(remote, 'contest.standings', f'contestId={cid}')
        rows = data.get('problems') if isinstance(data, dict) else None
        header = data.get('contest') if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows or not isinstance(header, dict) or str(header.get('id')) != cid:
            raise SyncError('Codeforces 比赛题单不完整')
        previous = {p['key']: p for p in event['problems']}
        seen = set()
        problems = []
        for row in rows:
            if not isinstance(row, dict):
                raise SyncError('Codeforces 比赛题目格式异常')
            label, title = row.get('index'), row.get('name')
            if (not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9]+', label)
                    or label.casefold() in seen or not isinstance(title, str) or not title.strip()):
                raise SyncError('Codeforces 比赛题目格式异常')
            seen.add(label.casefold())
            key = f'{cid}:{label}'.lower()
            difficulty = row.get('rating')
            if difficulty is None:
                difficulty = previous.get(key, {}).get('difficulty')
            problems.append(problem(key, label, title, f"{event['url']}/problem/{label}", difficulty))
        if not previous.keys() <= {p['key'] for p in problems}:
            raise SyncError('Codeforces 榜单缺少已收录的题目')
        verified = dict(event, problems=problems, roster_verified=True, roster_checked_at=now)
        if not event.get('roster_checked_at') and event['starts_at'] > now - 7 * 86400:
            verified['roster_recheck_at'] = now + 86400
        elif verified.get('roster_recheck_at', 0) <= now:
            verified.pop('roster_recheck_at', None)
        verified.pop('paired_with', None)
        verified.pop('roster_retry_at', None)
        verified.pop('roster_needs_check', None)
        events.append(verified)
    if len(events) == 2:
        events[0]['paired_with'] = events[1]['id']
        events[1]['paired_with'] = events[0]['id']
        first, second = [e['problems'] for e in events]
        first_counts = Counter(p['title'] for p in first)
        second_counts = Counter(p['title'] for p in second)
        ambiguous = {title for title in first_counts.keys() & second_counts.keys()
                     if first_counts[title] != 1 or second_counts[title] != 1}
        for p in first + second:
            if p['title'] in ambiguous:
                p['identity_pending'] = True
        peers = {p['title']: p for p in first if p['title'] not in ambiguous}
        for p in second:
            peer = peers.get(p['title'])
            if peer:
                # Local keys and URLs stay division-specific. Div. 1 owns the ledger key.
                p['canonical_key'] = peer['key']
                if peer['difficulty'] is None:
                    peer['difficulty'] = p['difficulty']
                p['difficulty'] = peer['difficulty']
    for old, updated in zip(group, events):
        links = {p['key']: p.get('canonical_key') for p in updated['problems']}
        if any(p.get('canonical_key') and links.get(p['key']) != p['canonical_key'] for p in old['problems']):
            raise SyncError('Codeforces 共用题映射出现冲突，已保留上次题单')
    return events
