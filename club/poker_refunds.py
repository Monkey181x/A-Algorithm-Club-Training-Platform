"""Settle the immutable entitlements captured during the free-poker upgrade."""
import logging

from django.db import transaction
from django.utils import timezone

from .models import CurrencyBalance, CurrencyTransaction, LegacyPokerRefund, PokerChipBalance, PokerRoom, PokerSeat


logger = logging.getLogger(__name__)


@transaction.atomic
def settle_refund(refund_id):
    refund = LegacyPokerRefund.objects.select_for_update().filter(pk=refund_id, settled_at__isnull=True).first()
    if refund is None:
        return
    if refund.coins:
        wallet, _ = CurrencyBalance.objects.select_for_update().get_or_create(user_id=refund.user_id)
        if wallet.balance + refund.coins > 2147483647:
            raise OverflowError(f'Coin balance limit for poker refund {refund.pk}')
        wallet.balance += refund.coins
        wallet.save(update_fields=['balance', 'updated_at'])
        CurrencyTransaction.objects.create(
            user_id=refund.user_id, team_id=wallet.team_id, member_id=wallet.member_id,
            amount=refund.coins, balance_after=wallet.balance, transaction_type='poker_cashout',
            description=f'旧版德州扑克筹码退还（{refund.coins * 1000} 筹码）',
        )
    PokerSeat.objects.filter(user_id=refund.user_id, room__mode='coins').delete()
    PokerChipBalance.objects.filter(user_id=refund.user_id).delete()
    refund.settled_at = timezone.now()
    refund.save(update_fields=['settled_at'])


def settle_pending_refunds():
    pending = LegacyPokerRefund.objects.filter(settled_at__isnull=True).order_by('pk')
    for refund_id in pending.values_list('pk', flat=True).iterator():
        try:
            settle_refund(refund_id)
        except OverflowError:
            # Leave the entitlement and seat intact; other accounts can settle.
            logger.exception('Legacy poker refund %s is still pending', refund_id)
    PokerRoom.objects.filter(mode='coins').exclude(seats__user__isnull=False).delete()
