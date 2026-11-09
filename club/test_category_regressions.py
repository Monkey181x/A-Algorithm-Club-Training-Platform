from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase

from .currency_views import shop_categories_data
from .management_views import delete_team_data
from .models import FileDeletion, ShopCategory, ShopItem, Team


class CategoryRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('category-review')
        self.team = Team.objects.create(name='Review', code='review', created_by=self.user)
        ShopCategory.objects.all().delete()

    def test_category_response_uses_position_with_counts(self):
        a = ShopCategory.objects.create(team=self.team, name='A', position=2)
        b = ShopCategory.objects.create(team=self.team, name='B', position=1)
        c = ShopCategory.objects.create(team=self.team, name='C', position=0)
        ShopItem.objects.create(team=self.team, name='Hidden', price=1, category=a, is_active=False)
        self.assertEqual(shop_categories_data(self.team), [
            {'id': c.pk, 'name': 'C', 'count': 0},
            {'id': b.pk, 'name': 'B', 'count': 0},
            {'id': a.pk, 'name': 'A', 'count': 1},
        ])
        a.position = 0
        a.save(update_fields=['position'])
        self.assertEqual([row['id'] for row in shop_categories_data(self.team)], [a.pk, c.pk, b.pk])

    def test_whole_team_deletion_preserves_other_team_and_queues_icons(self):
        category = ShopCategory.objects.create(team=self.team, name='Tools')
        item = ShopItem.objects.create(team=self.team, name='Product', price=1, category=category, icon='shop_items/review.png')
        other = Team.objects.create(name='Other', code='other', created_by=self.user)
        other_category = ShopCategory.objects.create(team=other, name='Other tools')
        other_item = ShopItem.objects.create(team=other, name='Keep', price=1, category=other_category)
        with self.assertRaises(ProtectedError), transaction.atomic():
            category.delete()
        team_id = self.team.pk
        delete_team_data(self.team)
        self.assertFalse(Team.objects.filter(pk=team_id).exists())
        self.assertTrue(ShopCategory.objects.filter(pk=category.pk, team__isnull=True).exists())
        self.assertTrue(ShopItem.objects.filter(pk=item.pk, team__isnull=True).exists())
        self.assertTrue(ShopItem.objects.filter(pk=other_item.pk).exists())
        self.assertTrue(ShopCategory.objects.filter(pk=other_category.pk).exists())
        self.assertFalse(FileDeletion.objects.filter(name='shop_items/review.png').exists())
