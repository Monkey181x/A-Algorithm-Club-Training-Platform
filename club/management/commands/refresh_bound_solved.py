"""One-time baseline enrollment for accounts that were bound before this feature."""
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from club.models import PlatformBinding, PlatformSolvedBaseline
from club.profile_views import enqueue


class Command(BaseCommand):
    help='Preview or enqueue one-time solved-baseline refreshes for existing platform bindings.'

    def add_arguments(self, parser):
        parser.add_argument('--apply',action='store_true')
        parser.add_argument('--retry-failed',action='store_true')
        parser.add_argument('--user-id',type=int)

    def handle(self,*args,**options):
        bindings=PlatformBinding.objects.order_by('pk')
        if options['user_id']:
            bindings=bindings.filter(user_id=options['user_id'])
        queued=skipped=0
        for binding in bindings.iterator(chunk_size=100):
            with transaction.atomic():
                binding=PlatformBinding.objects.select_for_update().get(pk=binding.pk)
                prior=PlatformSolvedBaseline.objects.filter(binding=binding).first()
                if binding.sync_state.get('version') == 2:
                    if binding.status == 'pending' and binding.requested_at > timezone.now()-timedelta(minutes=5):
                        skipped += 1
                        continue
                    self.stdout.write(f'user={binding.user_id} platform={binding.platform}: '+('enqueue' if options['apply'] else 'would enqueue'))
                    queued += 1
                    if options['apply']:
                        from club.platform_account_views import start
                        start(binding, full=not binding.sync_state.get('complete'))
                    continue
                if (prior and (prior.total is not None or not options['retry_failed'])) or (
                        binding.status=='pending' and binding.requested_at>timezone.now()-timedelta(minutes=4)):
                    skipped+=1
                    continue
                self.stdout.write(f'user={binding.user_id} platform={binding.platform}: '+('enqueue' if options['apply'] else 'would enqueue'))
                queued+=1
                if options['apply']:
                    binding.sync_token=uuid.uuid4();binding.status='pending';binding.error=''
                    binding.requested_at=timezone.now()
                    binding.save(update_fields=['sync_token','status','error','requested_at'])
                    transaction.on_commit(lambda binding=binding:enqueue(binding))
        self.stdout.write(f'eligible={queued} skipped={skipped} apply={options["apply"]}')
