from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import Mock, patch
from django.test import TestCase
from . import test_community as fixtures
from .models import PlatformBinding,ContestSyncState,CurrencyTransaction
from .tasks import check_contests_nightly

class NightlyContestTests(TestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        self.binding=PlatformBinding.objects.create(user=self.author,platform='atcoder',account='reader')
        self.now=datetime(2026,11,8,4,0,tzinfo=ZoneInfo('Asia/Shanghai'))
        self.clock=patch('club.tasks.timezone.now',return_value=self.now)
        self.clock_mock=self.clock.start();self.addCleanup(self.clock.stop)
        self.dispatcher=patch('club.tasks.check_contests_nightly.apply_async').start()
        self.addCleanup(patch.stopall)

    def test_before_four_does_nothing(self):
        with patch('club.tasks.timezone.now',return_value=self.now-timedelta(seconds=1)):
            self.assertEqual(check_contests_nightly()['status'],'before_schedule')
        self.assertFalse(ContestSyncState.objects.exists())

    def test_remote_failure_retries_with_backoff_and_daily_cap(self):
        from .tasks import discover_contest_progress
        from .platform_sync import SyncError
        for attempt in range(1,4):
            self.assertEqual(check_contests_nightly()['status'],'started')
            state=ContestSyncState.objects.get(user=self.author)
            self.assertEqual(state.auto_attempts,attempt)
            with patch('club.contest_progress_sources.discover',side_effect=SyncError('temporary timeout')):
                discover_contest_progress(self.author.pk,str(state.token))
            state.refresh_from_db()
            self.assertEqual(state.status,'failed')
            self.assertIsNone(state.auto_finished_at)
            self.assertEqual(check_contests_nightly()['status'],'complete')
            if attempt < 3:
                self.assertIsNotNone(state.auto_retry_at)
                self.clock_mock.return_value=state.auto_retry_at
        self.assertIsNone(state.auto_retry_at)

    def test_hydration_error_keeps_failure_until_final_task(self):
        from .tasks import finish_contest_sync
        state=ContestSyncState(user=self.author,has_errors=True,auto_attempts=1)
        state.auto_token=state.token
        finish_contest_sync(state)
        self.assertEqual(state.status,'failed')
        self.assertIsNone(state.auto_finished_at)
        self.assertIsNotNone(state.auto_retry_at)

    def test_daily_once_no_coins_and_next_day(self):
        initial=CurrencyTransaction.objects.count()
        with patch('club.tasks.discover_contest_progress.apply_async') as dispatch,self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(check_contests_nightly()['status'],'started')
        dispatch.assert_called_once()
        state=ContestSyncState.objects.get(user=self.author)
        state.status='ready';state.save()
        self.assertEqual(check_contests_nightly()['status'],'complete')
        self.assertEqual(CurrencyTransaction.objects.count(),initial)
        with patch('club.tasks.timezone.now',return_value=self.now+timedelta(days=1)):
            self.assertEqual(check_contests_nightly()['status'],'started')

    def test_manual_job_waits_without_consuming_daily_marker(self):
        ContestSyncState.objects.create(user=self.author,status='pending',requested_at=self.now,progressed_at=self.now)
        self.assertEqual(check_contests_nightly()['status'],'waiting')
        self.assertIsNone(ContestSyncState.objects.get(user=self.author).auto_checked_on)

    def test_recovery_after_four_and_queue_failure(self):
        with patch('club.tasks.timezone.now',return_value=self.now+timedelta(hours=6)),patch('club.tasks.discover_contest_progress.apply_async',side_effect=RuntimeError()),self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(check_contests_nightly()['status'],'started')
        state=ContestSyncState.objects.get(user=self.author)
        self.assertEqual(state.auto_checked_on,self.now.date())
        self.assertEqual(state.status,'failed')
        self.assertEqual(state.auto_attempts,1)
        self.assertEqual(check_contests_nightly()['status'],'complete')
        self.clock_mock.return_value=state.auto_retry_at
        self.assertEqual(check_contests_nightly()['status'],'started')
        state.refresh_from_db();self.assertEqual(state.auto_attempts,2)

    def test_qoj_and_inactive_are_excluded(self):
        self.binding.platform='qoj';self.binding.save()
        self.assertEqual(check_contests_nightly()['status'],'complete')
        self.binding.platform='atcoder';self.binding.save()
        self.author.team_memberships.update(is_active=False)
        self.assertEqual(check_contests_nightly()['status'],'complete')

    def test_stopped_worker_resumes_today(self):
        state=ContestSyncState.objects.create(user=self.author,status='pending',requested_at=self.now-timedelta(hours=1),progressed_at=self.now-timedelta(hours=1),auto_checked_on=self.now.date())
        state.auto_token=state.token;state.auto_attempts=1;state.save()
        old=state.token
        self.assertEqual(check_contests_nightly()['status'],'complete')
        state.refresh_from_db();self.assertEqual(state.status,'failed')
        self.clock_mock.return_value=state.auto_retry_at
        self.assertEqual(check_contests_nightly()['status'],'started')
        state.refresh_from_db();self.assertNotEqual(old,state.token)
        self.assertEqual(state.auto_attempts,2)

    def test_stalled_third_attempt_cannot_reset_daily_budget(self):
        state=ContestSyncState.objects.create(user=self.author,status='pending',auto_checked_on=self.now.date(),auto_attempts=3,
            requested_at=self.now-timedelta(hours=1),progressed_at=self.now-timedelta(hours=1))
        state.auto_token=state.token;state.save()
        self.assertEqual(check_contests_nightly()['status'],'complete')
        state.refresh_from_db()
        self.assertEqual(state.auto_attempts,3)
        self.assertEqual(state.auto_checked_on,self.now.date())
        self.assertIsNone(state.auto_retry_at)
        self.clock_mock.return_value=self.now+timedelta(days=1)
        self.assertEqual(check_contests_nightly()['status'],'started')
        state.refresh_from_db();self.assertEqual(state.auto_attempts,1)

    def test_hydration_enqueue_failure_schedules_retry(self):
        from .models import ContestParticipation
        from .tasks import enqueue_hydration
        check_contests_nightly()
        state=ContestSyncState.objects.get(user=self.author)
        c=ContestParticipation.objects.create(user=self.author,platform='atcoder',account='reader',contest_key='probe',
            title='Probe',url='https://atcoder.jp/contests/probe',starts_at=self.now-timedelta(hours=2),ends_at=self.now-timedelta(hours=1))
        with patch('club.tasks.hydrate_contest_progress.apply_async',side_effect=RuntimeError('queue unavailable')):
            enqueue_hydration(c.pk,str(state.token))
        state.refresh_from_db()
        self.assertEqual(state.status,'failed')
        self.assertEqual(state.auto_retry_at,self.now+timedelta(minutes=5))
        self.clock_mock.return_value=state.auto_retry_at
        self.assertEqual(check_contests_nightly()['status'],'started')

    def test_luogu_hydration_resumes_long_history_after_nightly_retry(self):
        from .contest_progress_sources import header
        from .models import ContestParticipation, ContestProblemProgress
        from .platform_sync import SyncError
        from .tasks import discover_contest_progress, hydrate_contest_progress

        self.binding.platform = 'luogu'
        self.binding.save()
        start = self.now - timedelta(hours=2)
        end = start + timedelta(hours=1)
        url = 'https://www.luogu.com.cn/contest/42'
        pages = []
        calls = []
        clock = 0
        remote = Mock(deadline=140)
        fail_page = True

        def response(request_url, **kwargs):
            nonlocal clock
            self.assertLess(clock, remote.deadline)
            clock += 2
            if request_url == url:
                calls.append('metadata')
                self.assertEqual(remote.deadline, 140)
                return {'data': {'problems': [{'pid': pid} for pid in ('P1', 'P2')]}}
            if request_url.endswith('/practice'):
                return {'data': {'passed': ['P1', 'P2']}}
            calls.append('records')
            page = int(request_url.rsplit('=', 1)[-1])
            pages.append(page)
            self.assertLess(remote.deadline, 140)
            if page == 16 and fail_page:
                raise SyncError('temporary failure')
            rows = []
            if page <= 80:
                submitted_at = {1: end.timestamp(), 80: start.timestamp()}.get(page, end.timestamp() + 60)
                rows = [{'id': page, 'user': {'uid': 9}, 'status': 12, 'submitTime': submitted_at,
                         'problem': {'pid': 'P1' if page == 1 else 'P2'}, 'contest': {'id': 42}}]
            return {'data': {'records': {'result': rows}}}

        def run_hydration(token):
            nonlocal clock
            clock = 0
            calls.clear()
            hydrate_contest_progress(contest.pk, token)
            self.assertEqual(calls[0], 'metadata')
            self.assertEqual(remote.deadline, 140)

        remote.json.side_effect = response
        with patch('club.contest_progress_sources.Remote', return_value=remote), \
             patch('club.contest_progress_sources.discover', side_effect=lambda *args: [
                 header('42', 'Probe', url, start.timestamp(), end.timestamp())]), \
             patch('club.contest_progress_sources.configure_luogu', return_value='_uid=7; __client_id=test'), \
             patch('club.platform_sync.luogu', return_value={'profile_url': 'https://www.luogu.com.cn/user/9'}), \
             patch('club.contest_progress_sources.time.monotonic', side_effect=lambda: clock), \
             patch('club.contest_progress_sources.time.sleep'), \
             patch('club.tasks.enqueue_hydration') as enqueue, self.captureOnCommitCallbacks(execute=True):
            check_contests_nightly()
            state = ContestSyncState.objects.get(user=self.author)
            token = str(state.token)
            discover_contest_progress(self.author.pk, token)
            contest = ContestParticipation.objects.get(user=self.author)
            run_hydration(token)
            contest.refresh_from_db()
            self.assertEqual(contest.source_data['luogu_scan']['next_page'], 16)
            run_hydration(token)
            state.refresh_from_db()
            self.assertEqual(state.status, 'failed')
            self.assertIsNotNone(state.auto_retry_at)
            self.clock_mock.return_value = state.auto_retry_at
            fail_page = False
            check_contests_nightly()
            state.refresh_from_db()
            token = str(state.token)
            discover_contest_progress(self.author.pk, token)
            for _ in range(5):
                run_hydration(token)

        self.assertEqual(pages, [*range(1, 17), *range(16, 82)])
        self.assertEqual(enqueue.call_count, 7)
        state.refresh_from_db()
        contest.refresh_from_db()
        self.assertEqual(state.status, 'ready')
        self.assertTrue(contest.detail_complete)
        self.assertNotIn('luogu_scan', contest.source_data)
        progress = ContestProblemProgress.objects.filter(participation=contest).order_by('problem_key')
        self.assertEqual(list(progress.values_list('accepted_at', 'official_ac')), [(end, False), (start, True)])

    def test_late_failure_and_heartbeat_cannot_overwrite_new_run(self):
        import uuid
        from .tasks import fail_contest_sync
        check_contests_nightly()
        state=ContestSyncState.objects.get(user=self.author)
        fail_contest_sync(self.author.pk,uuid.uuid4(),'old callback')
        fail_contest_sync(self.author.pk,state.token,'stale scan',stale_before=self.now-timedelta(minutes=30))
        state.refresh_from_db();self.assertEqual(state.status,'pending')
        state.status='ready';state.save()
        fail_contest_sync(self.author.pk,state.token,'late callback')
        state.refresh_from_db();self.assertEqual(state.status,'ready')

    def test_other_user_is_not_blocked_by_manual_sync(self):
        from django.contrib.auth.models import User
        from .models import TeamMembership
        other=User.objects.create_user('nightly-other')
        TeamMembership.objects.create(user=other,team=self.team,display_name='Other')
        PlatformBinding.objects.create(user=other,platform='atcoder',account='other')
        ContestSyncState.objects.create(user=self.author,status='pending',requested_at=self.now,progressed_at=self.now)
        result=check_contests_nightly()
        self.assertEqual(result,{'status':'started','user_id':other.pk})
        self.assertIsNone(ContestSyncState.objects.get(user=self.author).auto_checked_on)

    def test_three_active_chains_limit_admission(self):
        from django.contrib.auth.models import User
        for i in range(3):
            u=User.objects.create_user('busy-'+str(i))
            ContestSyncState.objects.create(user=u,status='pending',requested_at=self.now,progressed_at=self.now)
        self.assertEqual(check_contests_nightly()['status'],'waiting')
        self.assertFalse(ContestSyncState.objects.filter(user=self.author).exists())
