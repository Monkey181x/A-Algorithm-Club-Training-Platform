import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth.models import User
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase

from . import test_shop_fulfillment as fixtures
from .currency_services import purchase_item
from .models import ShopFulfillment, UserInventory, CurrencyTransaction


class ConsumableUseTests(TestCase):
    setUp = fixtures.ShopFulfillmentTests.setUp
    login = fixtures.ShopFulfillmentTests.login
    post = fixtures.ShopFulfillmentTests.post
    item = fixtures.ShopFulfillmentTests.item

    def buy(self, quantity=2):
        item = self.item('consumable')
        purchase_item(self.member, item.pk, quantity)
        inventory = UserInventory.objects.get(item=item, member=self.member)
        return item, inventory, f'/api/currency/inventory/{inventory.pk}/use'

    def test_purchase_stays_available_until_use_and_admin_confirms_consumption(self):
        item, inventory, path = self.buy()
        self.assertEqual(ShopFulfillment.objects.count(), 0)
        self.assertEqual(self.admin.get('/api/currency/admin/dispatch').json()['pending_count'], 0)
        row = self.owner.get('/api/currency/inventory').json()['inventory'][0]
        self.assertTrue(row['can_use'])
        self.assertEqual(row['inventory_id'], inventory.pk)
        token = str(uuid.uuid4())
        response = self.post(self.owner, path, {'request_id': token})
        self.assertEqual(response.status_code, 200, response.content)
        unit = ShopFulfillment.objects.get()
        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity, 1)
        self.assertEqual(unit.status, 'dispatching')
        self.assertEqual(self.post(self.owner, path, {'request_id': token}).json()['duplicate'], True)
        rows = self.owner.get('/api/currency/inventory').json()['inventory']
        self.assertEqual(len(rows), 2)
        self.assertEqual(next(row for row in rows if row.get('status'))['status'], 'dispatching')
        pending = self.admin.get('/api/currency/admin/dispatch').json()
        self.assertEqual(pending['pending_count'], 1)
        self.assertEqual(pending['orders'][0]['item_type'], 'consumable')
        confirm = '/api/currency/admin/dispatch'
        self.assertEqual(self.post(self.admin, confirm, {'id': unit.pk}).status_code, 200)
        self.assertTrue(self.post(self.admin, confirm, {'id': unit.pk}).json()['duplicate'])
        unit.refresh_from_db()
        self.assertEqual(unit.status, 'consumed')
        self.assertIsNotNone(unit.delivered_at)
        self.assertEqual(unit.delivered_by, self.root)
        self.assertEqual(self.post(self.owner, path, {'request_id': token}).json()['status'], 'consumed')
        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity, 1)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.balance, 94)
        self.assertEqual(CurrencyTransaction.objects.filter(member=self.member).count(), 1)

    def test_multiple_purchases_allocate_each_unit_once_and_zero_stock_disappears(self):
        item, inventory, path = self.buy(1)
        purchase_item(self.member, item.pk, 2)
        for _ in range(3):
            self.assertEqual(self.post(self.owner, path, {'request_id': str(uuid.uuid4())}).status_code, 200)
        self.assertEqual(list(ShopFulfillment.objects.order_by('purchase_id','sequence').values_list('sequence', flat=True)), [1, 1, 2])
        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity, 0)
        self.assertEqual(self.post(self.owner, path, {'request_id': str(uuid.uuid4())}).status_code, 409)
        rows = self.owner.get('/api/currency/inventory').json()['inventory']
        self.assertEqual(len(rows), 3)
        self.assertFalse(any(row.get('can_use') for row in rows))

    def test_nonowners_wrong_types_and_nonadmins_cannot_use_or_confirm(self):
        item, inventory, path = self.buy()
        self.assertEqual(self.post(self.outsider, path, {'request_id': str(uuid.uuid4())}).status_code, 404)
        self.assertEqual(self.post(self.admin, path, {'request_id': str(uuid.uuid4())}).status_code, 403)
        self.assertEqual(self.post(self.owner, path, {}).status_code, 400)
        ticket = self.item('makeup_ticket')
        purchase_item(self.member, ticket.pk)
        ticket_inventory = UserInventory.objects.get(item=ticket)
        self.assertEqual(self.post(self.owner, f'/api/currency/inventory/{ticket_inventory.pk}/use', {'request_id': str(uuid.uuid4())}).status_code, 404)
        unit_id = self.post(self.owner, path, {'request_id': str(uuid.uuid4())}).json()['fulfillment_id']
        self.assertEqual(self.post(self.owner, '/api/currency/admin/dispatch', {'id': unit_id}).status_code, 403)
        self.assertEqual(self.post(self.outsider, '/api/currency/admin/dispatch', {'id': unit_id}).status_code, 404)
        self.assertEqual(self.outsider.get('/api/currency/admin/dispatch').json()['pending_count'], 0)

    def test_prior_inventory_remains_usable_after_delisting_and_tokens_cannot_cross_items(self):
        item, inventory, path = self.buy()
        item.is_active = False
        item.save(update_fields=['is_active'])
        token = str(uuid.uuid4())
        self.assertEqual(self.post(self.owner, path, {'request_id': token}).status_code, 200)
        other_item = self.item('consumable')
        purchase_item(self.member, other_item.pk)
        other_inventory = UserInventory.objects.get(item=other_item)
        response = self.post(self.owner, f'/api/currency/inventory/{other_inventory.pk}/use', {'request_id': token})
        self.assertEqual(response.status_code, 409)
        other_inventory.refresh_from_db()
        self.assertEqual(other_inventory.quantity, 1)


class ConcurrentConsumableUseTests(TransactionTestCase):
    login = fixtures.ShopFulfillmentTests.login
    post = fixtures.ShopFulfillmentTests.post
    item = fixtures.ShopFulfillmentTests.item

    def setUp(self):
        User.objects.get_or_create(username='admin', defaults={'is_superuser': True, 'is_staff': True})
        fixtures.ShopFulfillmentTests.setUp(self)

    def test_simultaneous_same_request_only_uses_one_unit(self):
        self.concurrent_use(quantity=2, shared_token=True, expected=[200, 200], remaining=1)

    def test_simultaneous_distinct_requests_cannot_overspend_last_unit(self):
        self.concurrent_use(quantity=1, shared_token=False, expected=[200, 409], remaining=0)

    def concurrent_use(self, quantity, shared_token, expected, remaining):
        item = self.item('consumable')
        purchase_item(self.member, item.pk, quantity)
        inventory = UserInventory.objects.get(item=item)
        path = f'/api/currency/inventory/{inventory.pk}/use'
        clients = [self.login(self.user, self.team), self.login(self.user, self.team)]
        token = str(uuid.uuid4())
        barrier = Barrier(2)
        def use(client):
            close_old_connections()
            try:
                barrier.wait()
                return self.post(client, path, {'request_id': token if shared_token else str(uuid.uuid4())}).status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(use, clients)), expected)
        inventory.refresh_from_db()
        self.assertEqual(inventory.quantity, remaining)
        self.assertEqual(ShopFulfillment.objects.count(), 1)
