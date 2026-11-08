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
    def test_nowcoder_long_ac_scan_preserves_progress_and_shares_complete_result(self):
        from urllib.parse import parse_qs, urlsplit
        from django.core.cache.backends.locmem import LocMemCache
        from .contest_progress_sources import ContinueHydration, SyncError

        backend = LocMemCache(self.id(), {})
        self.addCleanup(backend.clear)
        start = REWARD_START + timedelta(hours=10)
        contest = NS(platform='nowcoder', account='9', contest_key='42',
                     url='https://ac.nowcoder.com/acm/contest/42', starts_at=start,
                     ends_at=start + timedelta(hours=1))
        clock = 0
        pages = []
        calls = []
        remote = Mock(deadline=140)

        def metadata(url, **kwargs):
            nonlocal clock
            self.assertEqual(remote.deadline, 140)
            clock += 2
            if '/problem-list' in url:
                calls.append('problems')
                return {'data': {'data': [{'problemId': 1, 'index': 'A'}, {'problemId': 80, 'index': 'B'}]}}
            calls.append('ratings')
            return {'code': 0, 'data': {'papers': []}}

        def history(url, **kwargs):
            nonlocal clock
            self.assertEqual(calls[0], 'problems')
            self.assertLessEqual(remote.deadline, 47)
            self.assertLess(clock, remote.deadline)
            clock += 2
            page = int(parse_qs(urlsplit(url).query)['page'][0])
            pages.append(page)
            if page == 16 and pages.count(16) == 1:
                raise SyncError('temporary failure')
            day = '2026-11-01 10:05:00' if page == 80 else '2026-11-01 20:00:00'
            next_link = '<a rel="next" href="?page=2">Next</a>' if page < 80 else ''
            return ('<script>window.curUser.id="9";</script><table><tr>'
                    f'<td></td><td><a href="/acm/problem/{page}">Problem</a></td>'
                    f'<td>Accepted</td><td>{day}</td></tr></table>{next_link}')

        remote.json.side_effect = metadata
        remote.text.side_effect = history
        with patch('club.contest_progress_sources.caches', {'platform_requests': backend}), \
             patch('club.contest_progress_sources.cache', backend), \
             patch('club.public_catalog.cache', backend), \
             patch('club.contest_progress_sources.time.monotonic', side_effect=lambda: clock):
            with self.assertRaises(ContinueHydration):
                hydrate(remote, contest)
            self.assertEqual(backend.get('nc-accepted:v3:9')['next_page'], 16)
            clock = 0
            with self.assertRaisesMessage(SyncError, 'temporary failure'):
                hydrate(remote, contest)
            self.assertEqual(backend.get('nc-accepted:v3:9')['next_page'], 16)
            for _ in range(4):
                clock = 0
                with self.assertRaises(ContinueHydration):
                    hydrate(remote, contest)
            clock = 0
            rows, error = hydrate(remote, contest)
            self.assertEqual(calls[-1], 'ratings')
            self.assertFalse(error)
            self.assertEqual([row['official_ac'] for row in rows], [False, True])
            self.assertTrue(rows[0]['accepted'])
            clock = 0
            contest.contest_key = '43'
            hydrate(remote, contest)
        self.assertEqual(pages, [*range(1, 17), *range(16, 81)])

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

    def test_leetcode_empty_rank_hint_checks_neighbors_and_resumes_failed_hint(self):
        from django.core.cache.backends.locmem import LocMemCache
        from .platform_sync import SyncError

        contest = NS(platform='leetcode', account='reader', contest_key='weekly-contest-1',
                     starts_at=REWARD_START, ends_at=REWARD_START+timedelta(hours=1),
                     source_data={'rank': 26})
        remote = Mock()
        pages = []

        def response(url, **kwargs):
            if '/info/' in url:
                return {'contest': {'start_time': REWARD_START.timestamp(), 'duration': 3600},
                        'questions': [{'question_id': 1, 'title_slug': 'one', 'title': 'One'}]}
            page = int(url.split('pagination=')[1].split('&')[0])
            pages.append(page)
            if page == 2:
                return {'total_rank': [], 'submissions': []}
            if pages.count(1) == 1:
                raise SyncError('temporary failure')
            return {'total_rank': [{'user_slug': 'reader', 'data_region': 'US'}],
                    'submissions': [{'1': {'status': 10, 'date': REWARD_START.timestamp()}}]}

        remote.json.side_effect = response
        backend = LocMemCache(self.id(), {})
        self.addCleanup(backend.clear)
        with patch('club.contest_progress_sources.cache.get', return_value={}), \
             patch('club.public_catalog.cache', backend):
            with self.assertRaisesMessage(SyncError, 'temporary failure'):
                hydrate(remote, contest)
            self.assertEqual(contest.source_data['ranking_scan'], {'hint_pages': [1, 3]})
            rows, error = hydrate(remote, contest)
        self.assertEqual(pages, [2, 1, 1])
        self.assertTrue(rows[0]['official_ac'])
        self.assertFalse(error)
        self.assertNotIn('ranking_scan', contest.source_data)

    def test_leetcode_empty_hint_falls_back_to_sequential_eof(self):
        from django.core.cache.backends.locmem import LocMemCache
        from .contest_progress_sources import ContinueHydration, SyncError

        contest = NS(platform='leetcode', account='reader', contest_key='weekly-contest-1',
                     starts_at=REWARD_START, ends_at=REWARD_START+timedelta(hours=1),
                     source_data={'rank': 126})
        remote = Mock()
        pages = []

        def response(url, **kwargs):
            if '/info/' in url:
                return {'contest': {'start_time': REWARD_START.timestamp(), 'duration': 3600}}
            pages.append(int(url.split('pagination=')[1].split('&')[0]))
            return {'total_rank': [], 'submissions': []}

        remote.json.side_effect = response
        backend = LocMemCache(self.id(), {})
        self.addCleanup(backend.clear)
        with patch('club.public_catalog.cache', backend):
            with self.assertRaises(ContinueHydration):
                hydrate(remote, contest)
            self.assertEqual(contest.source_data['ranking_scan'], {'next_page': 1})
            with self.assertRaisesMessage(SyncError, '已查完正式榜单'):
                hydrate(remote, contest)
        self.assertEqual(pages, [6, 5, 7, 1])
        self.assertNotIn('ranking_scan', contest.source_data)

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
        self.assertEqual(metadata, ['1', '2', '2'])
        self.assertEqual([row['contest_key'] for row in complete], ['1', '2'])
        self.assertFalse(complete.warning)
        self.assertIsNone(checkpoint_cache.get('luogu-discovery:v1:15:9'))

    def test_luogu_rated_metadata_rotates_pending_keys_without_cookie(self):
        from django.core.cache.backends.locmem import LocMemCache
        from .platform_sync import SyncError

        backend = LocMemCache(self.id(), {})
        self.addCleanup(backend.clear)
        remote = Mock(deadline=140, evidence_user_id=15)
        clock = 0
        metadata = []
        rated_keys = [str(key) for key in range(100, 180)]

        def response(url, **kwargs):
            nonlocal clock
            self.assertLess(clock, remote.deadline)
            clock += 2
            if '/user/' in url:
                return {'data': {'elo': [{'contest': {'id': key}} for key in rated_keys]}}
            key = url.rsplit('/', 1)[-1]
            metadata.append(key)
            if len(metadata) == 1:
                raise SyncError('temporary failure')
            return {'data': {'contest': {'name': key, 'startTime': 1, 'endTime': 2}}}

        remote.json.side_effect = response
        with patch('club.contest_progress_sources.configure_luogu', return_value=''), \
             patch('club.platform_sync.luogu', return_value={'profile_url': 'https://www.luogu.com.cn/user/9'}), \
             patch('club.contest_progress_sources.caches', {'platform_requests': backend}), \
             patch('club.contest_progress_sources.time.monotonic', side_effect=lambda: clock):
            first = discover(remote, 'luogu', '9')
            self.assertTrue(first.warning)
            clock = 0
            second = discover(remote, 'luogu', '9')
        self.assertEqual(metadata, [*rated_keys, rated_keys[0]])
        self.assertEqual(len(first), 44)
        self.assertCountEqual([row['contest_key'] for row in second], rated_keys)
        self.assertFalse(second.warning)
        self.assertIsNone(backend.get('luogu-discovery:v1:15:9'))
