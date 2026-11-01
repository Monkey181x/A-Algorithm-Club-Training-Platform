from django.contrib.auth.models import User
from django.test import TestCase

from . import test_community as fixtures
from .models import Team, TeamMembership, PlatformBinding, Submission, SubmissionPart


class MemberProfileTests(TestCase):
    setUp = fixtures.CommunityTests.setUp

    def url(self):
        return f'/api/members/{self.author_member.pk}/profile'

    def record(self, **extra):
        return Submission.objects.create(team=self.team,member=self.author_member,
            semester=self.semester,week=self.week,problem=self.problem,**extra)

    def test_member_can_read_five_platforms_but_not_private_fields(self):
        PlatformBinding.objects.create(user=self.author,platform='leetcode',account='cn:alice',status='ready',
            snapshot={'rating':1530,'solved':55,'private_token':'must-not-leak'})
        self.client.force_login(self.reader)
        response=self.client.get(self.url())
        self.assertEqual(response.status_code,200,response.content)
        data=response.json()
        self.assertEqual(len(data['platforms']),5)
        self.assertEqual(data['bindings'][0]['snapshot'],{'rating':1530,'solved':55})
        self.assertNotIn('account',data['member'])
        self.assertNotIn('password',response.content.decode())
        self.assertNotIn('must-not-leak',response.content.decode())
        self.assertIn('no-store',response['Cache-Control'])

    def test_unrelated_or_inactive_members_are_not_visible(self):
        outsider=User.objects.create_user('unrelated-member-profile')
        team=Team.objects.create(name='Elsewhere',code='OTHERPROFILE',created_by=outsider)
        TeamMembership.objects.create(team=team,user=outsider,display_name='Outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url()).status_code,404)
        self.client.force_login(self.reader)
        self.author_member.is_active=False;self.author_member.save()
        self.assertEqual(self.client.get(self.url()).status_code,404)
        self.client.logout()
        self.assertEqual(self.client.get(self.url()).status_code,401)

    def test_recent_records_never_reveal_rejected_material_or_edit_actions(self):
        record=self.record(rejection_note='private old reason')
        SubmissionPart.objects.create(submission=record,kind='proof',status='approved',review_note='private review')
        SubmissionPart.objects.create(submission=record,kind='logic',status='rejected',text_content='private rejected material')
        self.record(is_valid=False)
        self.client.force_login(self.reader)
        response=self.client.get(self.url());data=response.json()
        self.assertEqual(data['total'],1)
        item=data['submissions'][0]
        self.assertEqual([part['kind'] for part in item['parts']],['proof'])
        self.assertFalse(item['can_edit']);self.assertFalse(item['can_delete']);self.assertFalse(item['can_resubmit'])
        self.assertNotIn('private ',response.content.decode())

    def test_recent_records_paginate_in_reverse_creation_order(self):
        ids=[self.record().pk for _ in range(23)]
        self.client.force_login(self.reader)
        first=self.client.get(self.url()).json()
        second=self.client.get(self.url()+'?page=2').json()
        self.assertEqual(first['next_page'],2)
        self.assertEqual(second['next_page'],None)
        self.assertEqual([item['id'] for item in first['submissions']+second['submissions']],list(reversed(ids)))
        self.assertEqual(self.client.get(self.url()+'?page=bad').status_code,400)

    def test_sandbox_members_are_private(self):
        user=User.objects.create_user('__club_test_private_profile',first_name='CLUB_TEST_SANDBOX')
        member=TeamMembership.objects.create(team=self.team,user=user,display_name='Private')
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(f'/api/members/{member.pk}/profile').status_code,404)
