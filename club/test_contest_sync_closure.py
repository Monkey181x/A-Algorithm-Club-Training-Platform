from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import Mock, patch

from django.db import close_old_connections
from django.test import TransactionTestCase
from django.utils import timezone

from . import test_community as fixtures
from . import contest_progress_views
from .account_views import close_user
from .models import ContestParticipation,ContestProblemProgress,ContestSyncState,PlatformBinding
from .tasks import discover_contest_progress,hydrate_contest_progress


class ContestSyncClosureTests(TransactionTestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        self.binding=PlatformBinding.objects.create(user=self.author,platform='codeforces',account='private_handle')
        self.state=ContestSyncState.objects.create(user=self.author,status='pending',
            requested_at=timezone.now(),remaining=1,total=1,messages={'private_handle':'old message'})
        self.token=str(self.state.token)
        self.contest=ContestParticipation.objects.create(user=self.author,platform='codeforces',account='private_handle',
            contest_key='existing',title='Existing contest',starts_at=timezone.now()-timedelta(hours=2),
            ends_at=timezone.now(),source_data={'account':'private_handle'},detail_complete=True,sync_error='private_handle error')
        self.proof=ContestProblemProgress.objects.create(participation=self.contest,problem_key='A',label='A',
            title='Verified proof',accepted=True,official_ac=True,accepted_at=timezone.now())

    def test_page_continuation_is_persisted_before_requeue(self):
        from .contest_progress_sources import ContinueHydration
        def next_page(remote,c):
            c.source_data['ranking_scan']={'next_page':11}
            raise ContinueHydration('continue')
        with patch('club.contest_progress_sources.hydrate',side_effect=next_page),patch('club.tasks.enqueue_hydration') as enqueue:
            hydrate_contest_progress(self.contest.pk,self.token)
        self.contest.refresh_from_db();self.state.refresh_from_db()
        self.assertEqual(self.contest.source_data['ranking_scan']['next_page'],11)
        self.assertNotEqual(str(self.contest.hydrated_token),self.token)
        self.assertEqual(self.state.remaining,1)
        enqueue.assert_called_once_with(self.contest.pk,self.token)
        with patch('club.contest_progress_sources.hydrate',return_value=([],'')):
            hydrate_contest_progress(self.contest.pk,self.token)
        self.state.refresh_from_db()
        self.assertEqual((self.state.remaining,self.state.status),(0,'ready'))

    def test_manual_link_preserves_leetcode_rank_and_cursor(self):
        self.binding.platform='leetcode';self.binding.save()
        self.contest.platform='leetcode';self.contest.source_data={'rank':9999,'ranking_scan':{'next_page':101}};self.contest.save()
        row={'contest_key':self.contest.contest_key,'title':'Same','url':'https://leetcode.com/contest/weekly-contest-1/',
             'starts_at':self.contest.starts_at,'ends_at':self.contest.ends_at,'source_data':{'manual':True}}
        with patch('club.contest_progress_sources.linked_contest',return_value=row),patch('club.tasks.enqueue_hydration'):
            discover_contest_progress(self.author.pk,self.token,'https://leetcode.com/contest/weekly-contest-1/')
        self.contest.refresh_from_db()
        self.assertEqual(self.contest.source_data['rank'],9999)
        self.assertEqual(self.contest.source_data['ranking_scan']['next_page'],101)

    def test_leetcode_link_uses_only_the_matching_region(self):
        self.binding.platform = 'leetcode'
        self.binding.account = 'reader'
        self.binding.save()
        url = 'https://leetcode.cn/contest/weekly-contest-1/'
        self.client.force_login(self.author)
        response = self.client.post('/api/contests/progress', {'contest_url': url}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        PlatformBinding.objects.create(user=self.author, platform='leetcode', account='cn:reader')
        with patch('club.contest_progress_sources.linked_contest', return_value=self.discovered_rows()[0]) as linked, \
             patch('club.tasks.enqueue_hydration') as enqueue:
            discover_contest_progress(self.author.pk, self.token, url)
        self.assertEqual(linked.call_args.args[1:], (url, 'cn:reader'))
        linked.assert_called_once()
        participation = ContestParticipation.objects.get(platform='leetcode', user=self.author)
        self.assertEqual(participation.account, 'cn:reader')
        enqueue.assert_called_once_with(participation.pk, self.token)
        with patch('club.contest_progress_sources.hydrate', return_value=([], '')):
            hydrate_contest_progress(participation.pk, self.token)
        self.state.refresh_from_db()
        self.assertEqual(self.state.status, 'ready')
        self.assertFalse(self.state.has_errors)

    def test_nowcoder_long_history_requeues_in_background_and_keeps_other_results(self):
        from urllib.parse import parse_qs, urlsplit
        from django.core.cache.backends.locmem import LocMemCache
        from .contest_progress_sources import discover
        from .platform_sync import SyncError

        binding = PlatformBinding.objects.create(user=self.author, platform='nowcoder', account='9')
        self.state.total = self.state.remaining = 0
        self.state.save(update_fields=['total', 'remaining'])
        backend = LocMemCache(self.id(), {})
        self.addCleanup(backend.clear)
        clock = 0
        pages = []
        metadata = []
        rated_requests = 0
        remote = Mock(deadline=140)
        batches = []

        def history(url):
            nonlocal clock
            self.assertLess(clock, remote.deadline)
            self.assertLessEqual(remote.deadline, 47)
            clock += 2
            page = int(parse_qs(urlsplit(url).query)['page'][0])
            pages.append(page)
            if page == 18 and pages.count(18) == 1:
                raise SyncError('temporary interruption')
            next_link = '<a rel="next" href="?page=2">Next</a>' if page < 80 else ''
            return f'<table><tr><td><a href="/acm/contest/{page}">Contest</a></td></tr></table>{next_link}'

        def contest_info(url, **kwargs):
            nonlocal clock, rated_requests
            self.assertEqual(remote.deadline, 140)
            self.assertLess(clock, remote.deadline)
            clock += 2
            if '/rating-history' in url:
                rated_requests += 1
                return {'code': 0, 'data': [{'contestId': 100}]}
            key = int(url.rsplit('=', 1)[1])
            metadata.append(key)
            if key == 1 and metadata.count(1) == 1:
                raise SyncError('temporary metadata failure')
            return {'data': {'name': str(key), 'startTime': 1793556000000, 'endTime': 1793559600000}}

        remote.text.side_effect = history
        remote.json.side_effect = contest_info
        with patch('club.contest_progress_sources.Remote', return_value=remote), \
             patch('club.contest_progress_sources.caches', {'platform_requests': backend}), \
             patch('club.public_catalog.cache', backend), \
             patch('club.contest_progress_sources.time.monotonic', side_effect=lambda: clock), \
             patch('club.contest_progress_sources.discover',
                   side_effect=lambda r, p, a: discover(r, p, a) if p == 'nowcoder' else self.discovered_rows()) as discover_mock, \
             patch('club.tasks.discover_contest_progress.apply_async',
                   side_effect=lambda **options: batches.append(options)), \
             patch('club.tasks.enqueue_hydration') as hydrate_queue:
            discover_contest_progress(self.author.pk, self.token)
            self.assertTrue(ContestParticipation.objects.filter(platform='nowcoder', contest_key='100').exists())
            for _ in range(8):
                if not batches:
                    break
                hydrate_queue.assert_not_called()
                options = batches.pop(0)
                self.assertEqual(options['kwargs']['resume_bindings'], [binding.pk])
                clock = 0
                discover_contest_progress(*options['args'], **options['kwargs'])
            self.assertFalse(batches)
        self.state.refresh_from_db()
        self.assertEqual((self.state.status, self.state.total, self.state.has_errors), ('pending', 82, False))
        self.assertEqual(hydrate_queue.call_count, 82)
        self.assertEqual(sum(call.args[1] == 'codeforces' for call in discover_mock.call_args_list), 1)
        self.assertEqual(rated_requests, 1)
        self.assertEqual(pages, [*range(1, 19), *range(18, 81)])
        self.assertCountEqual(metadata, [*range(1, 81), 100, 1])
        self.assertIsNone(backend.get(f'nowcoder-discovery:v1:{self.author.pk}:9'))

    def discovered_rows(self,*args):
        return [{'contest_key':'new-contest','title':'New contest','url':'https://codeforces.com/contest/999',
            'starts_at':timezone.now()-timedelta(hours=2),'ends_at':timezone.now(),
            'source_data':{'account':'private_handle'}}]

    def close_in_thread(self,started=None,finished=None):
        close_old_connections()
        try:
            if started:started.set()
            return close_user(self.author.pk,self.author)
        finally:
            if finished:finished.set()
            close_old_connections()

    def run_task(self,task,*args):
        close_old_connections()
        try:return task(*args)
        finally:close_old_connections()

    def assert_cancelled_and_anonymized(self):
        self.state.refresh_from_db()
        self.assertNotEqual(str(self.state.token),self.token)
        self.assertEqual((self.state.status,self.state.remaining,self.state.total,self.state.messages),('cancelled',0,0,{}))
        self.assertIsNotNone(self.state.finished_at)
        for row in ContestParticipation.objects.filter(user=self.author):
            self.assertTrue(row.account.startswith('closed:'))
            self.assertEqual((row.source_data,row.sync_error),({},''))
        self.proof.refresh_from_db();self.assertTrue(self.proof.accepted);self.assertTrue(self.proof.official_ac)

    def network_overlaps_closure(self,task,args,source_name,result):
        fetching,release=Event(),Event()
        def fetch(*args):
            fetching.set()
            self.assertTrue(release.wait(10),'Network operation was not released')
            return result()
        with patch('club.contest_progress_sources.Remote'),patch(source_name,side_effect=fetch), \
                patch('club.tasks.enqueue_hydration') as enqueue:
            with ThreadPoolExecutor(max_workers=2) as pool:
                job=pool.submit(self.run_task,task,*args)
                try:
                    self.assertTrue(fetching.wait(10),'Task did not start fetching')
                    closed=pool.submit(self.close_in_thread)
                    self.assertTrue(closed.result(timeout=5),'Closure did not commit while network call was outside locks')
                finally:release.set()
                job.result(timeout=10)
            enqueue.assert_not_called()
        self.assert_cancelled_and_anonymized()

    def test_discovery_result_fetched_before_closure_cannot_create_history_afterward(self):
        self.network_overlaps_closure(discover_contest_progress,(self.author.pk,self.token),
            'club.contest_progress_sources.discover',self.discovered_rows)
        self.assertFalse(ContestParticipation.objects.filter(user=self.author,contest_key='new-contest').exists())

    def test_discovery_already_past_binding_check_commits_before_closure_scan(self):
        writing,closing,closed=Event(),Event(),Event()
        original=ContestParticipation.objects.update_or_create
        def paused_write(*args,**kwargs):
            writing.set()
            self.assertTrue(closing.wait(10),'Closure did not try to acquire the user lock')
            self.assertFalse(closed.wait(.2),'Closure passed an in-progress discovery write')
            return original(*args,**kwargs)
        with patch('club.contest_progress_sources.Remote'), \
                patch('club.contest_progress_sources.discover',side_effect=self.discovered_rows), \
                patch.object(ContestParticipation.objects,'update_or_create',side_effect=paused_write), \
                patch('club.tasks.enqueue_hydration'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                job=pool.submit(self.run_task,discover_contest_progress,self.author.pk,self.token)
                self.assertTrue(writing.wait(10),'Discovery did not pass its binding check')
                closure=pool.submit(self.close_in_thread,closing,closed)
                job.result(timeout=10)
                self.assertTrue(closure.result(timeout=10))
        self.assertTrue(ContestParticipation.objects.filter(user=self.author,contest_key='new-contest').exists())
        self.assert_cancelled_and_anonymized()

    def test_late_hydration_cannot_restore_private_errors_or_replace_verified_progress(self):
        self.network_overlaps_closure(hydrate_contest_progress,(self.contest.pk,self.token),
            'club.contest_progress_sources.hydrate',lambda:([], 'private_handle late error'))
        self.assertEqual(ContestProblemProgress.objects.filter(participation=self.contest).count(),1)

    def test_queued_old_tasks_exit_without_remote_requests_after_closure(self):
        close_user(self.author.pk,self.author)
        with patch('club.contest_progress_sources.Remote') as remote, \
                patch('club.contest_progress_sources.discover') as discover, \
                patch('club.contest_progress_sources.hydrate') as hydrate:
            discover_contest_progress(self.author.pk,self.token)
            hydrate_contest_progress(self.contest.pk,self.token)
        remote.assert_not_called();discover.assert_not_called();hydrate.assert_not_called()
        self.assert_cancelled_and_anonymized()

    def test_active_discovery_and_hydration_still_complete_and_ignore_duplicate_jobs(self):
        with patch('club.contest_progress_sources.Remote'), \
                patch('club.contest_progress_sources.discover',side_effect=self.discovered_rows) as discover, \
                patch('club.tasks.enqueue_hydration') as enqueue:
            discover_contest_progress(self.author.pk,self.token)
            row=ContestParticipation.objects.get(user=self.author,contest_key='new-contest')
            enqueue.assert_called_once_with(row.pk,self.token)
            self.state.refresh_from_db();self.assertEqual((self.state.status,self.state.remaining),('pending',1))
            with patch('club.contest_progress_sources.hydrate',return_value=([
                    {'problem_key':'A','label':'A','title':'Accepted','accepted':True,'official_ac':True}],'')):
                hydrate_contest_progress(row.pk,self.token)
            self.state.refresh_from_db();self.assertEqual((self.state.status,self.state.remaining),('ready',0))
            self.assertTrue(ContestProblemProgress.objects.filter(participation=row,accepted=True).exists())
            discover_contest_progress(self.author.pk,self.token)
            discover.assert_called_once()

    def test_stale_sync_request_cannot_restart_sync_after_closure(self):
        original=contest_progress_views.membership_for
        def stale_member(request):
            member=original(request)
            close_user(self.author.pk,self.author)
            return member
        with patch.object(contest_progress_views,'membership_for',side_effect=stale_member), \
                patch('club.contest_progress_views.enqueue') as enqueue:
            response=self.client.post('/api/contests/progress')
        self.assertEqual(response.status_code,403,response.content)
        enqueue.assert_not_called()
        self.assert_cancelled_and_anonymized()
