from importlib import import_module
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from .poker_refunds import settle_pending_refunds


class PaidPokerRefundMigrationTests(TransactionTestCase):
    old = [('club', '0073_user_qualification_reminder')]
    new = [('club', '0084_legacy_poker_refunds')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def setUp(self):
        self.addCleanup(self.migrate, self.new)

    def test_site_that_applied_the_published_0074_keeps_its_existing_refund(self):
        apps = self.migrate([('club', '0083_structure_training_date')])
        chips = apps.get_model('club', 'PokerChipBalance')
        user = apps.get_model('auth', 'User').objects.create(username='already-refunded')
        chips.objects.create(user=user, balance=2000)
        original = import_module('club.migrations.0074_remove_paid_poker')
        with connection.schema_editor() as editor:
            original.refund_and_delete_paid_poker(apps, editor)
            editor.delete_model(chips)

        current = self.migrate(self.new)
        settle_pending_refunds()
        self.assertEqual(current.get_model('club', 'CurrencyBalance').objects.get(user_id=user.pk).balance, 2)
        self.assertEqual(list(current.get_model('club', 'CurrencyTransaction').objects.values_list('amount', flat=True)), [2])
        self.assertFalse(current.get_model('club', 'LegacyPokerRefund').objects.exists())
        self.assertFalse(chips.objects.exists())

    def test_refunds_combined_balances_once_and_keeps_free_rooms(self):
        old = self.migrate(self.old)
        User = old.get_model('auth', 'User')
        Team = old.get_model('club', 'Team')
        Member = old.get_model('club', 'TeamMembership')
        Room = old.get_model('club', 'PokerRoom')
        Seat = old.get_model('club', 'PokerSeat')
        Chips = old.get_model('club', 'PokerChipBalance')
        Balance = old.get_model('club', 'CurrencyBalance')
        first, second, third, fourth = [User.objects.create(username=f'refund-{i}') for i in range(4)]
        team = Team.objects.create(name='Poker', code='POKER-REFUND', created_by=first)
        members = [Member.objects.create(team=team, user=user, display_name=user.username)
                   for user in (first, second, third, fourth)]
        Balance.objects.create(user=first, team=team, member=members[0], balance=4)
        Chips.objects.create(user=first, balance=1700)
        Chips.objects.create(user=second, balance=999)

        paid = Room.objects.create(team=team, created_by=first, mode='coins', name='Paid')
        seat = Seat.objects.create(room=paid, user=first, member=members[0], number=0, stack=200)
        paid.hand = {'street': 'flop', 'players': {str(first.pk): {'total': 150, 'seat_id': seat.pk}}}
        paid.save(update_fields=['hand'])
        finished = Room.objects.create(team=team, created_by=fourth, mode='coins', name='Finished')
        seat = Seat.objects.create(room=finished, user=fourth, member=members[3], number=0, stack=1100)
        finished.hand = {'street': 'finished', 'players': {str(fourth.pk): {'total': 980, 'seat_id': seat.pk}}}
        finished.save(update_fields=['hand'])
        free = Room.objects.create(team=team, created_by=third, mode='friendly', name='Free')
        free_seat = Seat.objects.create(room=free, user=third, member=members[2], number=0, stack=5000)
        practice = Room.objects.create(team=team, created_by=second, mode='practice', name='Solo')

        current = self.migrate(self.new)
        wallets = current.get_model('club', 'CurrencyBalance').objects
        receipts = current.get_model('club', 'LegacyPokerRefund').objects
        self.assertEqual(dict(wallets.values_list('user_id', 'balance')), {first.pk: 4})
        self.assertEqual(dict(receipts.values_list('user_id', 'coins')), {first.pk: 2, second.pk: 0, fourth.pk: 1})
        self.assertEqual(Chips.objects.count(), 2)
        self.assertEqual(Room.objects.filter(mode='coins').count(), 2)
        with patch('club.poker_refunds.CurrencyTransaction.objects.create', side_effect=RuntimeError('interrupted')):
            with self.assertRaisesMessage(RuntimeError, 'interrupted'):
                settle_pending_refunds()
        self.assertEqual(wallets.get(user_id=first.pk).balance, 4)
        self.assertTrue(receipts.filter(user_id=first.pk, settled_at__isnull=True).exists())
        settle_pending_refunds()
        settle_pending_refunds()
        self.assertEqual(dict(wallets.values_list('user_id', 'balance')), {first.pk: 6, fourth.pk: 1})
        refunds = current.get_model('club', 'CurrencyTransaction').objects.filter(transaction_type='poker_cashout')
        self.assertCountEqual(refunds.values_list('user_id', 'amount', 'balance_after'),
                              [(first.pk, 2, 6), (fourth.pk, 1, 1)])
        self.assertCountEqual(current.get_model('club', 'PokerRoom').objects.values_list('pk', flat=True),
                              [free.pk, practice.pk])
        self.assertEqual(list(current.get_model('club', 'PokerSeat').objects.values_list('pk', 'stack')),
                         [(free_seat.pk, 5000)])
        self.assertFalse(Chips.objects.exists())
        self.assertFalse(receipts.filter(settled_at__isnull=True).exists())
