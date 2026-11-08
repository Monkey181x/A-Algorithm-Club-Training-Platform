from unittest.mock import patch
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from . import test_community as fixtures
from .models import PlatformBinding, PlatformSolvedRecord
from .platform_records import run_step, store
from .platform_sync import SyncError
from .profile_views import serialize_binding
from .platform_account_views import start

class ReliabilityTests(TestCase):
    def setUp(self):
        fixtures.CommunityTests.setUp(self)
        self.binding=PlatformBinding.objects.create(user=self.author,platform='codeforces',account='reader',status='pending',sync_state={'version':2,'phase':'records','page':3,'pages':2,'count':1})
        PlatformSolvedRecord.objects.create(binding=self.binding,problem_key='1:a',solved_on=None)

    def test_retry_retains_page_and_existing_records_then_stops(self):
        with patch('club.platform_records.attach'),patch('club.platform_records.history_page',side_effect=SyncError('temporary')),patch('club.tasks.sync_platform_binding.apply_async') as enqueue:
            for attempt in range(4):
                with self.captureOnCommitCallbacks(execute=True):
                    run_step(self.binding.pk,str(self.binding.sync_token))
                self.binding.refresh_from_db()
                self.assertEqual(self.binding.sync_state['page'],3)
                self.assertEqual(self.binding.solved_records.count(),1)
                self.assertEqual(self.binding.status,'pending' if attempt<3 else 'failed')
        self.assertEqual([c.kwargs['countdown'] for c in enqueue.call_args_list],[15,30,60])

    def test_unknown_dates_and_repeated_ac_preserve_first_known_date(self):
        for day in [None,'2026-11-03','2026-11-04',None]:
            store(self.binding,[{'problem_key':'1:a','solved_on':day,'title':'A','url':''}])
        record=self.binding.solved_records.get()
        self.assertEqual(record.solved_on.isoformat(),'2026-11-03')
        self.assertEqual(self.binding.solved_records.count(),1)

    def test_partial_and_complete_are_distinct(self):
        self.binding.status='ready';self.binding.sync_state.update(phase='done',complete=False)
        self.assertEqual(serialize_binding(self.binding)['coverage'],'partial')
        self.binding.sync_state['complete']=True
        self.assertEqual(serialize_binding(self.binding)['coverage'],'complete')

    def test_expired_job_resumes_saved_page(self):
        self.binding.requested_at=timezone.now()-timedelta(minutes=10)
        self.binding.save()
        with patch('club.platform_account_views.enqueue'),self.captureOnCommitCallbacks(execute=True):
            start(self.binding)
        self.assertEqual(self.binding.sync_state['page'],3)
        self.assertEqual(self.binding.sync_state['phase'],'records')

    def test_repeated_sync_reuses_pending_token(self):
        self.client.force_login(self.author)
        token=self.binding.sync_token
        with patch('club.platform_account_views.enqueue') as enqueue:
            response=self.client.post('/api/profile/platform-accounts/sync',{'ids':[self.binding.pk]},content_type='application/json')
        self.assertEqual(response.status_code,202)
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.sync_token,token)
        enqueue.assert_not_called()

    def test_credentials_error_requires_user_action_without_retry(self):
        from .platform_sync import CredentialError
        with patch('club.platform_records.attach',side_effect=CredentialError('invalid credentials')),patch('club.tasks.sync_platform_binding.apply_async') as enqueue,self.captureOnCommitCallbacks(execute=True):
            run_step(self.binding.pk,str(self.binding.sync_token))
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.status,'failed')
        self.assertEqual(self.binding.sync_state['page'],3)
        enqueue.assert_not_called()
