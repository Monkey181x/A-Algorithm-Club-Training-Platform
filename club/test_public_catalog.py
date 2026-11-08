import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from django.core.cache.backends.locmem import LocMemCache
from django.test import SimpleTestCase, TestCase
from . import public_catalog as catalog
from .problem_difficulty import normalize, publish, shared_map
from .platform_sync import SyncError


from django.test.runner import DiscoverRunner


class CatalogTestRunner(DiscoverRunner):
    """Isolate public network fixtures between tests and from runtime Redis."""
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self.cache_patch = patch.object(catalog, 'cache', LocMemCache('isolated-public-fixtures', {}))
        self.cache_patch.start()

    def teardown_test_environment(self, **kwargs):
        self.cache_patch.stop()
        super().teardown_test_environment(**kwargs)

    def get_resultclass(self):
        class Result(unittest.TextTestResult):
            def startTest(self, test):
                catalog.cache.clear()
                super().startTest(test)
        return Result


class PublicCatalogTests(SimpleTestCase):
    def setUp(self):
        self.cache = LocMemCache('catalog-tests', {})
        self.cache.clear()
        self.patch = patch.object(catalog, 'cache', self.cache)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.url = 'https://codeforces.com/api/contest.list?gym=false'
        self.data = {'status':'OK', 'result':[{'id':1}]}

    def test_calendar_and_tracker_share_snapshot(self):
        remote = Mock()
        remote.json.return_value = self.data
        self.assertEqual(catalog.PublicRemote(remote).json(self.url), self.data)
        loader = Mock(side_effect=AssertionError('second download'))
        self.assertEqual(json.loads(catalog.calendar_text(self.url, loader)), self.data)
        loader.assert_not_called()
        remote.json.assert_called_once()

    def test_failed_refresh_preserves_last_good_but_does_not_verify(self):
        with patch.object(catalog.time, 'time', return_value=1000):
            catalog.resource(self.url, lambda:self.data)
        with patch.object(catalog.time, 'time', return_value=2000):
            with self.assertRaises(SyncError):
                catalog.resource(self.url, lambda:{'status':'FAILED'})
        with patch.object(catalog.time, 'time', return_value=1001):
            self.assertEqual(catalog.resource(self.url, Mock(side_effect=AssertionError)), self.data)

    def test_account_and_region_isolation(self):
        remote = Mock()
        remote.json.return_value = []
        proxy = catalog.PublicRemote(remote)
        url = 'https://codeforces.com/api/user.status?handle=reader'
        proxy.json(url); proxy.json(url)
        self.assertEqual(remote.json.call_count, 2)
        self.assertNotEqual(catalog.canonical_url('https://leetcode.cn/contest/api/info/weekly-contest-1/'),
                            catalog.canonical_url('https://leetcode.com/contest/api/info/weekly-contest-1/'))
        self.assertIsNone(catalog.policy('https://www.luogu.com.cn/contest/1'))
        remote.credentials = {'cookie':'private'}
        remote.json.return_value = self.data
        proxy.json(self.url); proxy.json(self.url)
        self.assertEqual(remote.json.call_count, 4)

    def test_expired_schedule_is_replaced(self):
        with patch.object(catalog.time, 'time', return_value=1000):
            catalog.resource(self.url, lambda:self.data)
        changed = {'status':'OK','result':[{'id':1,'startTimeSeconds':2000}]}
        with patch.object(catalog.time, 'time', return_value=2000):
            self.assertEqual(catalog.resource(self.url, lambda:changed), changed)

    def test_concurrent_consumers_download_once(self):
        started = threading.Event()
        release = threading.Event()
        def load():
            started.set()
            self.assertTrue(release.wait(3))
            return self.data
        loader = Mock(side_effect=load)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(catalog.resource, self.url, loader)
            self.assertTrue(started.wait(3))
            second = pool.submit(catalog.resource, self.url, loader)
            release.set()
            self.assertEqual(first.result(), second.result())
        loader.assert_called_once()

    def test_nested_wrapper_does_not_lock_itself(self):
        remote = Mock(); remote.json.return_value = self.data
        self.assertEqual(catalog.PublicRemote(catalog.PublicRemote(remote)).json(self.url), self.data)
        remote.json.assert_called_once()


class DifficultyTests(TestCase):
    def test_normalize_raw_values(self):
        self.assertEqual(normalize('nowcoder', 3), 1600)
        self.assertAlmostEqual(normalize('atcoder', 0), 147.151776468577)
        self.assertIsNone(normalize('atcoder', float('nan')))
        self.assertIsNone(normalize('codeforces', True))

    def test_aliases_share_known_value_and_missing_cannot_erase(self):
        publish('nowcoder', [{'key':'problem/1', 'aliases':['contest/1/A'], 'difficulty':1600}])
        publish('nowcoder', [{'key':'problem/1', 'difficulty':None}])
        data = shared_map('nowcoder')
        self.assertEqual(data['problem/1']['difficulty'], data['contest/1/a']['difficulty'])
        self.assertTrue(data['problem/1']['difficulty_known'])
        self.assertTrue(data['problem/1']['difficulty_updated_at'])

    def test_metadata_and_reward_share_current_value(self):
        from .test_contest_progress import ProgressTests
        from .contest_rewards import reward_for
        from .ac_evidence import metadata
        from .problem_difficulty import for_problem
        ProgressTests.setUp(self)
        publish('leetcode', [{'key':'sample', 'difficulty':1600, 'difficulty_source':'test'}])
        self.assertEqual(for_problem(self.problem)['difficulty'], metadata('leetcode')['sample']['difficulty'])
        self.assertEqual(reward_for(self.problem, self.member, self.profile)[0], 3)
        publish('qoj', [{'key':'1', 'difficulty':3000}])
        self.assertEqual(shared_map('qoj'), {})

    def test_claimed_amount_does_not_change_after_rating_update(self):
        from .test_contest_progress import ProgressTests
        from .contest_rewards import claim_problems
        from .models import ContestProblemClaim
        ProgressTests.setUp(self)
        self.assertEqual(claim_problems(self.member)['total'], 1)
        publish('leetcode', [{'key':'sample', 'difficulty':1600}])
        self.assertEqual(claim_problems(self.member)['total'], 0)
        self.assertEqual(ContestProblemClaim.objects.get(user=self.user).amount, 1)

    def test_contest_preview_identity_matches_deduplicated_claim(self):
        from .test_contest_progress import ProgressTests
        from .contest_rewards import serialize_participation, claim_problems
        from .models import ContestParticipation, ContestProblemProgress
        ProgressTests.setUp(self)
        other = ContestParticipation.objects.get(pk=self.contest.pk)
        other.pk=None; other.contest_key='weekly-contest-531'; other.save()
        duplicate = ContestProblemProgress.objects.get(pk=self.problem.pk)
        duplicate.pk=None; duplicate.participation=other; duplicate.save()
        rows = [serialize_participation(c,self.member)['problems'][0] for c in (self.contest,other)]
        self.assertEqual(rows[0]['reward_key'],rows[1]['reward_key'])
        preview = {}
        for row in sorted(rows,key=lambda p:p['id']):
            if row['claimable']:preview.setdefault(tuple(row['reward_key']),row['reward'])
        self.assertEqual(sum(preview.values()),claim_problems(self.member)['total'])

    def test_reward_snapshot_reads_platform_metadata_once_for_many_problems(self):
        from .test_contest_progress import ProgressTests
        from .reward_snapshot import RewardSnapshot
        from .ac_evidence import metadata
        ProgressTests.setUp(self)
        with patch('club.reward_snapshot.metadata',wraps=metadata) as load:
            snapshot=RewardSnapshot(self.member)
            snapshot.info('leetcode')
            with self.assertNumQueries(0):
                for _ in range(100):
                    snapshot.identity(self.problem)
                    self.assertFalse(snapshot.claimed(self.problem))
            self.assertEqual(load.call_count,1)

    def test_tracker_task_publishes_normalized_difficulty(self):
        import uuid
        from .models import TrackerCatalog
        from .tracker_sources import parse_nowcoder
        from .tasks import refresh_tracker_catalog
        from .ac_evidence import metadata
        token = uuid.uuid4()
        TrackerCatalog.objects.create(kind='nowcoder',status='pending',token=token)
        event = parse_nowcoder({'id':1,'name':'Test','questions':[{'problemId':2,'title':'P','difficulty':3}]})
        with patch('club.tracker_sources.fetch', return_value=([event],{},True)), patch('club.platform_sync.Remote'):
            refresh_tracker_catalog('nowcoder',str(token))
        info = metadata('nowcoder')
        self.assertEqual(info['problem/2']['difficulty'], 1600)
        publish('nowcoder', [{'key':'contest/1/a','difficulty':2000}])
        info = metadata('nowcoder')
        self.assertEqual(info['problem/2']['difficulty'], 2000)
        self.assertEqual(info['contest/1/a']['difficulty'], 2000)
