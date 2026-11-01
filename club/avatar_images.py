"""Small, versioned derivatives; original historical avatar references stay intact."""
import hashlib
import io

from PIL import Image, ImageOps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import transaction

from .models import UserProfile

RECIPE = 'jpeg-contain-v2'
SMALL_SIZE = 96
LARGE_SIZE = 512
MAX_PIXELS = 16000000


def version(source):
    return hashlib.sha256(f'{RECIPE}:{source}'.encode()).hexdigest()[:24]


def thumbnail_name(source):
    return f'avatars/thumbnails/{version(source)}.jpg'


def image_bytes(image, size, quality):
    # Preserve the complete frame and aspect ratio; never crop or upscale.
    image = image.copy()
    image.thumbnail((size, size), resample=Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=quality, optimize=True)
    return output.getvalue()


def read_image(file):
    image = Image.open(file)
    if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > MAX_PIXELS:
        raise ValueError('Unsupported avatar')
    # JPEG draft decoding avoids expanding all source pixels when possible.
    image.draft('RGB', (LARGE_SIZE * 2, LARGE_SIZE * 2))
    return ImageOps.exif_transpose(image).convert('RGB')


def prepare_upload(file):
    image = read_image(file)
    return image_bytes(image, LARGE_SIZE, 88), image_bytes(image, SMALL_SIZE, 82)


def url_for(profile, member_id=None, size='large'):
    if not profile or not profile.avatar:
        return None
    base = f'/api/members/{member_id}/avatar' if member_id is not None else '/api/profile/avatar'
    return f'{base}?size={size}&v={version(profile.avatar.name)}'


def member_urls(members):
    profiles = {p.user_id: p for p in UserProfile.objects.filter(user_id__in=[m.user_id for m in members])}
    return {m.pk: url_for(profiles.get(m.user_id), m.pk, 'small') for m in members}


def ensure_thumbnail(profile):
    """Return a small file, generating legacy/missing derivatives once per account."""
    storage = profile.avatar.storage
    name = thumbnail_name(profile.avatar.name)
    if storage.exists(name):
        return name
    with transaction.atomic():
        User.objects.select_for_update().get(pk=profile.user_id)
        current = UserProfile.objects.get(pk=profile.pk)
        if not current.avatar:
            raise FileNotFoundError('Avatar removed')
        profile.avatar = current.avatar
        name = thumbnail_name(current.avatar.name)
        if not storage.exists(name):
            with current.avatar.open('rb') as file:
                content = image_bytes(read_image(file), SMALL_SIZE, 82)
            saved = storage.save(name, ContentFile(content))
            if saved != name:
                # Defensive cleanup for storage backends that rename on a collision.
                storage.delete(saved)
                if not storage.exists(name):
                    raise OSError('Thumbnail storage failed')
    return name
