"""Shared OJ identities and AC evidence queries; no scoring or reward policy.

Consumers select which evidence is eligible: aggregate baselines never prove a
reward, and a known solve date never substitutes for a contest timestamp.
"""
from django.db.models import Q
from .models import PlatformBinding, PlatformSolvedRecord, PlatformSolvedBaseline, ContestProblemProgress, TrackerCatalog


def namespace(platform,account):
    return ('leetcode_cn' if account.startswith('cn:') else 'leetcode_us') if platform=='leetcode' else platform


def metadata(platform):
    from .problem_difficulty import shared_map, normalize
    shared = shared_map(platform)
    kind='icpc' if platform=='qoj' else platform
    catalog=TrackerCatalog.objects.filter(kind=kind).only('id','updated_at').first()
    if not catalog:return {key:dict(value,key=key) for key,value in shared.items()}
    from django.core.cache import cache
    key=f'platform-reward-metadata:v2:{kind}:{catalog.updated_at.isoformat() if catalog.updated_at else "pending"}'
    result=cache.get(key)
    if result is not None:
        return merge_difficulties(result, shared)
    result={}
    for event in catalog.contests:
        for p in event['problems']:
            aliases=[p['key']]+p.get('aliases',[])
            canonical=next((s for s in aliases if platform=='nowcoder' and s.startswith('problem/')),p['key'])
            value = p.get('difficulty')
            if platform == 'nowcoder' and not p.get('difficulty_source'):
                value = normalize(platform, value)
            for alias in aliases:result[alias.lower()]={'key':canonical.lower(),'difficulty':value,
                'difficulty_source':p.get('difficulty_source',''), 'difficulty_updated_at':catalog.updated_at.isoformat() if catalog.updated_at else None,
                'difficulty_known':value is not None}
    cache.set(key,result,900)
    return merge_difficulties(result, shared)


def merge_difficulties(base, shared):
    result = {k:dict(v) for k,v in base.items()}
    latest = {}
    for alias, info in shared.items():
        key = result.get(alias, {}).get('key', alias)
        if key not in latest or info['difficulty_updated_at'] > latest[key]['difficulty_updated_at']:
            latest[key] = info
        result.setdefault(alias, {'key':key})
    for info in result.values():
        if info['key'] in latest:info.update(latest[info['key']])
    return result


def canonical(platform,key,info=None):
    info=metadata(platform) if info is None else info
    return info.get(key.lower(),{}).get('key',key.lower())


class ACEvidence:
    def __init__(self, user_ids, platform=None, binding_id=None):
        self.user_ids = list(user_ids)
        self.bindings = PlatformBinding.objects.filter(user_id__in=self.user_ids)
        if platform:
            self.bindings = self.bindings.filter(platform=platform)
        if binding_id:
            self.bindings = self.bindings.filter(pk=binding_id)

    def records(self):
        return PlatformSolvedRecord.objects.filter(binding__in=self.bindings).select_related('binding')

    def baselines(self):
        return PlatformSolvedBaseline.objects.filter(binding__in=self.bindings).select_related('binding')

    def contests(self, *, bound_only=True):
        rows = ContestProblemProgress.objects.filter(participation__user_id__in=self.user_ids, accepted=True).select_related('participation')
        if bound_only:
            accounts = Q(pk__in=[])
            for uid, platform, account in self.bindings.values_list('user_id','platform','account'):
                accounts |= Q(participation__user_id=uid, participation__platform=platform, participation__account=account)
            rows = rows.filter(accounts)
        return rows.exclude(participation__source_data__contains={'demo': True})

    def accepted_keys(self):
        from .platform_solved import contest_key
        keys = set()
        maps = {}
        def add(platform, key):
            if platform not in maps:
                maps[platform] = metadata(platform)
            keys.add(canonical(platform, key, maps[platform]))
        for row in self.records():
            add(row.binding.platform, row.problem_key)
        for base in self.baselines():
            for key in set(base.problem_ids) | set(base.dated_problems):
                add(base.binding.platform, key)
        for row in self.contests():
            add(row.participation.platform, contest_key(row))
        return keys
