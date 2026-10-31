from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from . import test_shop_fulfillment as fixtures
from .currency_services import purchase_item
from .models import CurrencyBalance, CurrencyTransaction, Purchase, RetirementSubsidy, ShopItem, Team, TeamMembership, UserProfile
from .rankings import ranking_data
from .retirement import ensure_retirement_subsidies, sync_retirement_subsidies


class RetirementTests(TestCase):
    setUp = fixtures.ShopFulfillmentTests.setUp
    login = fixtures.ShopFulfillmentTests.login
    post = fixtures.ShopFulfillmentTests.post
    item = fixtures.ShopFulfillmentTests.item

    def senior(self, grade=4, day=None):
        return UserProfile.objects.create(user=self.user, grade=grade, grade_date=day or timezone.localdate())

    def test_each_team_gets_10000_once_with_exact_source(self):
        self.senior()
        second = TeamMembership.objects.create(team=self.other, user=self.user, display_name='Owner')
        CurrencyBalance.objects.create(team=self.other, member=second, balance=7)
        response = self.owner.get('/api/currency/balance')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['balance'], 10100)
        self.assertEqual(CurrencyBalance.objects.get(member=second).balance, 10007)
        self.assertEqual(ensure_retirement_subsidies(self.user.pk), 0)
        self.assertEqual(sync_retirement_subsidies()['granted'], 0)
        self.assertEqual(RetirementSubsidy.objects.filter(user=self.user).count(), 2)
        transactions = CurrencyTransaction.objects.filter(member__user=self.user)
        self.assertEqual(transactions.count(), 2)
        for entry in transactions:
            self.assertEqual((entry.amount, entry.description, entry.get_transaction_type_display()), (10000, '退休金补贴', '退休金补贴'))

    def test_auto_promotion_and_existing_seniors_are_backfilled(self):
        self.senior(3, date(2026, 7, 31))
        with patch('club.grades.timezone.localdate', return_value=date(2026, 7, 31)):
            self.assertEqual(ensure_retirement_subsidies(self.user.pk), 0)
            self.assertIn(self.member.pk, [row['memberId'] for row in ranking_data(self.member, 'wealth')['rows']])
        with patch('club.grades.timezone.localdate', return_value=date(2026, 8, 1)):
            self.assertEqual(sync_retirement_subsidies()['granted'], 1)
            self.assertNotIn(self.member.pk, [row['memberId'] for row in ranking_data(self.member, 'wealth')['rows']])
            self.assertIn(self.member.pk, [row['memberId'] for row in ranking_data(self.member, 'checkin')['rows']])
            self.assertEqual(sync_retirement_subsidies()['granted'], 0)
        self.balance.refresh_from_db()
        self.assertEqual(self.balance.balance, 10100)

    def test_rejoining_does_not_repeat_but_new_team_receives_its_subsidy(self):
        self.senior()
        self.assertEqual(ensure_retirement_subsidies(self.user.pk), 1)
        self.member.delete()
        replacement = TeamMembership.objects.create(team=self.team, user=self.user, display_name='Returned')
        self.assertEqual(ensure_retirement_subsidies(self.user.pk), 0)
        self.assertFalse(CurrencyBalance.objects.filter(member=replacement).exists())
        second = TeamMembership.objects.create(team=self.other, user=self.user, display_name='New team')
        self.assertEqual(ensure_retirement_subsidies(self.user.pk), 1)
        self.assertEqual(CurrencyBalance.objects.get(member=second).balance, 10000)

    def test_physical_products_blocked_in_api_and_purchase_service(self):
        self.senior()
        physical = self.item('physical', stock=10)
        normal = self.item('consumable')
        rows = {row['id']: row for row in self.owner.get('/api/currency/shop/items').json()['items']}
        self.assertTrue(rows[physical.pk]['purchase_blocked'])
        self.assertFalse(rows[normal.pk]['purchase_blocked'])
        response = self.post(self.owner, '/api/currency/shop/purchase', {'item_id': physical.pk, 'quantity': 1, 'item_type': 'general'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('大三+', response.json()['error'])
        physical.refresh_from_db(); self.balance.refresh_from_db()
        self.assertEqual((physical.stock, self.balance.balance), (10, 100))
        self.assertEqual(Purchase.objects.count(), 0)
        self.assertEqual(CurrencyTransaction.objects.count(), 0)
        purchase_item(self.member, normal.pk)
        self.assertEqual(Purchase.objects.get().item_id, normal.pk)

    def test_automatic_promotion_blocks_physical_without_first_loading_balance(self):
        self.senior(3, date(2026, 7, 31))
        item = self.item('physical')
        with patch('club.grades.timezone.localdate', return_value=date(2026, 8, 1)):
            with self.assertRaisesMessage(ValueError, '大三+'):
                purchase_item(self.member, item.pk)
        self.assertEqual(RetirementSubsidy.objects.count(), 0)

    def test_missing_grade_and_disabled_currency_do_not_grant(self):
        self.assertEqual(ensure_retirement_subsidies(self.user.pk), 0)
        self.senior()
        self.team.currency_enabled = False
        self.team.save(update_fields=['currency_enabled'])
        self.assertEqual(ensure_retirement_subsidies(self.user.pk), 0)
        self.team.currency_enabled = True
        self.team.save(update_fields=['currency_enabled'])
        self.assertEqual(ensure_retirement_subsidies(self.user.pk), 1)
        UserProfile.objects.create(user=self.root, grade=4, grade_date=timezone.localdate())
        self.assertEqual(ensure_retirement_subsidies(self.root.pk), 0)


class ConcurrentRetirementTests(TransactionTestCase):
    def test_simultaneous_balance_reads_only_grant_once(self):
        user = User.objects.create_user('retirement-race')
        team = Team.objects.create(name='Retirement race', code='RETIRE-RACE', created_by=user, currency_enabled=True)
        member = TeamMembership.objects.create(team=team, user=user, display_name='Senior')
        UserProfile.objects.create(user=user, grade=4, grade_date=timezone.localdate())
        barrier = Barrier(2)

        def grant(_):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return ensure_retirement_subsidies(user.pk)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(grant, range(2))), [0, 1])
        self.assertEqual(CurrencyBalance.objects.get(member=member).balance, 10000)
        self.assertEqual(RetirementSubsidy.objects.count(), 1)
        self.assertEqual(CurrencyTransaction.objects.count(), 1)
