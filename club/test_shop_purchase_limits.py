import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth.models import User
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from . import test_shop_fulfillment as fixtures
from .currency_services import purchase_item
from .models import CurrencyBalance, CurrencyTransaction, Purchase, ShopFulfillment, ShopItem, Team, TeamMembership


class ShopPurchaseLimitTests(TestCase):
    setUp = fixtures.ShopFulfillmentTests.setUp
    login = fixtures.ShopFulfillmentTests.login
    post = fixtures.ShopFulfillmentTests.post
    item = fixtures.ShopFulfillmentTests.item

    def test_limit_counts_units_and_retries_do_not_purchase_again(self):
        item = self.item('physical', purchase_limit=2, stock=10)
        token = str(uuid.uuid4())
        purchase_item(self.member, item.pk, 2, token)
        self.assertTrue(purchase_item(self.member, item.pk, 2, token)['duplicate'])
        response = self.post(self.owner, '/api/currency/shop/purchase', {'item_id': item.pk, 'quantity': 1})
        self.assertEqual(response.status_code, 400)
        self.assertIn('已达购买上限', response.json()['error'])
        item.refresh_from_db(); self.balance.refresh_from_db()
        self.assertEqual((item.stock, self.balance.balance), (8, 94))
        self.assertEqual(Purchase.objects.count(), 1)
        self.assertEqual(CurrencyTransaction.objects.count(), 1)
        self.assertEqual(ShopFulfillment.objects.count(), 2)
        row = self.owner.get('/api/currency/shop/items').json()['items'][0]
        self.assertEqual((row['purchase_limit'], row['purchased_quantity'], row['purchase_limit_reached']), (2, 2, True))

    def test_oversized_order_rolls_back_and_other_accounts_have_separate_limits(self):
        item = self.item('physical', purchase_limit=2)
        with self.assertRaisesMessage(ValueError, '已达购买上限'):
            purchase_item(self.member, item.pk, 3)
        self.assertEqual(Purchase.objects.count(), 0)
        purchase_item(self.member, item.pk, 2)
        user = User.objects.create_user('another-buyer')
        member = TeamMembership.objects.create(team=self.team, user=user, display_name='Another')
        CurrencyBalance.objects.create(team=self.team, member=member, balance=100)
        purchase_item(member, item.pk)
        row = self.login(user, self.team).get('/api/currency/shop/items').json()['items'][0]
        self.assertEqual((row['purchased_quantity'], row['purchase_limit_reached']), (1, False))

    def test_enabling_or_lowering_limit_counts_old_delivered_orders(self):
        item = self.item('physical')
        purchase_item(self.member, item.pk, 2)
        ShopFulfillment.objects.update(status='delivered')
        response = self.admin.put('/api/currency/admin/shop',
                                  {'item_id': item.pk, 'purchase_limit': 1}, content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        with self.assertRaisesMessage(ValueError, '已达购买上限'):
            purchase_item(self.member, item.pk)
        self.assertTrue(self.owner.get('/api/currency/shop/items').json()['items'][0]['purchase_limit_reached'])
        response = self.admin.put('/api/currency/admin/shop',
                                  {'item_id': item.pk, 'purchase_limit': 0}, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        purchase_item(self.member, item.pk)

    def test_admin_validation_and_nonphysical_items(self):
        item = self.item('physical')
        for limit in (-1, True, 1.5, '2', 2147483648, None):
            with self.subTest(limit=limit):
                response = self.admin.put('/api/currency/admin/shop',
                    {'item_id': item.pk, 'purchase_limit': limit}, content_type='application/json')
                self.assertEqual(response.status_code, 400)
        item.refresh_from_db()
        self.assertEqual(item.purchase_limit, 0)
        normal = self.item('consumable')
        response = self.admin.put('/api/currency/admin/shop',
                                  {'item_id': normal.pk, 'purchase_limit': 1}, content_type='application/json')
        self.assertEqual(response.status_code, 400)
        purchase_item(self.member, normal.pk, 3)
        self.assertEqual(normal.purchases.get().quantity, 3)
        response = self.post(self.owner, '/api/currency/admin/shop', {'name': 'Forbidden', 'purchase_limit': 1})
        self.assertEqual(response.status_code, 403)


class ConcurrentShopPurchaseLimitTests(TransactionTestCase):
    def test_parallel_orders_cannot_exceed_limit(self):
        root, _ = User.objects.get_or_create(username='admin', defaults={'is_superuser': True, 'is_staff': True})
        team = Team.objects.create(name='Concurrent shop', code='LIMIT-RACE', created_by=root, currency_enabled=True)
        user = User.objects.create_user('concurrent-shop-buyer')
        member = TeamMembership.objects.create(team=team, user=user, display_name='Buyer')
        balance = CurrencyBalance.objects.create(team=team, member=member, balance=100)
        item = ShopItem.objects.create(team=team, name='Limited', price=3, stock=10, item_type='physical', purchase_limit=1)
        barrier = Barrier(2)

        def buy(_):
            close_old_connections()
            try:
                buyer = TeamMembership.objects.get(pk=member.pk)
                barrier.wait(timeout=10)
                try:
                    purchase_item(buyer, item.pk, request_id=str(uuid.uuid4()))
                    return 'purchased'
                except ValueError as error:
                    return str(error)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(buy, range(2)))
        self.assertEqual(results.count('purchased'), 1)
        self.assertTrue(any('已达购买上限' in result for result in results))
        item.refresh_from_db(); balance.refresh_from_db()
        self.assertEqual((item.stock, balance.balance), (9, 97))
        self.assertEqual(Purchase.objects.filter(item=item).count(), 1)
