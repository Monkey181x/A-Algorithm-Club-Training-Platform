"""Cross-worker request spacing shared by profile, contest and tracker adapters."""
import time
from django.core.cache import caches

GROUPS = {
    'codeforces.com': ('codeforces', 3),
    'atcoder.jp': ('atcoder', 3), 'kenkoooo.com': ('atcoder', 3),
    'ac.nowcoder.com': ('nowcoder', 2), 'www.nowcoder.com': ('nowcoder', 2),
    'leetcode.cn': ('leetcode', 2), 'leetcode.com': ('leetcode', 2),
    'www.luogu.com.cn': ('luogu', 2), 'qoj.ac': ('qoj', 2),
    'api.github.com': ('github', 1), 'raw.githubusercontent.com': ('github', 1),
    'rl.algoux.cn': ('rankland', 1), 'cdn.algoux.cn': ('rankland', 1),
}


def wait_for_slot(host, deadline):
    from .platform_sync import SyncError
    group, gap = GROUPS.get(host, (host, 1))
    gate = caches['platform_requests']
    while time.monotonic() < deadline:
        try:
            if gate.add('oj-request:' + group, True, timeout=gap):
                return
        except Exception:
            raise SyncError('请求限流服务暂不可用，请稍后重试') from None
        time.sleep(min(.15, max(0, deadline-time.monotonic())))
    raise SyncError('等待平台请求额度超时，请稍后重试')


def retryable(error):
    """Explicit account/config problems require user action, not more traffic."""
    from .platform_sync import CredentialError
    if isinstance(error, CredentialError):
        return False
    message = str(error)
    return not any(text in message for text in (
        '账号不匹配', 'Cookie 与账号不匹配', '请填写 QOJ Cookie',
        '请重新保存凭据', '请填写 API Key', '账号已添加',
        '凭据格式不正确', '未找到 Codeforces 账号', '未找到力扣',
    ))
