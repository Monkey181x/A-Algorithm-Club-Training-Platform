"""One-time public solved baseline. Never used to issue contest rewards."""
import math
import re
import time
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from django.utils import timezone
from django.core.exceptions import ValidationError

from .platform_sync import SyncError


def contest_key(problem):
    contest=problem.participation
    try:
        from .problem_parser import canonical_problem
        identity,_=canonical_problem(problem.url)
        if identity.startswith(contest.platform+':'):
            return identity.split(':',1)[1]
    except (ValueError,TypeError,ValidationError):
        pass
    if contest.platform=='codeforces':
        return f'{contest.contest_key}:{problem.problem_key}'.lower()
    if contest.platform=='atcoder':
        key=problem.problem_key.lower()
        return key if key.startswith(contest.contest_key.lower()+'_') else f'{contest.contest_key}_{key}'.lower()
    if contest.platform=='nowcoder':
        return f'contest/{contest.contest_key}/{problem.problem_key}'.lower()
    return problem.problem_key.lower()


def problem_url(platform,key,account):
    key=quote(key,safe=':_-/')
    if platform=='codeforces' and ':' in key:
        contest,index=key.split(':',1)
        return f'https://codeforces.com/problemset/problem/{contest}/{index.upper()}'
    if platform=='atcoder' and '_' in key:
        return f'https://atcoder.jp/contests/{key.rsplit("_",1)[0]}/tasks/{key}'
    if platform=='leetcode':
        return ('https://leetcode.cn' if account.startswith('cn:') else 'https://leetcode.com')+'/problems/'+key+'/'
    if platform=='luogu':
        return 'https://www.luogu.com.cn/problem/'+key.upper()
    if platform=='nowcoder':
        return 'https://ac.nowcoder.com/acm/'+key
    if platform=='qoj':
        return 'https://qoj.ac/problem/'+key
    return ''


def day_of(value):
    try:
        value = float(value)
        if value >= 1e12:
            value /= 1000
        if not math.isfinite(value) or value <= 0 or value > timezone.now().timestamp():
            return None
        return timezone.localdate(datetime.fromtimestamp(value, ZoneInfo('UTC'))).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def remember(dates, key, value):
    day = day_of(value)
    if day and (key not in dates or day < dates[key]):
        dates[key] = day


def result(total, keys, dates, notes=(), *, ids_complete=True):
    if type(total) is not int or total < 0:
        raise SyncError('未能获取可靠的累计过题总数，未建立基线')
    if len(keys)>total:
        raise SyncError('平台累计数与题目明细不一致，未建立基线')
    return {'total':total, 'problem_ids':sorted(keys), 'ids_complete':ids_complete,
            'dated_problems':dates, 'notes':list(notes)}


def collect(remote, platform, account, snapshot):
    """Fetch full counts where public; unavailable dates stay unknown, never guessed."""
    dates, keys = {}, set()
    if platform == 'codeforces':
        offset, seen = 1, set()
        while True:
            time.sleep(2.1)
            data = remote.json(f'https://codeforces.com/api/user.status?handle={quote(account,safe="")}&from={offset}&count=10000', limit=64*1024*1024)
            if data.get('status') != 'OK' or not isinstance(data.get('result'),list):
                raise SyncError('Codeforces 过题历史暂不可获取')
            rows = data['result']
            signature = tuple(str(r.get('id',r)) for r in rows)
            if rows and signature in seen:
                raise SyncError('Codeforces 分页未能继续，未使用部分总数')
            seen.add(signature)
            for row in rows:
                if row.get('verdict') != 'OK':
                    continue
                p = row.get('problem') or {}
                key = f"{p.get('contestId')}:{p.get('index','')}".lower()
                if not p.get('contestId') or not p.get('index'):
                    continue
                keys.add(key); remember(dates,key,row.get('creationTimeSeconds'))
            if len(rows) < 10000:
                return result(len(keys),keys,dates)
            offset += len(rows)
    if platform == 'atcoder':
        cursor, seen = 0, set()
        while True:
            time.sleep(3.1)
            try:
                rows = remote.json(f'https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user={quote(account,safe="")}&from_second={cursor}')
            except SyncError:
                return result(snapshot.get('solved'),keys,dates,
                    ['完整提交时间未能全部获取，未知日期题目只计总榜'],ids_complete=False)
            if not isinstance(rows,list):
                raise SyncError('AtCoder 过题历史暂不可获取')
            signature = tuple(str(r.get('id',r)) for r in rows)
            if rows and signature in seen:
                raise SyncError('AtCoder 分页未能继续，未使用部分总数')
            seen.add(signature)
            for row in rows:
                if str(row.get('user_id',account)).casefold()!=account.casefold():
                    raise SyncError('AtCoder 过题记录账号不匹配')
                if row.get('result')=='AC' and row.get('problem_id'):
                    key=row['problem_id'].lower(); keys.add(key); remember(dates,key,row.get('epoch_second'))
            if len(rows) < 500:
                total=max(len(keys),snapshot.get('solved',0))
                return result(total,keys,dates,ids_complete=total==len(keys))
            stamps=[r.get('epoch_second') for r in rows]
            if any(type(t) is not int for t in stamps) or max(stamps)<cursor or min(stamps)==max(stamps):
                return result(snapshot.get('solved'),keys,dates,
                    ['提交时间分页未能完整核验，未知日期题目只计总榜'],ids_complete=False)
            cursor=max(stamps)+1
    if platform == 'leetcode':
        china=account.startswith('cn:'); user=account[3:] if china else account
        host='https://leetcode.cn' if china else 'https://leetcode.com'
        notes=['平台仅公开部分 AC 时间；无法追溯日期的题目只计总榜']
        try:
            query = ('query($u:String!){recentACSubmissions(userSlug:$u){submitTime question{titleSlug}}}' if china else
                'query($u:String!){recentAcSubmissionList(username:$u,limit:20){titleSlug timestamp}}')
            payload=remote.json(host+('/graphql/noj-go/' if china else '/graphql/'),payload={'query':query,'variables':{'u':user}})
            rows=(payload.get('data') or {}).get('recentACSubmissions' if china else 'recentAcSubmissionList')
            if not isinstance(rows,list):
                raise SyncError('公开 AC 时间暂不可用')
            for row in rows:
                key=(row.get('question') or {}).get('titleSlug') if china else row.get('titleSlug')
                if key:
                    key=key.lower(); keys.add(key); remember(dates,key,row.get('submitTime') if china else row.get('timestamp'))
        except (SyncError,KeyError,TypeError,ValueError):
            notes.append('本次未取得公开 AC 时间，已保留准确累计总数')
        return result(snapshot.get('solved'),keys,dates,notes,ids_complete=False)
    if platform == 'luogu':
        from .contest_progress_sources import configure_luogu, luogu_ac_times
        uid=account
        profile_url=snapshot.get('profile_url','')
        match=re.search(r'/user/(\d+)$',profile_url)
        if match:
            uid=match[1]
        notes=[]
        try:
            cookie=configure_luogu(remote)
            if not cookie:
                raise SyncError('未配置洛谷查询 Cookie，无法追溯做题时间')
            evidence=luogu_ac_times(remote,uid,cookie,0)
            for pid, stamps in evidence.items():
                key=pid.lower(); keys.add(key)
                for stamp in stamps:
                    remember(dates,key,stamp)
        except SyncError:
            notes.append('公开做题时间不完整；无法追溯日期的题目只计总榜')
        return result(snapshot.get('solved'),keys,dates,notes,ids_complete=False)
    if platform == 'nowcoder':
        base=f'https://ac.nowcoder.com/acm/contest/profile/{account}'
        seen=set()
        for page in range(1,201):
            if page>1:
                time.sleep(.5)
            html=remote.text(base+f'/practice-coding?languageCategoryFilter=-1&orderType=DESC&page={page}&pageSize=200&statusTypeFilter=5',headers={'Referer':'https://ac.nowcoder.com/'})
            if not re.search(r'window\.curUser\.id\s*=\s*["\']'+re.escape(account)+r'["\']',html):
                raise SyncError('牛客过题记录账号不可核验')
            soup=BeautifulSoup(html,'html.parser')
            if not soup.select('table'):
                raise SyncError('牛客过题记录暂不可读取')
            rows=[]
            for row in soup.select('table tr'):
                link=next((a for a in row.select('a[href]') if re.search(r'/acm/(?:problem/\d+|contest/\d+/[A-Za-z0-9]+)$',a['href'].rstrip('/'))),None)
                if not link:
                    continue
                key=link['href'].rstrip('/').split('/acm/')[-1].lower(); keys.add(key); rows.append(str(row))
                table=row.find_parent('table')
                headers=[th.get_text(strip=True) for th in table.select('th')]
                column=next((i for i,label in enumerate(headers) if label in {'提交时间','通过时间','AC时间'}),None)
                cells=row.find_all('td',recursive=False)
                node=row.select_one('time[datetime]')
                raw_time=node.get('datetime','') if node else cells[column].get_text(' ',strip=True) if column is not None and column<len(cells) else ''
                stamp=re.search(r'\b(20\d\d-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d)?)\b',raw_time)
                if stamp:
                    try:
                        value=datetime.fromisoformat(stamp[1]).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
                        remember(dates,key,value)
                    except ValueError:
                        pass
            signature=tuple(rows)
            if rows and signature in seen:
                raise SyncError('牛客分页未能继续，未使用首屏总数')
            seen.add(signature)
            next_page=soup.select('a[rel="next"]') or any(a.get_text(strip=True) in {'下一页','>'} for a in soup.select('.pagination a'))
            if not next_page and len(rows)<200:
                return result(len(keys),keys,dates)
        raise SyncError('牛客历史记录过多，本次未能完整获取总数')
    raise SyncError('不支持的平台')
