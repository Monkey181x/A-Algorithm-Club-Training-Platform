import json
from datetime import date, timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.test import TestCase, SimpleTestCase
from django.utils import timezone

from . import test_community as fixtures
from .contest_rewards import REWARD_START
from .models import (PlatformBinding, PlatformSolvedBaseline, ContestParticipation, ContestProblemProgress,
    Submission, SubmissionPart, CurrencyTransaction)
from .platform_solved import collect
from .solved import lifetime_solved_counts, semester_solved_counts
from .tasks import sync_platform_binding


class BaselineTests(TestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        self.clock=patch('django.utils.timezone.now',return_value=REWARD_START+timedelta(days=9))
        self.clock.start();self.addCleanup(self.clock.stop)
        self.binding=PlatformBinding.objects.create(user=self.author,platform='codeforces',account='author')

    def baseline(self,binding=None,total=10,dates=None):
        return PlatformSolvedBaseline.objects.create(binding=binding or self.binding,total=total,ids_complete=True,
            problem_ids=['1:a'],dated_problems=dates or {'1:a':'2026-11-02'},captured_at=REWARD_START+timedelta(days=4))

    def contest_problem(self,key,day,problem='A',accepted_at=True):
        start=REWARD_START+timedelta(days=day)
        contest=ContestParticipation.objects.create(user=self.author,platform='codeforces',account='author',
            contest_key=key,title=key,starts_at=start,ends_at=start+timedelta(hours=2))
        return ContestProblemProgress.objects.create(participation=contest,problem_key=problem,label=problem,
            accepted=True,official_ac=True,accepted_at=start+timedelta(minutes=1) if accepted_at else None)

    def test_four_sources_add_without_readding_baseline_contests_and_heatmap_matches_dated_only(self):
        self.baseline()
        lc=PlatformBinding.objects.create(user=self.author,platform='leetcode',account='cn:author')
        PlatformSolvedBaseline.objects.create(binding=lc,total=7,problem_ids=['slug'],dated_problems={'slug':'2026-10-01'},
            captured_at=REWARD_START+timedelta(days=4))
        self.contest_problem('old',0) # Old history discovered after binding is not an increment.
        self.contest_problem('2',7)
        self.contest_problem('unknown',8,accepted_at=False)
        from .models import Problem
        problem=Problem.objects.create(identity='site:test',url='https://example.com/site',title='Site')
        item=Submission.objects.create(team=self.team,member=self.author_member,semester=self.semester,
            week=self.week,problem=problem,record_date=date(2026,11,2))
        SubmissionPart.objects.create(submission=item,kind='proof',status='approved')
        self.assertEqual(lifetime_solved_counts([self.author.pk])[self.author.pk],19)
        self.assertEqual(semester_solved_counts([self.author.pk],self.semester)[self.author.pk],3)
        data=self.client.get('/api/training-activity?start=2026-10-01&end=2026-11-10').json()
        self.assertEqual(data['ac']['total'],19)
        self.assertEqual(data['ac']['undated'],15)
        self.assertEqual(data['ac']['rangeTotal'],4)
        self.assertEqual(sum(day['ac'] for day in data['days']),4)
        self.assertEqual(next(day['ac'] for day in data['days'] if day['date']=='2026-11-02'),2)
        self.assertFalse(CurrencyTransaction.objects.filter(member=self.author_member).exists())

    def test_successful_baseline_is_not_refetched_and_old_tasks_cannot_overwrite_new_accounts(self):
        self.binding.status='pending';self.binding.save()
        payload={'rating':100,'_solved_baseline':{'total':9,'problem_ids':['1:a'],
            'dated_problems':{'1:a':'2026-11-02'},'ids_complete':True}}
        with patch('club.platform_sync.fetch_snapshot',return_value=payload):
            sync_platform_binding(self.binding.pk,str(self.binding.sync_token))
        base=PlatformSolvedBaseline.objects.get(binding=self.binding)
        self.assertEqual(base.total,9)
        self.binding.refresh_from_db();self.assertNotIn('_solved_baseline',self.binding.snapshot)
        self.binding.status='pending';self.binding.save()
        with patch('club.platform_sync.fetch_snapshot',return_value={'rating':120}) as fetch:
            sync_platform_binding(self.binding.pk,str(self.binding.sync_token))
            self.assertFalse(fetch.call_args.kwargs['include_solved'])
        base.refresh_from_db();self.assertEqual(base.total,9)
        self.binding.refresh_from_db();self.assertEqual(self.binding.snapshot['solved'],9)
        old_token=str(self.binding.sync_token)
        with patch('club.tasks.sync_platform_binding.delay'),self.captureOnCommitCallbacks(execute=True):
            response=self.client.put('/api/profile/platforms/codeforces',json.dumps({'account':'different'}),content_type='application/json')
        self.assertEqual(response.status_code,202)
        self.assertFalse(PlatformSolvedBaseline.objects.filter(binding=self.binding).exists())
        with patch('club.platform_sync.fetch_snapshot') as fetch:
            sync_platform_binding(self.binding.pk,old_token)
            fetch.assert_not_called()

    def test_existing_binding_check_is_previewed_then_queued_once(self):
        output=StringIO();call_command('refresh_bound_solved',stdout=output)
        self.assertIn('eligible=1',output.getvalue())
        with patch('club.tasks.sync_platform_binding.delay') as dispatch,self.captureOnCommitCallbacks(execute=True):
            call_command('refresh_bound_solved',apply=True,stdout=StringIO())
        dispatch.assert_called_once()
        with patch('club.tasks.sync_platform_binding.delay') as dispatch,self.captureOnCommitCallbacks(execute=True):
            call_command('refresh_bound_solved',apply=True,stdout=StringIO())
        dispatch.assert_not_called()

    def test_incomplete_baseline_retains_failure_without_inventing_zero_and_can_retry(self):
        self.binding.status='pending';self.binding.save()
        with patch('club.platform_sync.fetch_snapshot',return_value={'rating':100,'_solved_baseline':{'error':'blocked'}}):
            sync_platform_binding(self.binding.pk,str(self.binding.sync_token))
        base=PlatformSolvedBaseline.objects.get(binding=self.binding)
        self.assertIsNone(base.total);self.assertEqual(base.error,'blocked')
        output=StringIO();call_command('refresh_bound_solved',stdout=output)
        self.assertIn('eligible=0',output.getvalue())
        output=StringIO();call_command('refresh_bound_solved',retry_failed=True,stdout=output)
        self.assertIn('eligible=1',output.getvalue())

    def test_repeat_problem_and_inexact_baseline_old_contests_do_not_inflate_total(self):
        base=self.baseline();self.contest_problem('1',7)
        self.assertEqual(lifetime_solved_counts([self.author.pk])[self.author.pk],10)

    def test_historical_contest_can_reclassify_unknown_baseline_date_without_increasing_total(self):
        base=self.baseline();base.ids_complete=False;base.save()
        self.contest_problem('historical',1)
        data=self.client.get('/api/training-activity?start=2026-11-01&end=2026-11-10').json()
        self.assertEqual(data['ac']['total'],10)
        self.assertEqual(data['ac']['undated'],8)
        self.assertEqual(data['ac']['rangeTotal'],2)
        base.ids_complete=False;base.save()
        row=self.contest_problem('2',0)
        row.accepted_at=REWARD_START+timedelta(days=7);row.save()
        self.assertEqual(lifetime_solved_counts([self.author.pk])[self.author.pk],10)


class AdapterTests(SimpleTestCase):
    @patch('club.platform_solved.time.sleep')
    def test_codeforces_reads_beyond_first_page_and_keeps_first_known_ac_time(self,_):
        first=[{'id':i,'verdict':'OK','problem':{'contestId':1,'index':'A'},'creationTimeSeconds':REWARD_START.timestamp()} for i in range(10000)]
        remote=Mock();remote.json.side_effect=[{'status':'OK','result':first},{'status':'OK','result':[
            {'id':10001,'verdict':'OK','problem':{'contestId':2,'index':'B'},'creationTimeSeconds':REWARD_START.timestamp()-86400}]}]
        data=collect(remote,'codeforces','author',{})
        self.assertEqual(data['total'],2);self.assertIn('from=10001',remote.json.call_args.args[0])
        self.assertEqual(data['dated_problems']['2:b'],'2026-10-31')

    def test_leetcode_total_does_not_turn_undated_questions_into_today(self):
        remote=Mock();remote.json.return_value={'data':{'recentAcSubmissionList':[
            {'titleSlug':'one','timestamp':str(REWARD_START.timestamp())},{'titleSlug':'unknown','timestamp':None}]}}
        data=collect(remote,'leetcode','author',{'solved':100})
        self.assertEqual(data['total'],100);self.assertEqual(data['dated_problems'],{'one':'2026-11-01'})
        self.assertFalse(data['ids_complete'])

    @patch('club.platform_solved.time.sleep')
    def test_atcoder_cursor_pages_and_deduplicates_ac(self,_):
        rows=[{'id':i,'user_id':'author','problem_id':'abc1_a','result':'AC','epoch_second':1000+i} for i in range(500)]
        remote=Mock();remote.json.side_effect=[rows,[]]
        data=collect(remote,'atcoder','author',{'solved':1})
        self.assertEqual(data['total'],1);self.assertEqual(len(data['problem_ids']),1)
        self.assertIn('from_second=1500',remote.json.call_args.args[0])

    def test_nowcoder_reads_next_page_and_real_dates(self):
        remote=Mock();remote.text.side_effect=[
            '''<script>window.curUser.id = "123";</script><table><tr><th>题目</th><th>提交时间</th></tr><tr><td><a href="/acm/problem/1">A</a></td><td>2026-10-30 12:00:00</td></tr></table><div class="pagination"><a>下一页</a></div>''',
            '''<script>window.curUser.id = "123";</script><table><tr><th>题目</th><th>提交时间</th></tr><tr><td><a href="/acm/problem/2">B</a></td><td>日期未知</td></tr></table>''']
        data=collect(remote,'nowcoder','123',{})
        self.assertEqual(data['total'],2);self.assertEqual(len(data['dated_problems']),1)
