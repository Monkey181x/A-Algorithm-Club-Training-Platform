from datetime import timedelta, date
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from .contest_rewards import reward_for, REWARD_START
from .contest_progress_sources import discover, parse_contest_url, hydrate


class WindowTests(SimpleTestCase):
    def test_boundaries_and_identity_all_platforms(self):
        member = NS(team=NS(currency_enabled=True, reward_settings={}, problem_rewards={}), user=None)
        profile = NS(grade=0, grade_date=date(2026, 10, 29))
        c = NS(source_data={}, starts_at=REWARD_START, ends_at=REWARD_START+timedelta(hours=1), detail_complete=True)
        p = NS(participation=c, accepted=True, official_ac=False, difficulty=None)
        with patch('django.utils.timezone.now', return_value=REWARD_START+timedelta(days=1)):
            for platform in ['codeforces', 'atcoder', 'nowcoder', 'luogu', 'leetcode']:
                c.platform = platform
                for offset, expected in [(-1, 0), (0, 1), (3599, 1), (3600, 0)]:
                    p.accepted_at = REWARD_START+timedelta(seconds=offset)
                    self.assertEqual(reward_for(p, member, profile)[0], expected, (platform, offset))
                p.accepted_at = None
                self.assertEqual(reward_for(p, member, profile)[0], 0)
            c.platform = 'qoj'
            self.assertEqual(reward_for(p, member, profile)[0], 2)

    def test_atcoder_discovers_awc_without_rating(self):
        remote = Mock()
        remote.json.side_effect = [[], [{'contest_id': 'awc0195', 'user_id': 'reader', 'epoch_second': REWARD_START.timestamp()+1, 'result': 'AC'}]]
        catalog = [{'id': 'awc0195', 'title': 'AWC', 'start_epoch_second': REWARD_START.timestamp(), 'duration_second': 3600}]
        with patch('club.contest_progress_sources.cached', return_value=catalog):
            rows = discover(remote, 'atcoder', 'reader')
        self.assertEqual([r['contest_key'] for r in rows], ['awc0195'])

    def test_link_allowlist(self):
        self.assertEqual(parse_contest_url('https://atcoder.jp/contests/awc0195')[1], 'awc0195')
        for url in ['http://atcoder.jp/contests/a', 'https://example.org/contest/1', 'https://qoj.ac/contest/1', 'https://atcoder.jp:443/contests/a']:
            with self.assertRaises(ValueError):
                parse_contest_url(url)

    def test_cf_identity_does_not_override_time(self):
        c = NS(platform='codeforces', contest_key='1', account='reader', url='https://codeforces.com/contest/1', starts_at=REWARD_START, ends_at=REWARD_START+timedelta(hours=1), source_data={})
        row = {'author': {'participantType': 'PRACTICE', 'members': [{'handle': 'reader'}]}, 'verdict': 'OK', 'creationTimeSeconds': REWARD_START.timestamp(), 'problem': {'index': 'A'}}
        with patch('club.contest_progress_sources.cf', side_effect=[{'rows': [], 'problems': [{'index': 'A', 'name': 'A'}]}, [row]]):
            rows, error = hydrate(Mock(), c)
        self.assertTrue(rows[0]['accepted'])


class RecoveryTests(SimpleTestCase):
    def test_leetcode_batches_reach_page_101_and_resume_failed_page(self):
        from .contest_progress_sources import ContinueHydration, SyncError
        c=NS(platform='leetcode',account='reader',contest_key='weekly-contest-1',starts_at=REWARD_START,ends_at=REWARD_START+timedelta(hours=1),source_data={'manual':True})
        remote=Mock(); failed=[False]; pages=[]
        def response(url, **kwargs):
            if '/info/' in url: return {'contest':{'start_time':REWARD_START.timestamp(),'duration':3600},'questions':[]}
            page=int(url.split('pagination=')[1].split('&')[0]); pages.append(page)
            if page==11 and not failed[0]:
                failed[0]=True; raise SyncError('temporary failure')
            return {'total_rank':[{'user_slug':'reader' if page==101 else 'other','data_region':'US'}],'submissions':[{}]}
        remote.json.side_effect=response
        with patch('club.contest_progress_sources.cache.get',return_value={}):
            for _ in range(13):
                try:
                    rows,error=hydrate(remote,c)
                    break
                except ContinueHydration: pass
                except SyncError:
                    self.assertEqual(c.source_data['ranking_scan']['next_page'],11)
            else: self.fail('Did not finish scan')
        self.assertEqual(pages.count(11),2)
        self.assertEqual(pages[-1],101)
        self.assertNotIn('ranking_scan',c.source_data)

    def test_luogu_failure_retains_both_discovery_sources(self):
        from .contest_progress_sources import SyncError
        remote=Mock();remote.json.side_effect=[{'data':{'elo':[{'contest':{'id':1}}]}},
            {'data':{'contest':{'name':'One','startTime':1,'endTime':2}}},
            {'data':{'contest':{'name':'Two','startTime':1,'endTime':2}}}]
        def partial(*args,**kwargs):
            kwargs['discovered'].add('2');raise SyncError('expired cookie')
        with patch('club.contest_progress_sources.configure_luogu',return_value='cookie'),patch('club.platform_sync.luogu',return_value={'profile_url':'https://www.luogu.com.cn/user/1'}),patch('club.contest_progress_sources.luogu_ac_times',side_effect=partial):
            rows=discover(remote,'luogu','1')
        self.assertEqual([r['contest_key'] for r in rows],['1','2'])
        self.assertIn('expired cookie',rows.warning)

    def test_luogu_scan_reserves_metadata_time_and_resumes_pending_work(self):
        from django.core.cache.backends.locmem import LocMemCache
        from .platform_sync import SyncError

        remote = Mock(deadline=140, evidence_user_id=15)
        checkpoint_cache = LocMemCache(self.id(), {})
        self.addCleanup(checkpoint_cache.clear)
        clock = 0
        pages = []
        metadata = []
        def response(url, **kwargs):
            nonlocal clock
            if '/user/' in url:
                return {'data': {'elo': [{'contest': {'id': 1}}]}}
            if '/record/list?' in url:
                page = int(url.rsplit('=', 1)[-1]); pages.append(page)
                if len(pages) == 1:
                    self.assertEqual(remote.deadline, 45)
                    return {'data': {'records': {'result': [{'id': 1, 'user': {'uid': 9},
                        'contest': {'id': 2}, 'submitTime': REWARD_START.timestamp() + 10}]}}}
                if len(pages) == 2:
                    clock = remote.deadline
                    raise SyncError('等待请求额度超时')
                return {'data': {'records': {'result': []}}}
            key = url.rsplit('/', 1)[-1]; metadata.append(key)
            self.assertEqual(remote.deadline, 140 if len(pages) == 2 else 200)
            if key == '2' and len(pages) == 2:
                raise SyncError('详情暂不可用')
            return {'data': {'contest': {'name': key, 'startTime': 1, 'endTime': 2}}}

        remote.json.side_effect = response
        with patch('club.contest_progress_sources.configure_luogu', return_value='_uid=7; __client_id=test'), \
             patch('club.platform_sync.luogu', return_value={'profile_url': 'https://www.luogu.com.cn/user/9'}), \
             patch('club.contest_progress_sources.caches', {'platform_requests': checkpoint_cache}), \
             patch('club.contest_progress_sources.time.monotonic', side_effect=lambda: clock), \
             patch('club.contest_progress_sources.time.sleep'):
            partial = discover(remote, 'luogu', '9')
            self.assertEqual([row['contest_key'] for row in partial], ['1'])
            self.assertTrue(partial.warning)
            remote.deadline = 200
            complete = discover(remote, 'luogu', '9')
        self.assertEqual(pages, [1, 2, 2])
        self.assertEqual(metadata, ['1', '2', '1', '2'])
        self.assertEqual([row['contest_key'] for row in complete], ['1', '2'])
        self.assertFalse(complete.warning)
        self.assertIsNone(checkpoint_cache.get('luogu-discovery:v1:15:9'))
