from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from . import test_community as fixtures
from .models import (Submission, SubmissionPart, SolvedProblemCredit, CheckinRecord,
                     PlatformBinding, ContestParticipation, ContestProblemProgress)


class TrainingActivityTests(TestCase):
    setUp = fixtures.CommunityTests.setUp

    def test_date_strip_spans_previous_monday_to_today_for_each_day_of_week(self):
        from datetime import datetime, time
        from unittest.mock import patch
        monday=timezone.localdate()-timedelta(days=timezone.localdate().weekday())
        for index in range(7):
            now=timezone.make_aware(datetime.combine(monday+timedelta(days=index),time(12)))
            with self.subTest(weekday=index),patch('django.utils.timezone.now',return_value=now):
                response=self.client.get('/api/dashboard')
                self.assertEqual(response.status_code,200,response.content)
                days=response.json()['pulse']
                expected=[(monday+timedelta(days=i-7)).isoformat() for i in range(index+8)]
                self.assertEqual([d['date'] for d in days],expected)
                self.assertEqual(sum(d['isToday'] for d in days),1)
                self.assertFalse(any(d['date']>expected[-1] for d in days))

    def test_heatmap_retains_records_older_than_current_week(self):
        old=timezone.localdate()-timedelta(days=10)
        self.record(day=old)
        response=self.client.get('/api/training-activity',{'start':old.isoformat(),'end':old.isoformat()})
        self.assertEqual(response.status_code,200,response.content)
        day=response.json()['days'][0]
        self.assertEqual(day['date'],old.isoformat())
        self.assertEqual(day['daily'],1)

    def record(self, member=None, valid=True, approved=True, day=None):
        row = Submission.objects.create(team=self.team, member=member or self.author_member,
            semester=self.semester, week=self.week, problem=self.problem,
            record_date=day or timezone.localdate(), is_valid=valid)
        SubmissionPart.objects.create(submission=row, kind='proof', status='approved' if approved else 'pending')
        return row

    def payload(self):
        response = self.client.get('/api/training-activity')
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_ac_dedup_daily_counts_and_other_users_are_private(self):
        self.record(); self.record(); self.record(valid=False)
        self.record(member=self.owner_member)
        data = self.payload()
        self.assertEqual(data['ac']['total'], 1)
        self.assertEqual(data['ac']['rangeTotal'], 1)
        self.assertEqual(data['daily']['total'], 2)
        self.assertEqual(data['daily']['streak'], 1)
        today = next(d for d in data['days'] if d['isToday'])
        self.assertEqual(len(today['records']), 3)
        self.assertTrue(today['canSubmit'])

    def test_checkins_and_pending_proofs_are_not_ac(self):
        self.record(approved=False)
        CheckinRecord.objects.create(team=self.team, member=self.author_member,
            checkin_date=timezone.localdate()-timedelta(days=1))
        data = self.payload()
        self.assertEqual(data['ac']['total'], 0)
        self.assertEqual(data['daily']['total'], 1)
        self.assertEqual(data['daily']['streak'], 1)

    def test_empty_invalid_and_checkin_only_days_are_not_submissions(self):
        today = timezone.localdate()
        self.record(day=today-timedelta(days=1), valid=False)
        CheckinRecord.objects.create(team=self.team, member=self.author_member,
            checkin_date=today-timedelta(days=2))
        response = self.client.get('/api/training-activity', {
            'start': (today-timedelta(days=3)).isoformat(),
            'end': (today+timedelta(days=1)).isoformat()})
        self.assertEqual(response.status_code, 200)
        days = response.json()['days']
        for day in days:
            with self.subTest(date=day['date']):
                self.assertEqual(day['daily'], 0)
                self.assertFalse(day['hasSubmission'])
                self.assertEqual(day['submitted'], day['checkedIn'])
        self.assertTrue(days[1]['checkedIn'])

    def test_pending_submission_is_flagged_only_on_its_record_date(self):
        today = timezone.localdate()
        self.record(day=today-timedelta(days=1), approved=False)
        response = self.client.get('/api/training-activity', {
            'start': (today-timedelta(days=2)).isoformat(), 'end': today.isoformat()})
        days = response.json()['days']
        self.assertEqual([day['hasSubmission'] for day in days], [False, True, False])
        self.assertEqual([day['daily'] for day in days], [0, 1, 0])
        self.assertEqual([day['ac'] for day in days], [0, 0, 0])

    def test_real_contest_dates_and_undated_credits_not_fabricated(self):
        PlatformBinding.objects.create(user=self.author, platform='codeforces', account='author')
        p = ContestParticipation.objects.create(user=self.author, platform='codeforces', account='author',
            contest_key='1', title='Contest', url='https://example.com', starts_at=self.week.starts_at, ends_at=self.week.ends_at)
        ContestProblemProgress.objects.create(participation=p, problem_key='A', label='A', title='A',
            accepted=True, accepted_at=timezone.now(), url='https://example.com/A')
        SolvedProblemCredit.objects.get_or_create(user=self.author, source='contest', problem_key='codeforces:1:A')
        SolvedProblemCredit.objects.create(user=self.author, source='contest', problem_key='old:B')
        data = self.payload()
        self.assertEqual(data['ac']['total'], 2)
        self.assertEqual(data['ac']['rangeTotal'], 1)
        self.assertEqual(data['ac']['undated'], 1)
        self.assertEqual(data['daily']['total'], 0)

    def test_permissions_and_invalid_ranges(self):
        self.assertEqual(self.client.post('/api/training-activity').status_code, 405)
        self.assertEqual(self.client.get('/api/training-activity?start=2026-02-30').status_code, 400)
        self.assertEqual(self.client.get('/api/training-activity?start=2020-01-01&end=2026-01-01').status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get('/api/training-activity').status_code, 401)

    def test_month_range_leaves_lifetime_total_and_includes_future_empty_days(self):
        self.record(day=timezone.localdate()-timedelta(days=40))
        start=timezone.localdate();end=start+timedelta(days=2)
        data=self.client.get('/api/training-activity', {'start':start.isoformat(),'end':end.isoformat()}).json()
        self.assertEqual(data['ac']['total'],1)
        self.assertEqual(data['ac']['rangeTotal'],0)
        self.assertEqual(len(data['days']),3)
        self.assertFalse(data['days'][-1]['canSubmit'])
