import math
import uuid
from datetime import timedelta

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .models import PlatformBinding, PlatformSolvedBaseline, PlatformSolvedRecord, TrackerCatalog, ContestProblemProgress, QojCatalogOverlay
from .tracker_sources import KINDS
from .views import api_login_required, api_error, membership_for


def accepted_keys(user,kind,binding_id=None):
    bindings=PlatformBinding.objects.filter(user=user,platform=KINDS[kind])
    if binding_id:bindings=bindings.filter(pk=binding_id)
    accounts=set(bindings.values_list('account',flat=True))
    keys=set(PlatformSolvedRecord.objects.filter(binding__in=bindings).values_list('problem_key',flat=True))
    for base in PlatformSolvedBaseline.objects.filter(binding__in=bindings):
        keys.update(base.problem_ids)
    from .platform_solved import contest_key
    for row in ContestProblemProgress.objects.filter(participation__user=user,participation__platform=KINDS[kind],participation__account__in=accounts,accepted=True).select_related('participation'):
        if not row.participation.source_data.get('demo'):
            keys.add(contest_key(row))
    return {key.lower() for key in keys}


def queue_refresh(kind,force=False):
    with transaction.atomic():
        catalog,_=TrackerCatalog.objects.get_or_create(kind=kind)
        catalog=TrackerCatalog.objects.select_for_update().get(pk=catalog.pk)
        now=timezone.now()
        if catalog.status=='pending' and catalog.requested_at and catalog.requested_at>now-timedelta(minutes=5):
            return catalog
        if not force and catalog.updated_at and catalog.updated_at>now-timedelta(hours=6):
            return catalog
        if catalog.requested_at and catalog.requested_at>now-timedelta(seconds=60):
            return catalog
        catalog.token=uuid.uuid4();catalog.status='pending';catalog.error='';catalog.requested_at=now
        catalog.refresh_state={};catalog.save()
        from .tasks import refresh_tracker_catalog
        def enqueue():
            try:refresh_tracker_catalog.delay(kind,str(catalog.token))
            except Exception:TrackerCatalog.objects.filter(pk=catalog.pk,token=catalog.token).update(status='failed',error='目录更新失败，请重试')
        transaction.on_commit(enqueue)
        return catalog


@api_login_required
@require_http_methods(['GET','POST'])
def tracker(request,kind,refresh=False):
    if kind not in KINDS:return api_error('不支持的 Tracker',404)
    if not membership_for(request):return api_error('请先加入团队',403)
    if refresh:
        if request.method!='POST':return api_error('仅支持 POST',405)
        catalog=queue_refresh(kind,True)
        if kind == 'icpc':
            from .tasks import enqueue_qoj_overlay
            for binding in PlatformBinding.objects.filter(user=request.user,platform='qoj').exclude(encrypted_credentials=''):
                enqueue_qoj_overlay(binding.pk)
        return JsonResponse({'status':catalog.status},status=202)
    if request.method!='GET':return api_error('仅支持 GET',405)
    try:
        page=int(request.GET.get('page','1'));binding_id=int(request.GET.get('account','0'))
        if page<1 or binding_id<0:raise ValueError()
    except ValueError:return api_error('筛选参数不正确',400)
    bindings=PlatformBinding.objects.filter(user=request.user,platform=KINDS[kind]).order_by('id')
    if binding_id and not bindings.filter(pk=binding_id).exists():return api_error('账号不存在',404)
    catalog=TrackerCatalog.objects.filter(kind=kind).first()
    overlays = list(QojCatalogOverlay.objects.filter(binding__in=bindings)) if kind == 'icpc' else []
    if binding_id:
        overlays = [row for row in overlays if row.binding_id == binding_id]
    details = {}
    for overlay in overlays:
        details.update(overlay.contests)
    keys=accepted_keys(request.user,kind,binding_id)
    from .platform_rewards import available_rewards,namespace,canonical,metadata
    reward_map=available_rewards(membership_for(request),KINDS[kind],binding_id or None)
    reward_info=metadata(KINDS[kind]) if reward_map else {}
    filtered_rewards=set()
    items=[];all_items=catalog.contests if catalog else []
    query=request.GET.get('q','').strip().casefold()
    for source in all_items:
        actual_problems = details.get(source['id'],source['problems'])
        if query and query not in (' '.join([source['title'],source.get('site','')]+[p['title']+' '+p['label'] for p in actual_problems])).casefold():continue
        if any(request.GET.get(key) and str(source.get(key,''))!=request.GET[key] for key in ('year','category','stage','site')):continue
        item=dict(source);problems=[]
        ratings = {p['label']:p for p in source['problems']}
        for source_problem in actual_problems:
            p = dict(source_problem)
            for field in ('tier','difficulty_source','accepted_teams','total_teams'):
                if field in ratings.get(p['label'],{}):
                    p[field] = ratings[p['label']][field]
            problems.append(dict(p,accepted=bool(keys.intersection([p['key']]+p.get('aliases',[])))))
            ns=KINDS[kind]
            reward_key=ns+'|'+canonical(ns,p['key'],reward_info)
            reward=reward_map.get(reward_key)
            problems[-1].update(claim_key=reward_key if reward else '',reward=reward['amount'] if reward else 0)
        problems.sort(key=lambda p: ('ZZ' if p['label'].casefold()=='ex' else p['label'].upper()))
        item['problems']=problems;item['solved']=sum(p['accepted'] for p in problems)
        status='done' if problems and item['solved']==len(problems) else 'doing' if item['solved'] else 'todo'
        if request.GET.get('progress') and request.GET['progress']!=status:continue
        if request.GET.get('hide_completed')=='1' and status=='done':continue
        item['progress']=status;items.append(item)
        filtered_rewards.update(p['claim_key'] for p in problems if p.get('claim_key'))
    items.sort(key=lambda c:(c.get('year',''),c.get('starts_at',0),int(c['id']) if c['id'].isdigit() else 0,c['id']),reverse=True)
    total=len(items);pages=max(1,math.ceil(total/20));page=min(page,pages)
    filters={field:sorted({str(c.get(field,'')) for c in all_items if c.get(field)},reverse=field=='year') for field in ('year','category','stage','site')}
    pending=catalog and catalog.status=='pending' and catalog.requested_at and catalog.requested_at>timezone.now()-timedelta(minutes=5)
    response=JsonResponse({'kind':kind,'contests':items[(page-1)*20:page*20],'page':page,'pages':pages,'total':total,
        'solved_contests':sum(c['progress']=='done' for c in items),'solved_problems':sum(c['solved'] for c in items),
        'problem_count':sum(len(c['problems']) for c in items),'filters':filters,
        'reward_keys':sorted(filtered_rewards),'reward_total':sum(reward_map[key]['amount'] for key in filtered_rewards),
        'accounts':[{'id':b.pk,'account':b.account,'status':b.status,'error':b.error} for b in bindings],
        'status':'pending' if pending else 'failed' if catalog and catalog.status=='pending' else catalog.status if catalog else 'idle',
        'updated_at':catalog.updated_at.isoformat() if catalog and catalog.updated_at else None,
        'details_status':'pending' if any(row.status=='pending' for row in overlays) else 'ready',
        'error':catalog.error if catalog and catalog.status=='failed' else ''})
    response['Cache-Control']='no-store'
    return response
