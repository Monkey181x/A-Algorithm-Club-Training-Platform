import importlib
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from django.apps import apps
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, SimpleTestCase
from .grades import grade_on, cohort_data, validate_admission_year, grade_for_year
from .models import UserProfile, Team, TeamMembership


class CohortRulesTests(SimpleTestCase):
    def test_first_autumn_includes_january_but_not_spring(self):
        profile=SimpleNamespace(admission_year=2026,grade=4,grade_date=date(2013,1,1))
        for day,level in [(date(2026,8,1),0),(date(2027,1,31),0),(date(2027,2,14),0),
                          (date(2027,2,15),1),(date(2027,8,1),2),(date(2028,8,1),4)]:
            self.assertEqual(grade_on(profile,day),level)
        self.assertIsNone(grade_on(profile,date(2026,7,31)))

    def test_squad_baseline_and_input_validation(self):
        for year,number,label in [(2013,1,'第一分队'),(2025,13,'第十三分队'),(2026,14,'第十四分队')]:
            data=cohort_data(SimpleNamespace(admission_year=year))
            self.assertEqual(data['squad_number'],number);self.assertEqual(data['squad_label'],label)
        for value in [True,2027,2012,'2026x',None]:
            with self.assertRaises(ValueError):validate_admission_year(value,date(2027,1,10))
        self.assertEqual(validate_admission_year('2026',date(2027,1,10)),2026)


class CohortStorageTests(TestCase):
    def test_registration_stores_admission_year_and_ignores_spoofed_grade(self):
        with patch('club.grades.timezone.localdate',return_value=date(2027,1,10)):
            options=self.client.get('/api/auth/registration-options').json()['admission_years']
            self.assertEqual(options[0]['value'],2026)
            payload={'account':'cohort-new','password':'LongPassword!123','confirm_password':'LongPassword!123','admission_year':'2026','grade':4}
            response=self.client.post('/api/auth/register',json.dumps(payload),content_type='application/json')
            self.assertEqual(response.status_code,201,response.content)
            profile=UserProfile.objects.get(user__username='cohort-new')
            self.assertEqual(profile.admission_year,2026);self.assertEqual(grade_on(profile),0)
            payload.update(account='cohort-future',admission_year=2027)
            self.assertEqual(self.client.post('/api/auth/register',json.dumps(payload),content_type='application/json').status_code,400)

    def test_backfill_preserves_titles_and_maps_existing_year_levels(self):
        root=User.objects.filter(username='admin').first() or User.objects.create_superuser('admin',password='RootTest!123')
        team=Team.objects.create(name='Backfill',code='COHORTFILL',created_by=root)
        users=[]
        for grade in (0,2,4):
            user=User.objects.create_user(f'legacy-cohort-{grade}')
            profile=UserProfile.objects.create(user=user,grade=grade,grade_date=date(2026,11,2))
            users.append(profile)
        title={'text':'保留称号','font':'serif','color':'#a86889'}
        member=TeamMembership.objects.create(user=users[0].user,team=team,display_name='Legacy',member_title=title)
        migration=importlib.import_module('club.migrations.0040_admission_year_and_titles')
        with patch('django.utils.timezone.localdate',return_value=date(2026,11,2)):
            migration.backfill(apps,SimpleNamespace(connection=connection))
            migration.backfill(apps,SimpleNamespace(connection=connection))
        for profile,year in zip(users,[2026,2025,2024]):
            profile.refresh_from_db();self.assertEqual(profile.admission_year,year)
        member.refresh_from_db();self.assertEqual(member.member_title,[title])
