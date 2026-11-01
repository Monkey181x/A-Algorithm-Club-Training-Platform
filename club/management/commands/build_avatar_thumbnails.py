from django.core.management.base import BaseCommand
from PIL import Image, UnidentifiedImageError

from club.avatar_images import ensure_thumbnail, thumbnail_name
from club.models import UserProfile


class Command(BaseCommand):
    help = 'Preview or generate missing 96px avatars, without changing original files or user links.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--user-id', type=int)
        parser.add_argument('--batch-size', type=int, default=100)

    def handle(self, *args, **options):
        if not 1 <= options['batch_size'] <= 1000:
            from django.core.management.base import CommandError
            raise CommandError('batch-size must be between 1 and 1000')
        profiles = UserProfile.objects.exclude(avatar='').order_by('pk')
        if options['user_id']:
            profiles = profiles.filter(user_id=options['user_id'])
        missing = generated = failed = existing = 0
        for profile in profiles.iterator(chunk_size=options['batch_size']):
            if profile.avatar.storage.exists(thumbnail_name(profile.avatar.name)):
                existing += 1
                continue
            missing += 1
            if not options['apply']:
                continue
            try:
                ensure_thumbnail(profile)
                generated += 1
            except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
                failed += 1
                self.stderr.write(f'user={profile.user_id}: avatar unavailable; original reference preserved')
        self.stdout.write(f'existing={existing} missing={missing} generated={generated} failed={failed} apply={options["apply"]}')
