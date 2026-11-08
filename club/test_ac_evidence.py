from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from . import test_community as fixtures
from .models import PlatformBinding, PlatformSolvedRecord, PlatformSolvedBaseline, ContestParticipation, ContestProblemProgress
from .ac_evidence import ACEvidence
from .tracker_views import accepted_keys

class EvidenceTests(TestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        self.binding=PlatformBinding.objects.create(user=self.author,platform='atcoder',account='reader',sync_state={'version':2})
        PlatformSolvedRecord.objects.create(binding=self.binding,problem_key='abc1_a')
        PlatformSolvedBaseline.objects.create(binding=self.binding,total=2,problem_ids=['abc1_a'],dated_problems={'abc1_b':'2026-11-02'},captured_at=timezone.now())

    def contest(self, account='reader', demo=False):
        c=ContestParticipation.objects.create(user=self.author,platform='atcoder',account=account,contest_key='abc2',title='ABC',starts_at=timezone.now()-timedelta(hours=1),ends_at=timezone.now(),source_data={'demo':True} if demo else {})
        return ContestProblemProgress.objects.create(participation=c,problem_key='abc2_a',label='A',accepted=True)

    def test_tracker_uses_same_union_and_keeps_unknown_dates(self):
        self.contest()
        evidence=ACEvidence([self.author.pk],platform='atcoder')
        self.assertEqual(evidence.accepted_keys(),{'abc1_a','abc1_b','abc2_a'})
        self.assertEqual(accepted_keys(self.author,'atcoder'),evidence.accepted_keys())
        self.assertIsNone(evidence.records().get().solved_on)

    def test_demo_and_unbound_contest_cannot_supply_reward_evidence(self):
        self.contest(account='unbound')
        self.contest(demo=True)
        evidence=ACEvidence([self.author.pk],platform='atcoder')
        self.assertFalse(evidence.contests().exists())
        self.assertNotIn('abc2_a',evidence.accepted_keys())

    def test_binding_filter_cannot_read_other_account(self):
        other=PlatformBinding.objects.create(user=self.author,platform='atcoder',account='second')
        PlatformSolvedRecord.objects.create(binding=other,problem_key='abc9_a')
        self.assertEqual(ACEvidence([self.author.pk],binding_id=other.pk).accepted_keys(),{'abc9_a'})
        from django.contrib.auth.models import User
        outsider=User.objects.create_user('evidence-outsider')
        self.assertFalse(ACEvidence([outsider.pk],binding_id=other.pk).records().exists())
