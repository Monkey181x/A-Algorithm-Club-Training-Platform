from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class PaidPokerRefundMigrationTests(TransactionTestCase):
    old = [('club', '0073_user_qualification_reminder')]
    new = [('club', '0074_remove_paid_poker')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def setUp(self):
        self.addCleanup(self.migrate, self.new)

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
        self.migrate(self.new)
        wallets = current.get_model('club', 'CurrencyBalance').objects
        self.assertEqual(dict(wallets.values_list('user_id', 'balance')), {first.pk: 6, fourth.pk: 1})
        refunds = current.get_model('club', 'CurrencyTransaction').objects.filter(transaction_type='poker_cashout')
        self.assertCountEqual(refunds.values_list('user_id', 'amount', 'balance_after'),
                              [(first.pk, 2, 6), (fourth.pk, 1, 1)])
        self.assertCountEqual(current.get_model('club', 'PokerRoom').objects.values_list('pk', flat=True),
                              [free.pk, practice.pk])
        self.assertEqual(list(current.get_model('club', 'PokerSeat').objects.values_list('pk', 'stack')),
                         [(free_seat.pk, 5000)])
        self.assertNotIn(Chips._meta.db_table, connection.introspection.table_names())
