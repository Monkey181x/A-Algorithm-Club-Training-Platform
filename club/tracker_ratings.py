"""ICPC board matching and solve-rate tiers, adapted from OJ Insight (MIT)."""
import re
import json
from collections import defaultdict
from pathlib import Path

from .tracker_sources import event_meta, SITE_NAMES


def normalize(text):
    return ''.join(ch for ch in text.casefold() if ch.isalnum())


def round_number(text):
    values=set()
    for m in re.finditer(r'\(([ivx]+|[1-9]\d?)\)|(?:online(?:[\s_-]+(?:qualification|contest))?|preliminary|round)[\s_:-]+([ivx]+|[1-9]\d?)(?:\b|_)|第([一二三四五六七八九十\d]+)[场轮]',text,re.I):
        token=next(x for x in m.groups() if x).lower()
        mapping={'i':1,'ii':2,'iii':3,'iv':4,'v':5,'一':1,'二':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9,'十':10}
        value=int(token) if token.isdigit() else mapping.get(token)
        if value:values.add(value)
    return next(iter(values)) if len(values)==1 else None


def match_board(event,boards):
    candidates=[]
    for board in boards:
        text=board['text'];flat=normalize(text);meta=event_meta(text)
        if meta['year']!=event['year'] or meta['category']!=event['category']:
            continue
        online=event['stage']=='网络赛'
        if online != (meta['stage']=='网络赛'):
            continue
        if online and round_number(event['title'])!=round_number(text):
            continue
        site=event.get('site','')
        if not online:
            if not site:continue
            aliases=[normalize(site)]+[normalize(en) for en,cn in SITE_NAMES.items() if cn==site]
            if not any(alias and alias in flat for alias in aliases):continue
        if any(word in flat for word in ('warmup','warmupcontest')):continue
        exact=normalize(event['title']) in flat
        candidates.append((int(exact),board))
    if not candidates:return None
    score=max(s for s,b in candidates)
    winners=[b for s,b in candidates if s==score]
    return winners[0] if len(winners)==1 else None


def tier(accepted,total):
    if total<=0:return None
    rate=accepted*100/total
    return 'gold' if rate<=10 else 'silver' if rate<=30 else 'bronze' if rate<=60 else 'iron'


def board_index(remote):
    boards=[]
    try:
        data=remote.json('https://rl.algoux.cn/api/v2/public/contests')
        for item in data.get('data',{}).get('contests',[]):
            if item.get('srkFileID'):
                labels=[str(item.get('uk','')),str(item.get('name',''))]
                labels.extend(str(value) for value in (item.get('title') or {}).values())
                boards.append({'source':'RankLand','text':' '.join(labels),'file_id':str(item['srkFileID'])})
    except Exception:pass
    # Public SRK collection exposes direct, fixed-origin downloads.
    if not boards:
        try:
            data=remote.json('https://api.github.com/repos/algoux/srk-collection/git/trees/master?recursive=1',limit=32*1024*1024)
            for node in data.get('tree',[]):
                path=node.get('path','')
                if path.startswith('official/') and path.endswith('.srk.json') and 'warmup' not in path.lower():
                    boards.append({'source':'RankLand','text':path,'url':'https://raw.githubusercontent.com/algoux/srk-collection/master/'+path})
        except Exception:pass
    paths=[]
    try:
        data=remote.json('https://api.github.com/repos/xcpcio/board-data/git/trees/main?recursive=1',limit=32*1024*1024)
        paths=[node.get('path','') for node in data.get('tree',[])]
    except Exception:pass
    if not paths:
        paths=json.loads((Path(__file__).parent/'data'/'xcpc-board-index.json').read_text(encoding='utf-8'))
    for path in paths:
        if path.startswith('data/') and path.endswith('/config.json'):
            text=path
            match=re.search(r'data/(icpc|ccpc)/(\d+)(?:st|nd|rd|th)/',path)
            if match:
                year=int(match[2])+(1975 if match[1]=='icpc' else 2014)
                text+=' '+str(year)
            boards.append({'source':'XCPCIO','text':text,'url':'https://raw.githubusercontent.com/xcpcio/board-data/main/'+path.rsplit('/',1)[0]+'/'})
    return boards


def board_stats(remote,board):
    if board['source']=='RankLand':
        url=board.get('url')
        if not url:
            meta=remote.json('https://rl.algoux.cn/api/v2/public/files/'+board['file_id'])
            url=meta.get('data',{}).get('url','')
        data=remote.json(url,limit=64*1024*1024)
        total=len(data.get('rows',[]))
        stats={}
        for p in data.get('problems',[]):
            accepted=(p.get('statistics') or {}).get('accepted')
            if type(accepted) is int and total>0 and 0<=accepted<=total:
                stats[str(p.get('alias','')).upper()]=(accepted,total)
        return stats
    config=remote.json(board['url']+'config.json')
    teams=remote.json(board['url']+'team.json',limit=32*1024*1024)
    runs=remote.json(board['url']+'run.json',limit=64*1024*1024)
    teams=teams if isinstance(teams,list) else list(teams.values())
    official=[t for t in teams if t.get('official') not in (False,0,None) or 'official' in t.get('group',[])]
    included={str(t['team_id']) for t in (official or teams) if 'team_id' in t}
    solved=defaultdict(set)
    for run in runs:
        if str(run.get('status','')).lower() in {'accepted','correct'} and str(run.get('team_id')) in included:
            solved[str(run.get('problem_id'))].add(str(run['team_id']))
    return {str(p['label']).upper():(len(solved[str(p['id'])]),len(included)) for p in config.get('problems',[]) if 'id' in p and 'label' in p and included}


def enrich(remote,event,boards):
    for source in ('XCPCIO','RankLand'):
        board=match_board(event,[b for b in boards if b['source']==source])
        if not board:continue
        try:stats=board_stats(remote,board)
        except Exception:continue
        if set(stats)!=set(p['label'].upper() for p in event['problems']):continue
        for p in event['problems']:
            accepted,total=stats[p['label'].upper()]
            p.update(tier=tier(accepted,total),accepted_teams=accepted,total_teams=total,difficulty_source=source)
        event['board_source']=source
        break
