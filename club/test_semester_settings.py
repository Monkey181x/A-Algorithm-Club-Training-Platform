import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.utils import timezone
from . import test_community as fixtures
from .models import Submission
from .services import validate_week_dates
from .semester_calendar import semester_bounds
from django.test import TestCase


class SemesterSettingsTests(TestCase):
    setUp=fixtures.CommunityTests.setUp

    def test_history_weeks_only_include_weeks_that_have_started(self):
        weeks = list(self.semester.weeks.order_by('number'))
        for now in (weeks[0].starts_at-timedelta(microseconds=1),
                    self.week.starts_at-timedelta(microseconds=1), self.week.starts_at,
                    weeks[-1].ends_at+timedelta(days=1)):
            with self.subTest(now=now), patch('club.views.timezone.now', return_value=now):
                self.client.force_login(self.author)
                response = self.client.get('/api/semesters')
            self.assertEqual(response.status_code, 200)
            semester = next(item for item in response.json()['semesters'] if item['id'] == self.semester.pk)
            self.assertEqual(semester['weeks'], [week.number for week in weeks if week.starts_at <= now])

    def settings(self):
        self.client.force_login(User.objects.get(username='admin'))
        return self.client.get(f'/api/management/teams/{self.team.pk}/settings').json()

    def save(self,data):
        return self.client.put(f'/api/management/teams/{self.team.pk}/settings',json.dumps(data),content_type='application/json')

    def payload(self):
        data=self.settings();term=data.pop('semester')
        data.update(term)
        data['name']=self.team.name
        return data

    def test_custom_dates_preserve_week_submission_and_update_heatmap(self):
        submission=Submission.objects.create(team=self.team,member=self.author_member,
            semester=self.semester,week=self.week,record_date=timezone.localdate())
        start=timezone.localdate()-timedelta(days=60)
        end=timezone.localdate()+timedelta(days=60)
        data=self.payload();data.update(custom_semester_dates=True,starts_on=start.isoformat(),ends_on=end.isoformat())
        response=self.save(data)
        self.assertEqual(response.status_code,200,response.content)
        self.semester.refresh_from_db();submission.refresh_from_db()
        self.assertFalse(self.semester.calendar_managed)
        self.assertEqual(submission.week_id,self.week.pk)
        validate_week_dates(self.semester)
        self.client.force_login(self.author)
        activity=self.client.get('/api/training-activity').json()
        self.assertEqual((activity['semester']['start'],activity['semester']['end']),(start.isoformat(),end.isoformat()))
        self.assertEqual(self.save(data).status_code,403)

    def test_invalid_dates_and_excluded_records_do_not_change_team(self):
        submission=Submission.objects.create(team=self.team,member=self.author_member,
            semester=self.semester,week=self.week,record_date=timezone.localdate()-timedelta(days=1))
        data=self.payload();data.update(custom_semester_dates=True,name='Must not change',
            starts_on=timezone.localdate().isoformat(),ends_on=self.semester.ends_on.isoformat())
        self.assertEqual(self.save(data).status_code,400)
        self.team.refresh_from_db();submission.refresh_from_db()
        self.assertEqual(self.team.name,'Team')
        self.assertEqual(submission.week_id,self.week.pk)
        data['starts_on']='bad'
        self.assertEqual(self.save(data).status_code,400)

    def test_disabling_custom_dates_restores_automatic_calendar(self):
        data=self.payload();data['custom_semester_dates']=False
        self.assertEqual(self.save(data).status_code,200)
        self.semester.refresh_from_db()
        self.assertTrue(self.semester.calendar_managed)
        self.assertEqual((self.semester.starts_on,self.semester.ends_on),semester_bounds(timezone.localdate()))

    def test_test_mode_heatmap_uses_target_team_dates(self):
        self.settings()
        session=self.client.session;session['team_id']=self.team.pk;session.save()
        response=self.client.post('/api/admin-test-mode',json.dumps({'enabled':True}),content_type='application/json')
        self.assertEqual(response.status_code,200,response.content)
        activity=self.client.get('/api/training-activity').json()
        self.assertEqual(activity['semester']['start'],self.semester.starts_on.isoformat())
        self.assertEqual(activity['semester']['end'],self.semester.ends_on.isoformat())
