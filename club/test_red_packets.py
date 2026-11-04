import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import close_old_connections, connection
from django.db.models import Sum
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone

from .models import CurrencyBalance, CurrencyTransaction, RedPacket, RedPacketClaim, Team, TeamMembership
from .red_packet_services import PacketError, claim_packet, expire_packet, send_packet, settle_expired_packets


class PacketFixtures:
    def setUp(self):
        self.admin, _ = User.objects.get_or_create(username='admin', defaults={'is_superuser': True})
        self.team = Team.objects.create(name='红包团队', code='PACKETS', created_by=self.admin, currency_enabled=True)
        self.other = Team.objects.create(name='其他团队', code='PACKETOTHER', created_by=self.admin, currency_enabled=True)
        self.users, self.members, self.clients = [], [], []
        for index in range(4):
            user = User.objects.create_user(f'packet-user-{index}')
            member = TeamMembership.objects.create(team=self.team, user=user, display_name=f'成员{index}')
            CurrencyBalance.objects.create(user=user, team=self.team, member=member, balance=1000)
            client = Client(); client.force_login(user)
            session = client.session; session['team_id'] = self.team.pk; session.save()
            self.users.append(user); self.members.append(member); self.clients.append(client)
        self.client = self.clients[0]

    def data(self, **kwargs):
        return {'amount': 20, 'count': 4, 'kind': 'random', 'message': '一起进步！',
                'request_id': str(uuid.uuid4()), **kwargs}

    def post(self, path, data=None, index=0):
        return self.clients[index].post(path, json.dumps(data or {}), content_type='application/json')

    def send(self, **kwargs):
        response = self.post('/api/red-packets', self.data(**kwargs))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['packet']

    def claim(self, packet, index=0):
        return self.post(f'/api/red-packets/{packet["id"]}/claim', index=index)

    def assert_conserved(self):
        wallets = CurrencyBalance.objects.aggregate(total=Sum('balance'))['total'] or 0
        escrow = RedPacket.objects.aggregate(total=Sum('remaining_amount'))['total'] or 0
        self.assertEqual(wallets + escrow, 4000)


class RedPacketTests(PacketFixtures, TestCase):
    def test_send_debits_exactly_once_and_conflicting_reuse_is_rejected(self):
        data = self.data()
        first = self.post('/api/red-packets', data)
        self.assertEqual(first.status_code, 200, first.content)
        second = self.post('/api/red-packets', data)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(first.json()['balance'], 980)
        self.assertEqual(RedPacket.objects.count(), 1)
        self.assertEqual(list(CurrencyTransaction.objects.values_list('amount', flat=True)), [-20])
        for update in [{'amount': 24}, {'count': 2}, {'kind': 'equal'}, {'message': 'changed'}]:
            self.assertEqual(self.post('/api/red-packets', {**data, **update}).status_code, 409)
        self.assert_conserved()

    def test_validation_does_not_create_or_debit(self):
        for field, values in {'amount': [0, -1, True, 1.5, '20', None, 2147483648, 1001],
                              'count': [0, -1, True, 1.5, '2', None, 101, 21],
                              'kind': ['other', None, {}], 'message': [None, 'x' * 101],
                              'request_id': ['', None, 'invalid']}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    response = self.post('/api/red-packets', self.data(**{field: value}))
                    self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(self.post('/api/red-packets', self.data(kind='equal', amount=21)).status_code, 400)
        self.assertFalse(RedPacket.objects.exists())
        self.assertFalse(CurrencyTransaction.objects.exists())
        self.assert_conserved()

    def test_equal_packets_and_one_claim_per_account_including_sender(self):
        packet = self.send(kind='equal')
        for index in range(4):
            for attempt in range(2):
                response = self.claim(packet, index)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()['amount'], 5)
        stored = RedPacket.objects.get()
        self.assertEqual((stored.remaining_count, stored.remaining_amount), (0, 0))
        self.assertEqual(RedPacketClaim.objects.count(), 4)
        self.assertEqual(CurrencyTransaction.objects.filter(transaction_type='red_packet_received').count(), 4)
        self.assert_conserved()

    def test_random_allocation_reserves_one_each_and_final_claim_gets_remainder(self):
        for bound in ('upper', 'lower'):
            with self.subTest(bound=bound):
                packet = self.send(amount=21)
                received = []
                with patch('club.red_packet_services.secrets.randbelow', side_effect=lambda n: n - 1 if bound == 'upper' else 0):
                    for index in range(4):
                        response = self.claim(packet, index)
                        self.assertEqual(response.status_code, 200, response.content)
                        received.append(response.json()['amount'])
                self.assertTrue(all(amount >= 1 for amount in received))
                self.assertEqual(sum(received), 21)
                self.assertEqual(RedPacket.objects.get(pk=packet['id']).remaining_amount, 0)
                self.assert_conserved()

    def test_minimum_packets_and_exhaustion(self):
        packet = self.send(amount=3, count=3)
        for index in range(3):
            self.assertEqual(self.claim(packet, index).json()['amount'], 1)
        self.assertEqual(self.claim(packet, 3).status_code, 409)
        self.assertEqual(RedPacketClaim.objects.count(), 3)
        self.assert_conserved()

    def test_packet_count_has_no_fixed_limit_and_large_packets_allocate_lazily(self):
        for count in (101, 40000, 2147483647):
            for kind in ('random', 'equal'):
                with self.subTest(count=count, kind=kind):
                    CurrencyBalance.objects.filter(user=self.users[0]).update(balance=2147483647)
                    packet = self.send(amount=count, count=count, kind=kind)
                    stored = RedPacket.objects.get(pk=packet['id'])
                    self.assertEqual(stored.total_count, count)
                    self.assertEqual(self.claim(packet, 1).json()['amount'], 1)
                    stored.refresh_from_db()
                    self.assertEqual((stored.remaining_count, stored.remaining_amount), (count - 1, count - 1))
                    self.assertEqual(stored.claims.count(), 1)

    def test_square_orders_available_before_newer_completed_and_paginates_all(self):
        older_open = self.send(amount=1, count=1)
        completed = self.send(amount=1, count=1)
        self.claim(completed, 1)
        newer_open = self.send(amount=1, count=1)
        result = self.client.get('/api/red-packets').json()
        self.assertEqual([p['id'] for p in result['packets']], [newer_open['id'], older_open['id'], completed['id']])
        for _ in range(23):
            self.send(amount=1, count=1)
        pages = [self.client.get('/api/red-packets?page=' + str(page)).json() for page in (1, 2)]
        self.assertEqual((pages[0]['total'], pages[0]['pages']), (26, 2))
        ids = [p['id'] for result in pages for p in result['packets']]
        self.assertEqual(len(set(ids)), 26)
        self.assertEqual(ids[-1], completed['id'])
        for value in ['0', '-1', 'abc', '2147483648']:
            self.assertEqual(self.client.get('/api/red-packets?page=' + value).status_code, 400)

    def test_permissions_cross_team_and_disabled_currency(self):
        packet = self.send()
        client = Client()
        for method, path in [('get', '/api/red-packets'), ('post', '/api/red-packets'),
                             ('get', f'/api/red-packets/{packet["id"]}'), ('post', f'/api/red-packets/{packet["id"]}/claim')]:
            self.assertEqual(getattr(client, method)(path).status_code, 401)
        TeamMembership.objects.create(team=self.other, user=self.users[1], display_name='跨团队成员')
        session = self.clients[1].session; session['team_id'] = self.other.pk; session.save()
        self.assertEqual(self.clients[1].get('/api/red-packets').json()['packets'], [])
        self.assertEqual(self.clients[1].get(f'/api/red-packets/{packet["id"]}').status_code, 404)
        self.assertEqual(self.claim(packet, 1).status_code, 404)
        self.team.currency_enabled = False; self.team.save()
        self.assertEqual(self.post('/api/red-packets', self.data()).status_code, 400)
        self.assertEqual(self.claim(packet).status_code, 400)
        result = self.client.get('/api/red-packets').json()
        self.assertFalse(result['can_send'])
        self.assertEqual(len(result['packets']), 1)
        self.client.force_login(self.admin)
        session = self.client.session; session['team_id'] = self.team.pk; session.save()
        self.assertEqual(self.client.get('/api/red-packets').status_code, 200)
        self.assertEqual(self.post('/api/red-packets', self.data()).status_code, 403)
        self.assertEqual(self.claim(packet).status_code, 403)

    def test_global_wallet_shared_and_negative_wallet_cannot_send(self):
        CurrencyBalance.objects.filter(user=self.users[0]).update(team=self.other, member=None)
        packet = self.send()
        self.assertEqual(self.claim(packet, 1).status_code, 200)
        self.assertEqual(CurrencyBalance.objects.filter(user=self.users[0]).count(), 1)
        self.assert_conserved()
        CurrencyBalance.objects.filter(user=self.users[0]).update(balance=-1)
        self.assertEqual(self.post('/api/red-packets', self.data()).status_code, 400)

    def test_send_and_claim_failures_roll_back_escrow_wallet_receipts_and_ledger(self):
        with patch('club.red_packet_services.deduct_currency', side_effect=ValueError('debit failure')):
            self.assertEqual(self.post('/api/red-packets', self.data()).status_code, 400)
        self.assertFalse(RedPacket.objects.exists())
        packet = self.send()
        with patch('club.red_packet_services.add_currency', side_effect=ValueError('credit failure')):
            self.assertEqual(self.claim(packet, 1).status_code, 400)
        self.assertFalse(RedPacketClaim.objects.exists())
        stored = RedPacket.objects.get()
        self.assertEqual((stored.remaining_count, stored.remaining_amount), (4, 20))
        self.assertEqual(CurrencyTransaction.objects.count(), 1)
        self.assert_conserved()

    def test_receiver_balance_overflow_rolls_back_and_debt_receives_normally(self):
        packet = self.send(amount=10, count=1)
        CurrencyBalance.objects.filter(user=self.users[1]).update(balance=2147483647)
        self.assertEqual(self.claim(packet, 1).status_code, 400)
        self.assertFalse(RedPacketClaim.objects.exists())
        self.assertEqual(RedPacket.objects.get().remaining_count, 1)
        CurrencyBalance.objects.filter(user=self.users[1]).update(balance=-20)
        response = self.claim(packet, 1)
        self.assertEqual(response.json(), {'amount': 10, 'balance': -10})

    def test_details_names_and_wallet_history(self):
        packet = self.send(kind='equal', message='<script>test</script>')
        self.claim(packet, 1)
        self.members[0].display_name = '新昵称'; self.members[0].save()
        result = self.clients[1].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertEqual((result['sender'], result['my_amount']), ('新昵称', 5))
        self.assertEqual(result['claims'][0]['name'], self.members[1].display_name)
        self.assertTrue(result['claims'][0]['is_me'])
        entry = self.clients[1].get('/api/currency/transactions').json()['transactions'][0]
        self.assertEqual((entry['type_display'], entry['amount']), ('领红包', 5))
        User.objects.filter(pk=self.users[0].pk).update(is_active=False)
        User.objects.filter(pk=self.users[1].pk).update(is_active=False)
        self.claim(packet, 2)
        result = self.clients[2].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertEqual(result['sender'], '已注销用户')
        self.assertEqual(result['claims'][0]['name'], '已注销用户')

    def test_service_rechecks_stale_membership_before_mutating(self):
        packet = self.send()
        TeamMembership.objects.filter(pk=self.members[1].pk).update(is_active=False)
        with self.assertRaises(PacketError):
            claim_packet(self.members[1], packet['id'])
        with self.assertRaises(PacketError):
            send_packet(self.members[1], self.data())
        self.assertFalse(RedPacketClaim.objects.exists())
        self.assert_conserved()

    def test_account_closure_preserves_escrow_and_anonymizes_details(self):
        from .account_views import close_user
        packet = self.send(kind='equal')
        self.claim(packet, 1)
        close_user(self.users[0].pk, self.admin)
        close_user(self.users[1].pk, self.admin)
        self.assertEqual(self.claim(packet, 2).json()['amount'], 5)
        result = self.clients[2].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertEqual(result['sender'], '已注销用户')
        self.assertEqual(result['claims'][0]['name'], '已注销用户')
        self.assertEqual((result['remaining_count'], result['remaining_amount']), (2, 10))
        self.assertEqual(self.claim(packet, 2).json()['amount'], 5)
        with self.assertRaises(PacketError):
            claim_packet(self.members[0], packet['id'])
        self.assert_conserved()

    def test_packet_values_and_claim_records_stay_hidden_until_viewer_claims(self):
        packet = self.send(kind='equal')
        self.claim(packet, 1)
        private_fields = {'total_amount', 'total_count', 'remaining_amount', 'remaining_count', 'claims'}
        for index in (0, 2):
            detail = self.clients[index].get(f'/api/red-packets/{packet["id"]}').json()['packet']
            card = self.clients[index].get('/api/red-packets').json()['packets'][0]
            for projection in (detail, card):
                self.assertFalse(projection['is_claimed'])
                self.assertFalse(projection['is_exhausted'])
                self.assertTrue(private_fields.isdisjoint(projection))
        received = self.clients[1].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertTrue(received['is_claimed'])
        self.assertEqual((received['my_amount'], received['total_amount'], received['total_count']), (5, 20, 4))
        self.assertEqual(len(received['claims']), 1)
        for index in (0, 2, 3):
            self.claim(packet, index)
        # Exhaustion never reveals another user's unclaimed packet values.
        other = self.send(amount=1, count=1)
        self.claim(other, 1)
        hidden = self.clients[2].get(f'/api/red-packets/{other["id"]}').json()['packet']
        self.assertTrue(hidden['is_exhausted'])
        self.assertFalse(hidden['is_claimed'])
        self.assertTrue(private_fields.isdisjoint(hidden))

    def test_sender_avatar_uses_current_team_member_image_and_hides_closed_identity(self):
        from .models import UserProfile
        profile, _ = UserProfile.objects.get_or_create(user=self.users[0])
        profile.avatar = 'avatars/red-packet-test.jpg'
        profile.save()
        packet = self.send()
        detail = self.clients[1].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertTrue(detail['sender_avatar'].startswith(f'/api/members/{self.members[0].pk}/avatar?size=small&v='))
        self.assertEqual(detail['sender'], self.members[0].display_name)
        User.objects.filter(pk=self.users[0].pk).update(is_active=False)
        detail = self.clients[1].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertIsNone(detail['sender_avatar'])
        self.assertEqual(detail['sender'], '已注销用户')

    def test_rejoining_team_does_not_allow_a_second_claim(self):
        packet = self.send(kind='equal')
        original = self.claim(packet, 1).json()
        self.members[1].delete()
        TeamMembership.objects.create(team=self.team, user=self.users[1], display_name='重新入队')
        self.assertEqual(self.claim(packet, 1).json(), original)
        self.assertEqual(RedPacketClaim.objects.count(), 1)
        self.assert_conserved()

    def test_maximum_amount_and_default_message(self):
        CurrencyBalance.objects.filter(user=self.users[0]).update(balance=2147483647)
        packet = self.send(amount=2147483647, count=1, message='   ')
        self.assertEqual(packet['message'], '恭喜发财，大吉大利！')
        self.assertEqual(self.claim(packet).json(), {'amount': 2147483647, 'balance': 2147483647})
        self.assertEqual(list(CurrencyTransaction.objects.order_by('pk').values_list('amount', flat=True)),
                         [-2147483647, 2147483647])

    def test_open_escrow_protects_team_deletion_until_claimed(self):
        from django.db.models.deletion import ProtectedError
        from .management_views import delete_team_data
        packet = self.send(amount=7, count=1)
        with self.assertRaises(ProtectedError):
            delete_team_data(self.team)
        self.assertEqual(RedPacket.objects.get().remaining_amount, 7)
        self.assert_conserved()
        self.claim(packet, 1)
        delete_team_data(self.team)
        self.assertFalse(RedPacket.objects.exists())
        self.assertEqual(CurrencyBalance.objects.aggregate(total=Sum('balance'))['total'], 4000)


class RedPacketExpiryTests(PacketFixtures, TestCase):
    def future(self):
        return (timezone.now() + timedelta(hours=1)).isoformat()

    def expire(self, packet):
        RedPacket.objects.filter(pk=packet['id']).update(expires_at=timezone.now() - timedelta(seconds=1))

    def test_unlimited_default_and_expiry_validation(self):
        packet = self.send()
        self.assertIsNone(packet['expires_at'])
        self.assertFalse(packet['is_expired'])
        for value in [True, 0, {}, [], '', 'invalid', '2027-01-01T00:00:00',
                      (timezone.now() - timedelta(seconds=1)).isoformat()]:
            with self.subTest(value=value):
                self.assertEqual(self.post('/api/red-packets', self.data(expires_at=value)).status_code, 400)
        self.assertEqual(RedPacket.objects.count(), 1)
        self.assert_conserved()

    def test_expiry_is_idempotent_but_request_token_cannot_change_deadline(self):
        data = self.data(expires_at=self.future())
        first = self.post('/api/red-packets', data)
        self.assertEqual(first.status_code, 200, first.content)
        packet = first.json()['packet']
        self.assertEqual(self.post('/api/red-packets', data).json(), first.json())
        self.assertEqual(self.post('/api/red-packets', {**data, 'expires_at': None}).status_code, 409)
        self.expire(packet)
        # An ordinary retry with the original, now-past deadline still resolves the receipt.
        original_deadline = RedPacket.objects.get(pk=packet['id']).expires_at
        data['expires_at'] = original_deadline.isoformat()
        self.assertEqual(self.post('/api/red-packets', data).status_code, 200)
        self.assertEqual(CurrencyTransaction.objects.filter(transaction_type='red_packet_sent').count(), 1)
        self.assertEqual(CurrencyTransaction.objects.filter(transaction_type='red_packet_refund').count(), 1)
        self.assert_conserved()

    def test_partial_expiry_refunds_only_unclaimed_amount_once_even_when_claim_rejected(self):
        packet = self.send(kind='equal', expires_at=self.future())
        self.claim(packet, 1)
        self.expire(packet)
        for _ in range(2):
            self.assertEqual(self.claim(packet, 2).status_code, 409)
            self.client.get('/api/red-packets')
            settle_expired_packets()
        stored = RedPacket.objects.get()
        self.assertEqual((stored.remaining_count, stored.remaining_amount, stored.refunded_amount), (3, 0, 15))
        self.assertIsNotNone(stored.refunded_at)
        self.assertEqual(CurrencyBalance.objects.get(user=self.users[0]).balance, 995)
        self.assertEqual(CurrencyTransaction.objects.filter(transaction_type='red_packet_refund').count(), 1)
        self.assertEqual(self.claim(packet, 1).json()['amount'], 5)
        hidden = self.clients[2].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertTrue(hidden['is_expired'])
        self.assertTrue(hidden['is_refunded'])
        self.assertFalse(hidden['is_exhausted'])
        self.assertNotIn('total_amount', hidden)
        self.assertNotIn('remaining_count', hidden)
        self.assert_conserved()

    def test_expired_packets_move_behind_open_packets_and_closed_team_can_refund(self):
        opened = self.send(amount=1, count=1)
        expired = self.send(expires_at=self.future())
        self.expire(expired)
        self.team.currency_enabled = False; self.team.save()
        result = self.client.get('/api/red-packets').json()['packets']
        self.assertEqual([p['id'] for p in result], [opened['id'], expired['id']])
        self.assertTrue(result[-1]['is_refunded'])
        self.assert_conserved()

    def test_closed_sender_and_removed_membership_still_receive_global_wallet_refund(self):
        from .account_views import close_user
        packet = self.send(expires_at=self.future())
        close_user(self.users[0].pk, self.admin)
        self.members[0].delete()
        self.expire(packet)
        self.assertEqual(expire_packet(packet['id']), 20)
        self.assertEqual(CurrencyBalance.objects.get(user=self.users[0]).balance, 1000)
        self.assert_conserved()

    def test_refund_overflow_keeps_escrow_and_retries_without_duplication(self):
        packet = self.send(expires_at=self.future())
        self.expire(packet)
        CurrencyBalance.objects.filter(user=self.users[0]).update(balance=2147483647)
        self.assertEqual(expire_packet(packet['id']), 0)
        stored = RedPacket.objects.get()
        self.assertEqual(stored.remaining_amount, 20)
        self.assertIsNone(stored.refunded_at)
        self.assertFalse(CurrencyTransaction.objects.filter(transaction_type='red_packet_refund').exists())
        CurrencyBalance.objects.filter(user=self.users[0]).update(balance=980)
        self.assertEqual(expire_packet(packet['id']), 20)
        self.assertEqual(expire_packet(packet['id']), 0)
        self.assert_conserved()

    def test_full_wallet_does_not_block_other_senders_refunds(self):
        blocked = self.send(expires_at=self.future())
        other = send_packet(self.members[2], self.data(expires_at=self.future()))
        self.expire(blocked)
        RedPacket.objects.filter(pk=other.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        CurrencyBalance.objects.filter(user=self.users[0]).update(balance=2147483647)
        self.assertEqual(settle_expired_packets(limit=1), 1)
        other.refresh_from_db()
        self.assertEqual(other.remaining_amount, 0)
        self.assertEqual(RedPacket.objects.get(pk=blocked['id']).remaining_amount, 20)
        CurrencyBalance.objects.filter(user=self.users[0]).update(balance=980)
        self.assertEqual(settle_expired_packets(limit=1), 1)
        self.assert_conserved()

    def test_unlimited_and_completed_packets_never_refund(self):
        unlimited = self.send()
        completed = self.send(amount=7, count=1, expires_at=self.future())
        self.claim(completed, 1)
        self.expire(completed)
        self.assertEqual(settle_expired_packets(), 0)
        self.assertEqual(expire_packet(unlimited['id']), 0)
        self.assertEqual(RedPacket.objects.get(pk=unlimited['id']).remaining_amount, 20)
        self.assertFalse(CurrencyTransaction.objects.filter(transaction_type='red_packet_refund').exists())
        self.assert_conserved()

    def test_refund_transaction_failure_keeps_wallet_and_escrow_intact(self):
        packet = self.send(expires_at=self.future())
        self.expire(packet)
        with patch('club.red_packet_services.CurrencyTransaction.objects.create', side_effect=RuntimeError('ledger failure')):
            with self.assertRaises(RuntimeError):
                expire_packet(packet['id'])
        self.assertEqual(CurrencyBalance.objects.get(user=self.users[0]).balance, 980)
        self.assertEqual(RedPacket.objects.get().remaining_amount, 20)
        self.assertIsNone(RedPacket.objects.get().refunded_at)
        self.assert_conserved()

    def test_best_luck_only_appears_after_random_packet_finishes(self):
        packet = self.send(amount=20, count=3)
        with patch('club.red_packet_services.secrets.randbelow', side_effect=[9, 2]):
            self.claim(packet, 0)
            self.claim(packet, 1)
            detail = self.client.get(f'/api/red-packets/{packet["id"]}').json()['packet']
            self.assertNotIn('best_luck', detail)
            self.assertFalse(any(c['is_best_luck'] for c in detail['claims']))
            self.claim(packet, 2)
        detail = self.client.get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertEqual([c['amount'] for c in detail['claims'] if c['is_best_luck']], [10])
        self.assertFalse(any(c['is_tied_best'] for c in detail['claims']))
        hidden = self.clients[3].get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertEqual(hidden['best_luck'][0]['name'], self.members[0].display_name)
        self.assertNotIn('total_amount', hidden)
        self.assertNotIn('claims', hidden)
        self.assert_conserved()

    def test_tied_best_luck_and_no_best_luck_for_equal_or_expired_packets(self):
        packet = self.send(amount=9, count=3)
        with patch('club.red_packet_services.secrets.randbelow', return_value=2):
            for index in range(3):
                self.claim(packet, index)
        detail = self.client.get(f'/api/red-packets/{packet["id"]}').json()['packet']
        self.assertEqual(len(detail['best_luck']), 3)
        self.assertTrue(all(c['is_best_luck'] and c['is_tied_best'] for c in detail['claims']))
        equal = self.send(amount=1, count=1, kind='equal')
        self.claim(equal)
        detail = self.client.get(f'/api/red-packets/{equal["id"]}').json()['packet']
        self.assertFalse(any(c['is_best_luck'] for c in detail['claims']))
        expired = self.send(expires_at=self.future())
        self.claim(expired)
        self.expire(expired)
        detail = self.client.get(f'/api/red-packets/{expired["id"]}').json()['packet']
        self.assertNotIn('best_luck', detail)
        self.assertFalse(any(c['is_best_luck'] for c in detail['claims']))
        self.assert_conserved()

    def test_expired_refunded_packet_allows_team_deletion(self):
        from .management_views import delete_team_data
        packet = self.send(expires_at=self.future())
        self.expire(packet)
        expire_packet(packet['id'])
        delete_team_data(self.team)
        self.assertFalse(RedPacket.objects.exists())
        self.assert_conserved()


@skipUnless(connection.vendor == 'postgresql', 'Requires PostgreSQL row locks')
class RedPacketConcurrencyTests(PacketFixtures, TransactionTestCase):
    def race(self, jobs):
        barrier = Barrier(len(jobs))
        def run(job):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return job()
            except ValueError as error:
                return getattr(error, 'status', 400)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            return list(pool.map(run, jobs))

    def test_two_claimants_racing_for_final_packet_only_credit_one(self):
        packet = self.send(amount=7, count=1)
        results = self.race([lambda: claim_packet(self.members[1], packet['id']),
                             lambda: claim_packet(self.members[2], packet['id'])])
        self.assertEqual(sum(isinstance(result, RedPacketClaim) for result in results), 1)
        self.assertIn(409, results)
        self.assertEqual(RedPacketClaim.objects.count(), 1)
        self.assertEqual(RedPacket.objects.get().remaining_amount, 0)
        self.assertEqual(CurrencyTransaction.objects.filter(transaction_type='red_packet_received').count(), 1)
        self.assert_conserved()

    def test_same_user_concurrent_claims_return_same_receipt(self):
        packet = self.send(amount=7, count=1)
        results = self.race([lambda: claim_packet(self.members[1], packet['id'])] * 2)
        self.assertEqual(results[0].pk, results[1].pk)
        self.assertEqual(RedPacketClaim.objects.count(), 1)
        self.assert_conserved()

    def test_duplicate_concurrent_sends_debit_only_once(self):
        data = self.data()
        results = self.race([lambda: send_packet(self.members[0], data)] * 2)
        self.assertEqual(results[0].pk, results[1].pk)
        self.assertEqual(RedPacket.objects.count(), 1)
        self.assertEqual(CurrencyTransaction.objects.count(), 1)
        self.assert_conserved()

    def test_distinct_concurrent_sends_cannot_overspend(self):
        first, second = self.data(amount=800), self.data(amount=800)
        results = self.race([lambda: send_packet(self.members[0], first),
                             lambda: send_packet(self.members[0], second)])
        self.assertEqual(sum(isinstance(result, RedPacket) for result in results), 1)
        self.assertEqual(RedPacket.objects.count(), 1)
        self.assertEqual(CurrencyBalance.objects.get(user=self.users[0]).balance, 200)
        self.assert_conserved()


    def test_concurrent_expiry_refunds_only_once(self):
        packet = self.send(expires_at=(timezone.now() + timedelta(hours=1)).isoformat())
        RedPacket.objects.filter(pk=packet['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        results = self.race([lambda: expire_packet(packet['id'])] * 2)
        self.assertEqual(sorted(results), [0, 20])
        self.assertEqual(CurrencyTransaction.objects.filter(transaction_type='red_packet_refund').count(), 1)
        self.assert_conserved()

    def test_expired_claim_racing_with_refund_cannot_credit_recipient(self):
        packet = self.send(expires_at=(timezone.now() + timedelta(hours=1)).isoformat())
        RedPacket.objects.filter(pk=packet['id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        results = self.race([lambda: expire_packet(packet['id']),
                             lambda: claim_packet(self.members[1], packet['id'])])
        self.assertIn(409, results)
        self.assertFalse(RedPacketClaim.objects.exists())
        self.assertEqual(CurrencyTransaction.objects.filter(transaction_type='red_packet_refund').count(), 1)
        self.assert_conserved()
