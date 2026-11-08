"""Shared public source snapshots. Personal/credential-dependent endpoints bypass this.

Keep the last good snapshot for diagnostics, but never serve expired data as a
successful verification. A distributed lock coalesces concurrent refreshes.
"""
import hashlib
import json
import re
import time
import uuid
from urllib.parse import urlsplit, parse_qsl, urlencode, urlunsplit

from django.core.cache import caches
from .platform_sync import SyncError

cache = caches['platform_requests']


def canonical_url(url):
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(sorted(parse_qsl(p.query))), ''))


def policy(url):
    p = urlsplit(url)
    if p.scheme != 'https':
        return None
    if p.netloc == 'codeforces.com' and p.path in ('/api/contest.list', '/api/problemset.problems'):
        if any(k != 'gym' for k, _ in parse_qsl(p.query)):
            return None
        return 'json', 900
    if p.netloc == 'kenkoooo.com' and p.path in tuple('/atcoder/resources/' + f for f in
            ('contests.json', 'problems.json', 'contest-problem.json', 'problem-models.json')):
        return 'json', 3600
    if p.netloc in ('leetcode.cn', 'leetcode.com') and re.fullmatch(r'/contest/api/(?:info/[\w-]+|list)/', p.path):
        return 'json', 900
    if p.netloc == 'ac.nowcoder.com' and p.path in ('/acm/contest/contest-info', '/acm/contest/problem-list'):
        if all(k in ('id', 'page') for k, _ in parse_qsl(p.query)):
            return 'json', 900
    if p.netloc == 'www.nowcoder.com' and p.path == '/problem/tracker/list':
        return 'json', 3600
    if p.netloc == 'atcoder.jp' and re.fullmatch(r'/contests/[\w-]+/tasks', p.path):
        return 'text', 900
    if url == 'https://raw.githubusercontent.com/zerotrac/leetcode_problem_rating/main/ratings.txt':
        return 'text', 3600
    return None


def validate(url, value):
    kind, _ = policy(url)
    if kind == 'text':
        if not isinstance(value, str) or not value.strip() or ('atcoder.jp/' in url and '<table' not in value):
            raise SyncError('公开题目资料不完整，请稍后重试')
    elif 'codeforces.com/' in url:
        if not isinstance(value, dict) or value.get('status') != 'OK' or 'result' not in value:
            raise SyncError('Codeforces 目录暂不可用')
        result = value['result']
        if ('contest.list' in url and not isinstance(result, list)) or ('problemset.problems' in url and
                (not isinstance(result, dict) or not isinstance(result.get('problems'), list))):
            raise SyncError('Codeforces 目录不完整')
    elif 'kenkoooo.com/' in url:
        expected = dict if url.endswith('problem-models.json') else list
        if not isinstance(value, expected) or (expected is list and not value):
            raise SyncError('AtCoder 目录暂不可用')
    elif not isinstance(value, dict) or value.get('code', 0) not in (0, 200) or not value:
        raise SyncError('公开比赛资料暂不可用')
    elif '/contest/api/info/' in url and (not isinstance(value.get('contest'), dict) or
            not value['contest'].get('start_time') or not value['contest'].get('duration')):
        raise SyncError('比赛时间不完整')
    elif 'nowcoder.com/' in url and not isinstance(value.get('data'), dict):
        raise SyncError('牛客比赛资料不完整')


def resource(url, loader, *, historical=False):
    rule = policy(url)
    if rule is None:
        return loader()
    url = canonical_url(url)
    key = 'public-catalog:v1:' + hashlib.sha256(url.encode()).hexdigest()
    ttl = 86400 if historical and '/tasks' in url else rule[1]
    deadline = time.monotonic() + 12
    while True:
        snapshot = cache.get(key)
        if snapshot is not None and time.time() - snapshot['fetched_at'] < ttl:
            return snapshot['value']
        # Lease exceeds the bounded Remote request lifetime. Do not delete it
        # without checking ownership after a slow/terminated request.
        owner = uuid.uuid4().hex
        if cache.add(key + ':lock', owner, 180):
            try:
                value = loader()
                validate(url, value)
                cache.set(key, {'value': value, 'fetched_at': time.time(), 'source': url}, 30 * 86400)
                return value
            finally:
                # Redis compare-and-delete prevents a late owner releasing a
                # replacement lease. LocMem is only used in isolated tests.
                if hasattr(cache, '_cache') and hasattr(cache._cache, 'get_client'):
                    client = cache._cache.get_client(write=True)
                    client.eval("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end",
                                1, cache.make_key(key + ':lock'), cache._cache._serializer.dumps(owner))
                elif cache.get(key + ':lock') == owner:
                    cache.delete(key + ':lock')
        if time.monotonic() >= deadline:
            raise SyncError('公开比赛资料正在更新，请稍后重试')
        time.sleep(.1)


class PublicRemote:
    def __init__(self, remote, *, historical=False):
        self.remote = remote.remote if isinstance(remote, PublicRemote) else remote
        self.historical = historical or (isinstance(remote, PublicRemote) and remote.historical)

    def __getattr__(self, name):
        return getattr(self.remote, name)

    def private(self, kwargs):
        from requests import Session
        credentials = getattr(self.remote, 'credentials', None)
        session = getattr(self.remote, 'session', None)
        return (kwargs.get('payload') is not None or
                isinstance(credentials, dict) and bool(credentials) or
                isinstance(session, Session) and bool(session.cookies) or
                any(k.lower() in ('cookie', 'authorization') for k in (kwargs.get('headers') or {})))

    def json(self, url, **kwargs):
        if self.private(kwargs):
            return self.remote.json(url, **kwargs)
        return resource(url, lambda: self.remote.json(url, **kwargs), historical=self.historical)

    def text(self, url, **kwargs):
        rule = policy(url)
        if rule and rule[0] == 'text' and not self.private(kwargs):
            return resource(url, lambda: self.remote.text(url, **kwargs), historical=self.historical)
        return self.remote.text(url, **kwargs)


def calendar_text(url, loader):
    rule = policy(url)
    if rule and rule[0] == 'json':
        return json.dumps(resource(url, lambda: json.loads(loader())))
    return loader()
