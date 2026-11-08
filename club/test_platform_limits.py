from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from .platform_limits import wait_for_slot, retryable
from .platform_sync import CredentialError, SyncError

class LimitTests(SimpleTestCase):
    def test_alias_hosts_share_gate_and_platforms_are_independent(self):
        gate=Mock();gate.add.return_value=True
        with patch('club.platform_limits.caches',{'platform_requests':gate}),patch('club.platform_limits.time.monotonic',return_value=0):
            for host in ['ac.nowcoder.com','www.nowcoder.com','codeforces.com','atcoder.jp','kenkoooo.com']:
                wait_for_slot(host,10)
        keys=[c.args[0] for c in gate.add.call_args_list]
        self.assertEqual(keys[0],keys[1]);self.assertEqual(keys[3],keys[4]);self.assertNotEqual(keys[0],keys[2])

    def test_busy_gate_waits_and_deadline_is_bounded(self):
        gate=Mock();gate.add.side_effect=[False,True]
        with patch('club.platform_limits.caches',{'platform_requests':gate}),patch('club.platform_limits.time.monotonic',return_value=0),patch('club.platform_limits.time.sleep') as sleep:
            wait_for_slot('qoj.ac',10)
        sleep.assert_called_once()
        with patch('club.platform_limits.time.monotonic',return_value=11):
            with self.assertRaises(SyncError):wait_for_slot('qoj.ac',10)

    def test_failure_types(self):
        self.assertFalse(retryable(CredentialError('access denied')))
        self.assertFalse(retryable(SyncError('Cookie 与账号不匹配')))
        self.assertTrue(retryable(SyncError('平台连接失败，请稍后重试')))
