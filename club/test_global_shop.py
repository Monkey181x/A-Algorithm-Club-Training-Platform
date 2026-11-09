import uuid
from django.test import TestCase
from .test_shop_fulfillment import ShopFulfillmentTests
from .models import TeamMembership, ShopCategory, Purchase, UserInventory, ShopItem, CurrencyBalance
from .currency_services import purchase_item, get_or_create_balance, initialize_default_currency_config
from .management_views import delete_team_data


class GlobalShopTests(TestCase):
    setUp = ShopFulfillmentTests.setUp
    login = ShopFulfillmentTests.login
    post = ShopFulfillmentTests.post
    item = ShopFulfillmentTests.item

    def check_direct_and_gift_badge_categories(self, gift_first):
        categories = [ShopCategory.objects.create(name=f'Badge source {i}') for i in range(4)]
        direct = self.item('badge', category=categories[0], cosmetic_key='taffy', cosmetic_duration_days=7)
        bundle = self.item('theme', category=categories[1], cosmetic_key='taffy', cosmetic_duration_days=7)
        physical = self.item('physical', category=categories[2], gift_badge_key='taffy')
        for product in ((bundle, direct) if gift_first else (direct, bundle)):
            purchase_item(self.member, product.pk)
        purchase_item(self.member, physical.pk)
        def badges():
            response = self.owner.get('/api/currency/inventory')
            self.assertEqual(response.status_code, 200)
            return [row for row in response.json()['inventory'] if row['item_type']=='badge']
        rows = badges()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]['category_ids']), {c.pk for c in categories[:3]})
        physical.category = categories[3]
        physical.save(update_fields=['category'])
        rows = badges()
        self.assertEqual(len(rows), 1)
        self.assertEqual(set(rows[0]['category_ids']), {categories[i].pk for i in (0, 1, 3)})

    def test_direct_then_gift_badge_categories(self):
        self.check_direct_and_gift_badge_categories(False)

    def test_gift_then_direct_badge_categories(self):
        self.check_direct_and_gift_badge_categories(True)

    def test_cross_team_wallet_catalog_limits_and_deleted_origin(self):
        second = TeamMembership.objects.create(team=self.other, user=self.user, display_name='Same buyer')
        category = ShopCategory.objects.create(team=self.team, name='Global goods')
        item = self.item('physical', category=category, stock=3, purchase_limit=1)
        self.assertEqual(get_or_create_balance(second).pk, self.balance.pk)
        client = self.login(self.user, self.other)
        self.assertIn(item.pk, [r['id'] for r in client.get('/api/currency/shop/items').json()['items']])
        self.assertIn(category.pk, [r['id'] for r in client.get('/api/currency/inventory').json()['categories']])
        purchase_item(self.member, item.pk, request_id=str(uuid.uuid4()))
        with self.assertRaisesMessage(ValueError, '已达购买上限'):
            purchase_item(second, item.pk)
        ticket = self.item('makeup_ticket', category=category)
        purchase_item(self.member, ticket.pk)
        purchase_item(second, ticket.pk)
        self.assertEqual(UserInventory.objects.filter(user=self.user, item=ticket).count(), 1)
        self.assertEqual(UserInventory.objects.get(user=self.user, item=ticket).quantity, 2)
        order_ids = set(Purchase.objects.values_list('pk', flat=True))
        delete_team_data(self.team)
        self.assertEqual(set(Purchase.objects.values_list('pk', flat=True)), order_ids)
        self.assertTrue(UserInventory.objects.filter(user=self.user, item=ticket).exists())
        self.assertTrue(ShopItem.objects.filter(pk=item.pk).exists())
        self.assertEqual(CurrencyBalance.objects.get(user=self.user).balance, 91)
        response = client.get('/api/currency/inventory')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({r['item_id'] for r in response.json()['inventory']}, {ticket.pk, item.pk})
        with self.assertRaisesMessage(ValueError, '已达购买上限'):
            purchase_item(second, item.pk)

    def test_new_team_does_not_duplicate_default_product(self):
        initialize_default_currency_config(self.team)
        initialize_default_currency_config(self.other)
        self.assertEqual(ShopItem.objects.filter(item_type='makeup_ticket').count(), 1)

    def test_account_inventory_management_without_team(self):
        from .models import ShopFulfillment
        from django.contrib.admin.models import LogEntry
        item = self.item('consumable')
        physical = self.item('physical', stock=2, gift_badge_key='rbc-pendant')
        badge = self.item('badge', cosmetic_key='taffy')
        purchase_item(self.member, item.pk)
        purchase_item(self.member, physical.pk)
        inventory = UserInventory.objects.get(user=self.user, item=item)
        unit = ShopFulfillment.objects.get(purchase__user=self.user, purchase__item=physical)
        delete_team_data(self.team)
        path = f'/api/management/accounts/{self.user.pk}/inventory'
        response = self.post(self.admin, path, {'action':'quantity', 'inventory_id':inventory.pk,
            'item_id':item.pk, 'expected_quantity':1, 'quantity':0})
        self.assertEqual(response.status_code, 200, response.content)
        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity, 0)
        response = self.post(self.admin, path, {'action':'revoke', 'unit_id':unit.pk})
        self.assertEqual(response.status_code, 200, response.content)
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'reclaimed')
        physical.refresh_from_db()
        self.assertEqual(physical.stock, 2)
        for product in (item, physical, badge):
            payload = {'action':'grant', 'item_id':product.pk, 'quantity':1, 'request_id':str(uuid.uuid4())}
            response = self.post(self.admin, path, payload)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(self.post(self.admin, path, payload).status_code, 200)
            order = Purchase.objects.get(request_id=payload['request_id'])
            self.assertEqual(order.user_id, self.user.pk)
            self.assertIsNone(order.member_id)
            self.assertIsNone(order.team_id)
        self.assertTrue(LogEntry.objects.filter(content_type__model='user', object_id=str(self.user.pk)).exists())
        self.assertEqual(self.post(self.outsider, path, {'action':'revoke','unit_id':unit.pk}).status_code, 403)

    def test_gift_is_deduplicated_with_all_source_categories(self):
        from .models import PurchaseBadgeGrant
        from .cosmetics import award_purchase_badge
        first = ShopCategory.objects.create(name='First gifts')
        second = ShopCategory.objects.create(name='Second gifts')
        products = [self.item('physical', category=category, gift_badge_key='rbc-pendant')
                    for category in (first, second)]
        for product in products:
            purchase_item(self.member, product.pk)
        order = Purchase.objects.filter(user=self.user).first()
        award_purchase_badge(order)
        self.assertEqual(PurchaseBadgeGrant.objects.filter(user=self.user).count(), 2)
        gifts = [row for row in self.owner.get('/api/currency/inventory').json()['inventory']
                 if row['id'] == 'gift-rbc-pendant']
        self.assertEqual(len(gifts), 1)
        self.assertEqual(set(gifts[0]['category_ids']), {first.pk, second.pk})
        products[1].category = first
        products[1].save(update_fields=['category'])
        gifts = [row for row in self.owner.get('/api/currency/inventory').json()['inventory']
                 if row['id'] == 'gift-rbc-pendant']
        self.assertEqual(gifts[0]['category_ids'], [first.pk])
