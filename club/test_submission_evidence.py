from datetime import timedelta
from unittest.mock import Mock,patch
from django.core.cache.backends.locmem import LocMemCache
from django.test import TestCase
from django.utils import timezone
from . import test_community as fixtures
from .models import PlatformBinding,ContestParticipation,PlatformEvidenceCoverage,PlatformSubmissionEvidence
from .submission_evidence import normalize,store_page,finish_coverage,contest_rows
from .contest_progress_sources import hydrate

class SubmissionEvidenceTests(TestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        isolated_cache = LocMemCache(self.id(), {})
        self.addCleanup(isolated_cache.clear)
        self.enterContext(patch('club.public_catalog.cache', isolated_cache))
        self.enterContext(patch('club.contest_progress_sources.caches', {'platform_requests': isolated_cache}))
        self.binding=PlatformBinding.objects.create(user=self.author,platform='atcoder',account='reader')
        self.now=timezone.now()
        self.contest=ContestParticipation.objects.create(user=self.author,platform='atcoder',account='reader',contest_key='abc1',title='ABC',url='https://atcoder.jp/contests/abc1',starts_at=self.now-timedelta(hours=2),ends_at=self.now-timedelta(hours=1))
        self.row={'id':1,'user_id':'reader','problem_id':'abc1_a','contest_id':'abc1','epoch_second':(self.now-timedelta(minutes=90)).timestamp(),'result':'AC'}

    def test_partial_unknown_and_stale_coverage_never_certify(self):
        data,valid=normalize('atcoder','reader',[self.row]);store_page(self.binding,data)
        self.assertIsNone(contest_rows(self.contest))
        finish_coverage(self.binding,{'full':True,'evidence_valid':False,'evidence_until':self.now.timestamp()})
        self.assertFalse(PlatformEvidenceCoverage.objects.exists())
        finish_coverage(self.binding,{'full':True,'evidence_valid':True,'evidence_until':self.now.timestamp()})
        self.assertEqual(len(contest_rows(self.contest)),1)
        PlatformEvidenceCoverage.objects.update(checked_at=self.now-timedelta(hours=1))
        self.assertIsNone(contest_rows(self.contest))

    def test_rejudge_updates_same_submission_without_duplicates(self):
        for verdict in ['AC','WA']:
            rows,_=normalize('atcoder','reader',[dict(self.row,result=verdict)])
            store_page(self.binding,rows)
        self.assertEqual(PlatformSubmissionEvidence.objects.count(),1)
        self.assertEqual(PlatformSubmissionEvidence.objects.get().verdict,'WA')

    def test_hydration_reuses_precise_ac_without_submission_request(self):
        rows,_=normalize('atcoder','reader',[self.row]);store_page(self.binding,rows)
        finish_coverage(self.binding,{'full':True,'evidence_valid':True,'evidence_until':self.now.timestamp()})
        remote=Mock();remote.text.return_value='<table><tbody><tr><td><a href="/contests/abc1/tasks/abc1_a">A</a></td><td>Task</td></tr></tbody></table>'
        with patch('club.contest_progress_sources.cached',return_value={}):
            results,error=hydrate(remote,self.contest)
        self.assertTrue(results[0]['accepted']);self.assertEqual(error,'')
        remote.json.assert_not_called()

    def test_changed_account_and_pending_verdict_force_remote_check(self):
        rows,_=normalize('atcoder','reader',[dict(self.row,result='WJ')]);store_page(self.binding,rows)
        finish_coverage(self.binding,{'full':True,'evidence_valid':True,'evidence_until':self.now.timestamp()})
        self.assertIsNone(contest_rows(self.contest))
        self.binding.account='other';self.binding.save()
        self.assertIsNone(contest_rows(self.contest))

    def test_mismatched_account_rejected(self):
        from .platform_sync import SyncError
        with self.assertRaises(SyncError):normalize('atcoder','reader',[dict(self.row,user_id='other')])

    def test_discovery_reuses_history_but_fetches_uncovered_tail(self):
        from .contest_progress_sources import discover
        rows,_=normalize('atcoder','reader',[self.row]);store_page(self.binding,rows)
        finish_coverage(self.binding,{'full':True,'evidence_valid':True,'evidence_until':self.now.timestamp()})
        remote=Mock();remote.evidence_user_id=self.author.pk;remote.json.side_effect=[[],[]]
        catalog=[{'id':'abc1','title':'ABC','start_epoch_second':self.contest.starts_at.timestamp(),'duration_second':3600}]
        with patch('club.contest_progress_sources.cached',return_value=catalog):
            rows=discover(remote,'atcoder','reader')
        self.assertEqual([r['contest_key'] for r in rows],['abc1'])
        self.assertIn('from_second='+str(int(self.now.timestamp()-600)),remote.json.call_args.args[0])

    def test_codeforces_reuses_window_with_known_problem_catalog(self):
        self.binding.platform='codeforces';self.binding.save()
        self.contest.platform='codeforces';self.contest.contest_key='1';self.contest.save()
        row={'id':42,'author':{'members':[{'handle':'reader'}]},'contestId':1,'problem':{'contestId':1,'index':'A'},'creationTimeSeconds':self.row['epoch_second'],'verdict':'OK'}
        rows,valid=normalize('codeforces','reader',[row]);self.assertTrue(valid);store_page(self.binding,rows)
        finish_coverage(self.binding,{'full':True,'evidence_valid':True,'evidence_until':self.now.timestamp()})
        from .public_catalog import resource
        resource('https://codeforces.com/api/problemset.problems', lambda:{'status':'OK','result':{'problems':[{'contestId':1,'index':'A','name':'Task'}]}})
        remote=Mock()
        results,error=hydrate(remote,self.contest)
        self.assertTrue(results[0]['accepted']);remote.json.assert_not_called()

    def test_atcoder_discovery_resumes_partial_submission_history(self):
        from .contest_progress_sources import discover
        from .contest_rewards import REWARD_START
        from .platform_sync import SyncError

        start = int(REWARD_START.timestamp())
        catalog = [
            {'id': key, 'title': key, 'start_epoch_second': start, 'duration_second': 3600}
            for key in ('rated', 'first', 'second')
        ]
        rated_history = [{'ContestScreenName': 'rated.contest.atcoder.jp'}]
        first_row = {'contest_id': 'first', 'user_id': 'reader', 'epoch_second': start + 60}
        second_row = dict(first_row, contest_id='second', epoch_second=start + 90)
        remote = Mock()
        remote.evidence_user_id = self.author.pk
        remote.json.side_effect = [
            rated_history, [first_row] * 500, SyncError('暂时不可用'),
            rated_history, [second_row],
        ]
        with patch('club.contest_progress_sources.cached', return_value=catalog), \
                patch('club.contest_progress_sources.time.sleep'):
            partial = discover(remote, 'atcoder', 'reader')
            self.assertTrue(partial.warning)
            self.assertEqual({row['contest_key'] for row in partial}, {'rated', 'first'})
            self.assertTrue(remote.json.call_args.args[0].endswith(f'from_second={start + 61}'))

            complete = discover(remote, 'atcoder', 'reader')
            self.assertEqual(complete.warning, '')
            self.assertEqual({row['contest_key'] for row in complete}, {'rated', 'first', 'second'})
            self.assertTrue(remote.json.call_args.args[0].endswith(f'from_second={start + 61}'))

    def test_atcoder_discovery_bounds_full_pages(self):
        from .contest_progress_sources import discover
        from .contest_rewards import REWARD_START

        start = int(REWARD_START.timestamp())
        remote = Mock()
        remote.evidence_user_id = self.author.pk
        remote.json.side_effect = [[], [{'epoch_second': start + 60}] * 500]
        with patch('club.contest_progress_sources.cached', return_value=[]), \
                patch('club.contest_progress_sources._ATCODER_DISCOVERY_PAGE_LIMIT', 1):
            rows = discover(remote, 'atcoder', 'reader')
        self.assertTrue(rows.warning)
        self.assertEqual(remote.json.call_count, 2)
