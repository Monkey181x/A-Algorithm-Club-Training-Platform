"""Account-scoped encrypted credentials and origin-scoped requests."""
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .platform_sync import SyncError


def load_credentials(binding):
    if not binding.encrypted_credentials:
        return {}
    from .problem_parser import key_cipher
    try:
        return json.loads(key_cipher().decrypt(binding.encrypted_credentials.encode()))
    except Exception:
        raise SyncError('请重新保存凭据') from None


def save_credentials(binding, data):
    from .problem_parser import key_cipher
    if data.get('clear_credentials'):
        binding.encrypted_credentials = ''
    allowed = {'codeforces': ('api_key', 'api_secret'), 'nowcoder': ('cookie',),
               'leetcode': ('cookie',), 'qoj': ('cookie',)}.get(binding.platform, ())
    incoming = {key: data[key].strip() for key in allowed if isinstance(data.get(key), str) and data[key].strip()}
    if not incoming:
        return
    if any(len(value) > 16000 or '\r' in value or '\n' in value for value in incoming.values()):
        raise ValueError('凭据格式不正确')
    current = load_credentials(binding)
    current.update(incoming)
    if binding.platform == 'codeforces' and not all(current.get(k) for k in allowed):
        raise ValueError('请填写 API Key 和 Secret')
    if binding.platform == 'qoj' and '=' not in current.get('cookie', ''):
        current['cookie'] = '__Host-UOJSESSID=' + current['cookie']
    binding.encrypted_credentials = key_cipher().encrypt(json.dumps(current).encode()).decode()


def prepare_request(remote, url, headers):
    parts = urlsplit(url)
    host = parts.hostname
    headers = dict(headers or {})
    credentials = getattr(remote, 'credentials', {})
    platform = credentials.get('platform')
    expected = {'codeforces': {'codeforces.com'}, 'qoj': {'qoj.ac'},
                'nowcoder': {'ac.nowcoder.com', 'www.nowcoder.com'},
                'leetcode': {'leetcode.cn'} if credentials.get('china') else {'leetcode.com'}}
    if host in {'codeforces.com', 'kenkoooo.com'}:
        from django.core.cache import caches
        gate = caches['platform_requests']
        gap = 3 if host == 'codeforces.com' else 2
        while not gate.add('request:' + host, True, timeout=gap):
            if time.monotonic() >= remote.deadline:
                raise SyncError('同步超时，请重试')
            time.sleep(.15)
    if host not in expected.get(platform, set()):
        return url, headers
    if credentials.get('cookie'):
        headers['Cookie'] = credentials['cookie']
    # Public regular-contest standings must remain anonymous, with contestId only.
    if platform == 'codeforces' and credentials.get('api_key') and parts.path != '/api/contest.standings':
        params = dict(parse_qsl(parts.query))
        if 'apiSig' not in params:
            params.update(apiKey=credentials['api_key'], time=str(int(time.time())))
            prefix = secrets.token_hex(3)
            query = urlencode(sorted(params.items()))
            raw = prefix + '/' + parts.path.rsplit('/', 1)[-1] + '?' + query + '#' + credentials['api_secret']
            params['apiSig'] = prefix + hashlib.sha512(raw.encode()).hexdigest()
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(sorted(params.items())), ''))
    return url, headers


def attach(remote, binding):
    remote.credentials = dict(load_credentials(binding), platform=binding.platform,
                              china=binding.account.startswith('cn:'))
    return remote
