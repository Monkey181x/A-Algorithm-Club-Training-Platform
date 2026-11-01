import importlib
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from . import test_community as fixtures
from .contest_rewards import REWARD_START, reward_for, claim_problems
from .models import (ContestParticipation, ContestProblemProgress, DatedSolvedProblemCredit,
    PlatformBinding, SolvedProblemCredit, Submission, SubmissionPart, UserProfile)
from .solved import semester_solved_counts, lifetime_solved_counts, archive_submission_credits


class SolvedPeriodTests(TestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        self.clock = patch('django.utils.timezone.now', return_value=REWARD_START+timedelta(days=14))
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.semester.starts_on = date(2026, 10, 1)
        self.semester.ends_on = date(2027, 2, 14)
        self.semester.calendar_managed = False
        self.semester.save()
        self.semester.weeks.all().delete()
        from .services import build_weeks
        build_weeks(self.semester)
        self.week=self.semester.weeks.get(starts_at__lte=timezone.now(),ends_at__gt=timezone.now())
        self.team.currency_enabled = True; self.team.save()
        UserProfile.objects.create(user=self.author, grade=0, grade_date=date(2026, 8, 1))
        PlatformBinding.objects.create(user=self.author, platform='codeforces', account='author')

    def contest(self, key, day, accepted=None, demo=False):
        start = REWARD_START+timedelta(days=day)
        contest = ContestParticipation.objects.create(user=self.author, platform='codeforces', account='author',
            contest_key=key, title=key, starts_at=start, ends_at=start+timedelta(hours=2), detail_complete=True,
            source_data={'demo':True} if demo else {})
        return ContestProblemProgress.objects.create(participation=contest, problem_key='A', label='A', title='A',
            accepted=True, official_ac=True, accepted_at=start+timedelta(minutes=5) if accepted is None else accepted,
            difficulty=500)

    def rows(self, **kwargs):
        response = self.client.get('/api/leaderboard', {'scope':'solved', **kwargs})
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        return data, next(row for row in data['rows'] if row['memberId']==self.author_member.pk)

    def test_defaults_to_current_semester_and_counts_pre_november_ac_without_coins(self):
        old = self.contest('old', -60)
        october = self.contest('october', -10)
        current = self.contest('current', 1)
        data, row = self.rows()
        self.assertEqual(data['period'], 'semester')
        self.assertEqual(data['semester']['start'], '2026-10-01')
        self.assertEqual(row['value'], 2)
        self.assertEqual(self.rows(period='all')[1]['value'], 3)
        self.assertEqual(reward_for(old,self.author_member)[0], 0)
        self.assertEqual(reward_for(october,self.author_member)[0], 0)
        result = claim_problems(self.author_member)
        self.assertEqual([r['id'] for r in result['claims']], [current.pk])
        self.assertEqual(result['total'], 1)

    def test_actual_ac_date_and_unknown_dates_respect_custom_term_boundaries(self):
        known = self.contest('known', -60, accepted=REWARD_START+timedelta(days=2))
        unknown = self.contest('unknown', -10)
        unknown.accepted_at = None; unknown.save()
        self.contest('future', 60)
        self.assertEqual(self.rows()[1]['value'], 1)
        # The known timestamp controls attribution, not an older contest start date.
        self.semester.starts_on = date(2026,11,1); self.semester.save()
        self.assertEqual(self.rows()[1]['value'], 1)
        self.assertEqual(reward_for(known,self.author_member)[0], 0)
        self.assertEqual(reward_for(unknown,self.author_member)[0], 0)

    def test_live_submissions_remove_on_invalidation_and_archive_keeps_dated_evidence(self):
        item = Submission.objects.create(team=self.team, member=self.author_member, semester=self.semester,
            week=self.week, problem=self.problem, record_date=date(2026,10,20))
        SubmissionPart.objects.create(submission=item, kind='proof', status='approved')
        self.assertEqual(self.rows()[1]['value'], 1)
        item.is_valid=False; item.save()
        self.assertEqual(self.rows()[1]['value'], 0)
        item.is_valid=True; item.save()
        archive_submission_credits(self.semester)
        self.assertEqual(self.rows()[1]['value'], 1)  # live + durable is deduplicated
        item.delete()
        self.assertEqual(self.rows()[1]['value'], 1)
        self.assertEqual(self.rows(period='all')[1]['value'], 1)

    def test_missing_legacy_dates_count_only_in_total_and_no_active_term_returns_zero(self):
        SolvedProblemCredit.objects.create(user=self.author, source='contest', problem_key='legacy:undated')
        self.assertEqual(self.rows()[1]['value'], 0)
        self.assertEqual(self.rows(period='all')[1]['value'], 1)
        self.semester.is_active=False; self.semester.save()
        data,row = self.rows()
        self.assertIsNone(data['semester']); self.assertEqual(row['value'],0)
        self.assertEqual(self.client.get('/api/leaderboard?scope=solved&period=invalid').status_code,400)

    def test_migration_backfills_historical_contests_without_awarding_or_freezing_live_submissions(self):
        self.contest('october', -10)
        self.contest('demo', -10, demo=True)
        DatedSolvedProblemCredit.objects.all().delete()
        SolvedProblemCredit.objects.all().delete()
        item = Submission.objects.create(team=self.team,member=self.author_member,semester=self.semester,
            week=self.week,problem=self.problem,record_date=date(2026,10,20))
        SubmissionPart.objects.create(submission=item,kind='proof',status='approved')
        migration=importlib.import_module('club.migrations.0037_dated_solved_credits')
        for _ in range(2):
            migration.backfill_dates(apps,SimpleNamespace(connection=SimpleNamespace(alias='default')))
        self.assertEqual(DatedSolvedProblemCredit.objects.count(),1)
        self.assertEqual(SolvedProblemCredit.objects.count(),1)
        item.delete()
        self.assertEqual(self.rows()[1]['value'],1)
        from .models import CurrencyTransaction
        self.assertFalse(CurrencyTransaction.objects.filter(member=self.author_member).exists())

    def test_non_accepted_unbound_and_demo_evidence_do_not_count(self):
        self.contest('demo', -10, demo=True)
        PlatformBinding.objects.filter(user=self.author).delete()
        self.contest('unbound', -10)
        self.assertEqual(self.rows()[1]['value'],0)
        self.assertEqual(self.rows(period='all')[1]['value'],0)

    def test_new_verified_ac_timestamp_replaces_unknown_time_date_and_uses_local_day(self):
        problem = self.contest('changing', -60)
        problem.accepted_at=None; problem.save()
        self.assertEqual(self.rows()[1]['value'],0)
        # UTC September 30 16:00 is October 1 in Asia/Shanghai.
        problem.accepted_at=REWARD_START-timedelta(days=31); problem.save()
        self.assertEqual(self.rows()[1]['value'],1)
        self.assertEqual(DatedSolvedProblemCredit.objects.filter(user=self.author).count(),1)
        self.assertEqual(self.rows(period='all')[1]['value'],1)
