"""One reward ledger for contest and practice ACs."""
from types import SimpleNamespace

from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import (PlatformBinding, PlatformSolvedRecord, PlatformProblemClaim, ContestProblemClaim,
                     TeamMembership, TrackerCatalog, CurrencyTransaction, UserProfile)
from .reward_rules import group_for, amount_for, PLATFORMS, reward_groups
from .views import api_login_required, api_error, body, membership_for


def namespace(platform,account):
    return ('leetcode_cn' if account.startswith('cn:') else 'leetcode_us') if platform=='leetcode' else platform


def metadata(platform):
    kind='icpc' if platform=='qoj' else platform
    catalog=TrackerCatalog.objects.filter(kind=kind).only('id','updated_at').first()
    if not catalog:return {}
    from django.core.cache import cache
    key=f'platform-reward-metadata:{kind}:{catalog.updated_at}'
    result=cache.get(key)
    if result is not None:return result
    result={}
    for event in catalog.contests:
        for p in event['problems']:
            aliases=[p['key']]+p.get('aliases',[])
            canonical=next((s for s in aliases if platform=='nowcoder' and s.startswith('problem/')),p['key'])
            for alias in aliases:result[alias.lower()]={'key':canonical.lower(),'difficulty':p.get('difficulty')}
    cache.set(key,result,900)
    return result


def canonical(platform,key,info=None):
    info=metadata(platform) if info is None else info
    return info.get(key.lower(),{}).get('key',key.lower())


def contest_identity(problem):
    from .platform_solved import contest_key
    c=problem.participation
    return namespace(c.platform,c.account),canonical(c.platform,contest_key(problem))


def platform_claimed(problem,member):
    platform,key=contest_identity(problem)
    base=problem.participation.platform
    info=metadata(base)
    rows=PlatformProblemClaim.objects.filter(team=member.team,platform=platform).filter(
        Q(user_id=member.user_id)|Q(account=problem.participation.account.casefold()))
    return any(canonical(base,stored,info)==key for stored in rows.values_list('problem_key',flat=True))


def available_rewards(member,platform=None,binding_id=None):
    from .contest_rewards import REWARD_START
    if not member or not member.pk or member.user.is_superuser or not member.is_active or not member.team.currency_enabled:
        return {}
    bindings=PlatformBinding.objects.filter(user_id=member.user_id)
    if platform:bindings=bindings.filter(platform=platform)
    bound=list(bindings)
    enabled={b.platform for b in bound if any(amount_for(member.team,b.platform,g,0,'practice') or
        any(((member.team.reward_settings or {}).get(g,{}).get('practice_rewards',{}).get(b.platform,[])))
        for g in reward_groups())}
    if not enabled:return {}
    profile=UserProfile.objects.filter(user_id=member.user_id).first()
    if profile is None:return {}
    maps={p:metadata(p) for p in enabled}
    grouped={}
    for record in PlatformSolvedRecord.objects.filter(binding__in=bindings,binding__platform__in=enabled).select_related('binding').order_by('pk'):
        b=record.binding;key=canonical(b.platform,record.problem_key,maps[b.platform]);ns=namespace(b.platform,b.account)
        grouped.setdefault((ns,key),[]).append(record)
    paid=set();accounts_paid=set()
    accounts=[b.account.casefold() for b in bound]
    relevant=Q(user_id=member.user_id)|Q(account__in=accounts)
    for row in PlatformProblemClaim.objects.filter(team_id=member.team_id).filter(relevant):
        base='leetcode' if row.platform.startswith('leetcode_') else row.platform
        key=canonical(base,row.problem_key,maps.get(base,{}))
        if row.user_id==member.user_id:paid.add((row.platform,key))
        accounts_paid.add((row.platform,key,row.account))
    from .platform_solved import contest_key
    for old in ContestProblemClaim.objects.filter(team_id=member.team_id,platform__in=enabled).filter(relevant|Q(member_id=member.pk)):
        participation=SimpleNamespace(platform=old.platform,contest_key=old.contest_key)
        proxy=SimpleNamespace(participation=participation,problem_key=old.problem_key,url='')
        key=canonical(old.platform,contest_key(proxy),maps[old.platform]);ns=namespace(old.platform,old.account)
        if old.user_id==member.user_id or old.member_id==member.pk:paid.add((ns,key))
        accounts_paid.add((ns,key,old.account.casefold()))
    result={};today=timezone.localdate()
    for (ns,key),records in grouped.items():
        if (ns,key) in paid:continue
        dated=[r.solved_on for r in records if r.solved_on]
        day=min(dated) if dated else None
        if day and (day<REWARD_START.date() or day>today):continue
        candidates=[r for r in records if (not binding_id or r.binding_id==binding_id) and (ns,key,r.binding.account.casefold()) not in accounts_paid]
        if not candidates:continue
        row=candidates[0];b=row.binding
        group=group_for(member.user,day,profile)
        if group is None:continue
        difficulty=maps[b.platform].get(row.problem_key,{}).get('difficulty')
        amount=amount_for(member.team,b.platform,group,difficulty,'practice',day=day)
        if amount:
            result[ns+'|'+key]={'platform':ns,'problem_key':key,'account':b.account.casefold(),
                'amount':amount,'group':group,'title':row.title or key,'binding_id':b.pk}
    return result


@transaction.atomic
def claim_rewards(member,keys=None,platform=None,binding_id=None):
    from .currency_services import add_currency
    User.objects.select_for_update().get(pk=member.user_id)
    member=TeamMembership.objects.select_for_update(of=('self',)).select_related('team','user').get(pk=member.pk)
    if not member.is_active:raise ValueError('当前成员权限已失效')
    available=available_rewards(member,platform,binding_id)
    if keys is not None:available={key:value for key,value in available.items() if key in keys}
    claims=[]
    for key,value in available.items():
        try:
            with transaction.atomic():
                PlatformProblemClaim.objects.create(team_id=member.team_id,user_id=member.user_id,member=member,
                    platform=value['platform'],account=value['account'],problem_key=value['problem_key'],
                    amount=value['amount'],group=value['group'],source='practice')
        except IntegrityError:continue
        add_currency(member,value['amount'],CurrencyTransaction.CONTEST_REWARD,
                     f"{PLATFORMS.get(value['platform'].split('_')[0],value['platform'])} · {value['title'][:100]} 过题奖励")
        claims.append({'key':key,'amount':value['amount']})
    return {'claims':claims,'total':sum(row['amount'] for row in claims)}


@api_login_required
@require_http_methods(['POST'])
def claim(request):
    member=membership_for(request)
    if not member or not member.pk or request.user.is_superuser:return api_error('仅团队成员可以领奖',403)
    data=body(request);keys=data.get('keys');platform=data.get('platform');binding_id=data.get('account')
    if platform is not None and platform not in PLATFORMS:return api_error('平台不正确',400)
    if keys is not None and (not isinstance(keys,list) or not keys or len(keys)>5000 or any(not isinstance(k,str) or len(k)>240 for k in keys)):
        return api_error('请选择题目',400)
    if binding_id is not None and (type(binding_id) is not int or not PlatformBinding.objects.filter(pk=binding_id,user=request.user).exists()):
        return api_error('账号不存在',404)
    try:return JsonResponse(claim_rewards(member,keys,platform,binding_id))
    except ValueError as exc:return api_error(str(exc),400)
