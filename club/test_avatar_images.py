import io
import tempfile
from io import StringIO
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, Client, override_settings
from django.test.utils import CaptureQueriesContext

from . import avatar_images
from .models import UserProfile, Team, TeamMembership, FileDeletion
from .rankings import ranking_data
from . import test_profile as profile_fixtures


class AvatarImageTests(TestCase):
    def setUp(self):
        profile_fixtures.ProfileTests.setUp(self)
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        setting = override_settings(MEDIA_ROOT=media.name)
        setting.enable()
        self.addCleanup(setting.disable)

    def upload(self):
        output = io.BytesIO()
        Image.effect_noise((1400, 1000), 35).convert('RGB').save(output, format='JPEG', quality=92)
        response = self.client.post('/api/profile', {'avatar':SimpleUploadedFile('photo.jpg', output.getvalue(), content_type='image/jpeg')})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json(), len(output.getvalue())

    def test_compression_keeps_full_frame_aspect_ratio_and_does_not_upscale(self):
        image = Image.new('RGB', (800, 400), 'green')
        # Distinct edge bands prove neither side is cropped out.
        image.paste('red', (0, 0, 100, 400))
        image.paste('blue', (700, 0, 800, 400))
        result = Image.open(io.BytesIO(avatar_images.image_bytes(image, 96, 82)))
        self.assertEqual(result.size, (96, 48))
        left, right = result.getpixel((2, 24)), result.getpixel((93, 24))
        self.assertGreater(left[0], 200)
        self.assertGreater(right[2], 200)
        tiny = Image.new('RGB', (20, 40), 'green')
        self.assertEqual(Image.open(io.BytesIO(avatar_images.image_bytes(tiny, 512, 88))).size, (20, 40))

    def contents(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.content if not response.streaming else '')
        data = b''.join(response.streaming_content)
        return response, data

    def legacy(self, user=None):
        output = io.BytesIO()
        Image.effect_noise((1400, 1000), 35).convert('RGB').save(output, format='PNG')
        profile, _ = UserProfile.objects.get_or_create(user=user or self.user)
        profile.avatar.save('legacy.png', ContentFile(output.getvalue()), save=True)
        return profile, output.getvalue()

    def test_upload_produces_clear_large_and_small_versions_and_conditional_private_cache(self):
        info, original_size = self.upload()
        large, large_bytes = self.contents(info['avatar'])
        small, small_bytes = self.contents(info['member_avatar'])
        self.assertEqual(Image.open(io.BytesIO(large_bytes)).size, (512, 366))
        self.assertEqual(Image.open(io.BytesIO(small_bytes)).size, (96, 69))
        self.assertLess(len(small_bytes), len(large_bytes))
        self.assertLess(len(small_bytes), original_size // 10)
        self.assertIn('private', small['Cache-Control'])
        self.assertIn('immutable', small['Cache-Control'])
        self.assertNotIn('public', small['Cache-Control'])
        self.assertEqual(small['CDN-Cache-Control'], 'no-store')
        self.assertIn('Cookie', small['Vary'])
        for validator in (small['ETag'], 'W/' + small['ETag']):
            response = self.client.get(info['member_avatar'], HTTP_IF_NONE_MATCH=validator)
            self.assertEqual(response.status_code, 304)
            self.assertEqual(response.content, b'')
        self.assertEqual(self.client.head(info['member_avatar']).status_code, 200)

    def test_replacement_changes_urls_without_changing_user_and_cleans_both_old_files(self):
        first, _ = self.upload()
        original = UserProfile.objects.get(user=self.user).avatar.name
        second, _ = self.upload()
        self.assertNotEqual(first['avatar'], second['avatar'])
        self.assertNotEqual(first['member_avatar'], second['member_avatar'])
        self.assertEqual(first['member_id'], second['member_id'])
        self.assertTrue(FileDeletion.objects.filter(name=original).exists())
        self.assertTrue(FileDeletion.objects.filter(name=avatar_images.thumbnail_name(original)).exists())
        stale, _ = self.contents(first['avatar'])
        self.assertEqual(stale['Cache-Control'], 'private, no-cache')
        current = self.client.get('/api/profile').json()
        self.assertEqual(second['avatar'], current['avatar'])

    def test_legacy_links_and_missing_thumbnail_fallback_never_send_large_image_to_small_request(self):
        profile, original = self.legacy()
        source = profile.avatar.name
        url = avatar_images.url_for(profile, self.member.pk, 'small')
        with patch('club.avatar_images.read_image', wraps=avatar_images.read_image) as decode:
            _, small = self.contents(url)
            _, again = self.contents(url)
            self.assertEqual(decode.call_count, 1)
        self.assertEqual(small, again)
        self.assertLess(len(small), len(original) // 20)
        self.assertEqual(Image.open(io.BytesIO(small)).size, (96, 69))
        profile.refresh_from_db()
        self.assertEqual(profile.avatar.name, source)
        with profile.avatar.open('rb') as file:
            self.assertEqual(file.read(), original)
        profile.avatar.storage.delete(avatar_images.thumbnail_name(source))
        self.assertEqual(self.client.get(url).status_code, 200)
        old, data = self.contents(f'/api/members/{self.member.pk}/avatar')
        self.assertEqual(data, original)
        self.assertEqual(old['Content-Type'], 'image/png')
        self.assertEqual(old['Cache-Control'], 'private, no-cache')

    def test_cached_etag_cannot_bypass_team_or_login_permissions(self):
        info, _ = self.upload()
        response, _ = self.contents(info['member_avatar'])
        headers = {'HTTP_IF_NONE_MATCH':response['ETag']}
        self.assertEqual(Client().get(info['member_avatar'], **headers).status_code, 401)
        team = Team.objects.create(name='Outside', code='OUTSIDE-AVATAR', created_by=self.root)
        outsider = User.objects.create_user('outside-avatar')
        TeamMembership.objects.create(team=team, user=outsider, display_name='Outside')
        client = Client(); client.force_login(outsider)
        self.assertEqual(client.get(info['member_avatar'], **headers).status_code, 404)
        self.assertEqual(self.client.get('/api/profile/avatar?size=original').status_code, 400)

    def test_batch_backfill_is_dry_by_default_idempotent_and_keeps_unavailable_sources(self):
        profile, original = self.legacy()
        missing = UserProfile.objects.create(user=self.other, avatar='avatars/missing.jpg')
        before = profile.avatar.name
        output = StringIO()
        call_command('build_avatar_thumbnails', stdout=output)
        self.assertIn('missing=2 generated=0', output.getvalue())
        self.assertFalse(profile.avatar.storage.exists(avatar_images.thumbnail_name(before)))
        call_command('build_avatar_thumbnails', apply=True, user_id=self.user.pk, stdout=StringIO())
        second = StringIO()
        call_command('build_avatar_thumbnails', apply=True, stdout=second, stderr=StringIO())
        self.assertIn('existing=1', second.getvalue())
        self.assertIn('failed=1', second.getvalue())
        profile.refresh_from_db(); missing.refresh_from_db()
        self.assertEqual(profile.avatar.name, before)
        self.assertEqual(missing.avatar.name, 'avatars/missing.jpg')
        with profile.avatar.open('rb') as file:
            self.assertEqual(file.read(), original)

    def test_rankings_use_one_profile_query_and_preserve_the_same_order_and_values(self):
        before = ranking_data(self.member, 'checkin')['rows']
        info, _ = self.upload()
        with CaptureQueriesContext(connection) as queries:
            after = ranking_data(self.member, 'checkin')['rows']
        self.assertEqual([{k:v for k,v in r.items() if k!='avatar'} for r in before],
                         [{k:v for k,v in r.items() if k!='avatar'} for r in after])
        self.assertEqual(sum('club_userprofile' in q['sql'] for q in queries), 1)
        row = next(r for r in after if r['memberId']==self.member.pk)
        self.assertEqual(row['avatar'], info['member_avatar'])
        self.assertIsNone(next(r for r in after if r['memberId']!=self.member.pk)['avatar'])

    def test_missing_and_broken_originals_fail_without_mutating_profile(self):
        profile = UserProfile.objects.create(user=self.user, avatar='avatars/absent.png')
        url = avatar_images.url_for(profile, self.member.pk, 'small')
        self.assertEqual(self.client.get(url).status_code, 404)
        profile.avatar.save('broken.png', ContentFile(b'not an image'), save=True)
        source = profile.avatar.name
        self.assertEqual(self.client.get(avatar_images.url_for(profile, self.member.pk, 'small')).status_code, 404)
        profile.refresh_from_db(); self.assertEqual(profile.avatar.name, source)

    def test_account_deletion_queues_both_avatar_sizes_for_existing_cleanup(self):
        self.upload()
        original = UserProfile.objects.get(user=self.user).avatar.name
        self.user.delete()
        self.assertTrue(FileDeletion.objects.filter(name=original).exists())
        self.assertTrue(FileDeletion.objects.filter(name=avatar_images.thumbnail_name(original)).exists())
