"""Public contest catalogs, adapted from OJ Insight (MIT)."""
import math
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .platform_sync import SyncError

KINDS = {'icpc': 'qoj', 'codeforces': 'codeforces', 'atcoder': 'atcoder', 'nowcoder': 'nowcoder'}


def contest(key,title,url,starts=0,category='',**extra):
    try:
        year=str(datetime.fromtimestamp(float(starts)).year) if float(starts)>0 else ''
    except (ValueError,OverflowError,OSError):
        year=''
    return dict(id=str(key),title=title,url=url,starts_at=starts,year=year,category=category,
                stage='',site='',problems=[],**extra)


def problem(key,label,title,url,difficulty=None,**extra):
    return dict(key=str(key).lower(),label=str(label),title=str(title),url=url,
                difficulty=difficulty,**extra)


def safe_url(value,base,hosts):
    from urllib.parse import urlsplit
    url=urljoin(base,str(value or ''))
    p=urlsplit(url)
    return url if p.scheme=='https' and p.hostname in hosts and not p.username and not p.password else base


def fetch(kind,remote,state):
    """Return one bounded batch, continuation state, and completion flag."""
    state=dict(state)
    if kind=='codeforces':
        a=remote.json('https://codeforces.com/api/contest.list?gym=false')
        b=remote.json('https://codeforces.com/api/problemset.problems',limit=64*1024*1024)
        if a.get('status')!='OK' or b.get('status')!='OK':
            raise SyncError('目录更新失败，请重试')
        items={str(c['id']):contest(c['id'],c['name'],f"https://codeforces.com/contest/{c['id']}",c.get('startTimeSeconds',0),cf_category(c['name']))
               for c in a['result'] if c.get('phase')=='FINISHED'}
        for p in b['result']['problems']:
            key=str(p.get('contestId'))
            if key in items:
                items[key]['problems'].append(problem(f"{key}:{p['index']}",p['index'],p['name'],f"https://codeforces.com/contest/{key}/problem/{p['index']}",p.get('rating')))
        return list(items.values()),state,True
    if kind=='atcoder':
        root='https://kenkoooo.com/atcoder/resources/'
        contests=remote.json(root+'contests.json',limit=32*1024*1024)
        problems=remote.json(root+'problems.json',limit=32*1024*1024)
        pairs=remote.json(root+'contest-problem.json',limit=32*1024*1024)
        models=remote.json(root+'problem-models.json',limit=32*1024*1024)
        if not all(isinstance(value,list) and value for value in (contests,problems,pairs)) or not isinstance(models,dict):
            raise SyncError('目录更新失败，请重试')
        items={c['id']:contest(c['id'],c['title'],'https://atcoder.jp/contests/'+c['id'],c.get('start_epoch_second',0),at_category(c['id'])) for c in contests}
        details={p['id']:p for p in problems}
        for pair in pairs:
            cid,pid=pair['contest_id'],pair['problem_id']
            if cid not in items or pid not in details:
                continue
            p=details[pid];raw=models.get(pid,{}).get('difficulty')
            difficulty=round(400*math.exp((raw-400)/400)) if isinstance(raw,(int,float)) and raw<400 else raw
            items[cid]['problems'].append(problem(pid,p.get('problem_index',pid),p['title'],f'https://atcoder.jp/contests/{cid}/tasks/{pid}',difficulty))
        return list(items.values()),state,True
    if kind=='nowcoder':
        page=state.get('page',1)
        data=remote.json(f'https://www.nowcoder.com/problem/tracker/list?contestType=0&page={page}&pageSize=100&limit=100',headers={'Referer':'https://www.nowcoder.com/problem/tracker'},limit=32*1024*1024)
        if data.get('code') not in (0,200) or not isinstance(data.get('data'),dict):
            raise SyncError('目录更新失败，请重试')
        payload=data['data']
        papers=next((payload[k] for k in ('papers','list','records','result') if isinstance(payload.get(k),list)),None)
        if papers is None:
            raise SyncError('目录更新失败，请重试')
        items=[parse_nowcoder(p) for p in papers]
        state.update(page=page+1)
        total=payload.get('totalCount')
        return items,state,len(papers)<100 or isinstance(total,int) and page*100>=total
    if kind=='icpc':
        queue=state.get('queue',[['21',1],['205',1],['212',1]])
        visited=state.get('visited',[])
        if not queue:
            return [],state,True
        cid,depth=queue.pop(0)
        html=remote.text('https://qoj.ac/category/'+cid)
        items,children=parse_qoj_category(html)
        visited.append(cid)
        if depth:
            queue.extend([[child,depth-1] for child in children if child not in visited and child not in [q[0] for q in queue]])
        state.update(queue=queue,visited=visited)
        return items,state,not queue
    raise SyncError('不支持的 Tracker')


def cf_category(title):
    if 'Educational' in title:return 'Educational'
    if 'Div. 1' in title and 'Div. 2' in title:return 'Div. 1 + 2'
    for value in ('Div. 1','Div. 2','Div. 3','Div. 4'):
        if value in title:return value
    return '其他'


def at_category(key):
    for prefix,label in [('abc','ABC'),('arc','ARC'),('agc','AGC'),('ahc','AHC'),('adt','每日训练'),('awc','AWC')]:
        if key.startswith(prefix):return label
    return '其他'


def parse_nowcoder(paper):
    cid=str(paper.get('id',paper.get('contestId','')))
    title=paper.get('contestName',paper.get('name',paper.get('title','')))
    category=next((label for word,label in [('周赛','周赛'),('小白月赛','小白月赛'),('练习赛','练习赛'),('挑战赛','挑战赛'),('寒假','寒假营'),('多校','多校'),('ICPC','XCPC'),('CCPC','XCPC')] if word in title),'其他')
    item=contest(cid,title,'https://ac.nowcoder.com/acm/contest/'+cid,(paper.get('beginTime',paper.get('startTime',0)) or 0)/1000,category)
    for index,p in enumerate(paper.get('questions',paper.get('problems',[]))):
        label=str(p.get('index',p.get('problemIndex',chr(65+index))))
        url=safe_url(p.get('questionUrl',p.get('problemUrl',p.get('url'))),item['url'],{'ac.nowcoder.com','www.nowcoder.com'})
        match=re.search(r'/acm/(problem/\d+|contest/\d+/[A-Za-z0-9]+)',url)
        key=match[1] if match else f'contest/{cid}/{label}'
        pid=p.get('problemId',p.get('problem_id'))
        aliases=[f'contest/{cid}/{label}'.lower()]
        if pid:aliases.append('problem/'+str(pid))
        practice=re.search(r'/practice/([0-9a-fA-F]{32})',url)
        if practice:aliases.extend([practice[1].lower(),'practice/'+practice[1].lower()])
        item['problems'].append(problem(key,label,p.get('title',p.get('questionTitle',p.get('name',label))),url,
                                        p.get('difficulty',p.get('difficultyScore',p.get('rating'))),aliases=aliases))
    return item


SITE_NAMES={'Beijing':'北京','Shanghai':'上海','Nanjing':'南京','Jinan':'济南','Chengdu':'成都','Chongqing':'重庆',
            'Hangzhou':'杭州','Wuhan':'武汉','Xian':'西安',"Xi'an":'西安','Shenyang':'沈阳','Hefei':'合肥',
            'Shenzhen':'深圳','Harbin':'哈尔滨','Qinhuangdao':'秦皇岛','Xuzhou':'徐州','Kunming':'昆明','Nanchang':'南昌',
            'Macau':'澳门','Hong Kong':'香港','Guilin':'桂林','Guangzhou':'广州','Weihai':'威海','Changchun':'长春',
            'Zhengzhou':'郑州','Lanzhou':'兰州','Mianyang':'绵阳','Xiangtan':'湘潭','Fuzhou':'福州','Xiamen':'厦门'}


def event_meta(title):
    year=re.search(r'20\d\d',title)
    low=title.lower()
    provincial=any(x in low for x in ('provincial','province','省赛')) or bool(re.search(r'[省市].{0,4}大学生.*程序设计',title))
    provincial=provincial or bool(re.search(r'\b(?:fujian|heilongjiang|shandong|jiangsu|zhejiang|jiangxi|hunan|hubei|henan|hebei|jilin|liaoning|sichuan|guangdong|guangxi|guizhou|yunnan|shaanxi|shanxi|anhui|hainan)\s+(?:\w+\s+){0,2}collegiate',low))
    series='省赛' if provincial else 'CCPC' if 'ccpc' in low or 'china collegiate' in low else 'ICPC'
    stage='网络赛' if any(x in low for x in ('online','preliminary','qualification','网络','预选')) else '总决赛' if any(x in low for x in ('world final','finals','总决赛')) else '区域赛' if series=='ICPC' else '分站赛'
    site=next((cn for en,cn in sorted(SITE_NAMES.items(),key=lambda x:-len(x[0])) if en.casefold() in low or cn in title),'全国' if stage=='网络赛' else '')
    return {'year':year[0] if year else '', 'category':series,'stage':stage,'site':site}


def qoj_problems(node):
    out={}
    for anchor in node.select('a[href]'):
        match=re.fullmatch(r'(?:https://qoj\.ac)?/(?:contest/\d+/)?problem/(\d+)(?:[/?#].*)?',anchor['href'])
        if not match:continue
        key=match[1];text=anchor.get_text(' ',strip=True)
        title=anchor.get('title',anchor.get('data-original-title',text))
        label=re.match(r'^([A-Z][0-9]?)\b',text)
        label=label[1] if label else chr(65+len(out))
        title=re.sub(r'^[A-Z][0-9]?\s*[.：:]\s*','',title)
        if key not in out or len(title)>len(out[key]['title']):
            out[key]=problem(key,label,title,'https://qoj.ac/problem/'+key)
    return list(out.values())


def parse_qoj_category(html):
    soup=BeautifulSoup(html,'html.parser')
    if not soup.select_one('table'):
        raise SyncError('目录更新失败，请重试')
    items=[];children=[]
    for row in soup.select('table tr'):
        link=next((a for a in row.select('a[href]') if re.fullmatch(r'(?:https://qoj\.ac)?/contest/\d+/?',a['href'])),None)
        if link:
            title=link.get_text(' ',strip=True)
            if any(word in title.lower() for word in ('warmup','warm-up','热身')):continue
            cid=link['href'].rstrip('/').rsplit('/',1)[-1]
            item=contest(cid,title,'https://qoj.ac/contest/'+cid)
            item.update(event_meta(title));item['problems']=qoj_problems(row)
            if not item['problems']:
                labels=[node.get_text(strip=True) for node in row.select('.badge')]
                item['problems']=[problem(f'contest:{cid}:{label}',label,label,item['url']) for label in labels if re.fullmatch(r'[A-Z][0-9]?',label)]
            items.append(item)
        else:
            children.extend(a['href'].rstrip('/').rsplit('/',1)[-1] for a in row.select('a[href]') if re.fullmatch(r'(?:https://qoj\.ac)?/category/\d+/?',a['href']))
    return items,list(dict.fromkeys(children))
