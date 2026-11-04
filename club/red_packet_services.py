"""Transactional red packet escrow; each send and claim writes one wallet entry."""
import secrets
import uuid

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .currency_services import add_currency, deduct_currency
from .models import CurrencyBalance, CurrencyTransaction, RedPacket, RedPacketClaim, TeamMembership


class PacketError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def locked_member(member):
    user = User.objects.select_for_update().get(pk=member.user_id)
    current = TeamMembership.objects.select_for_update(of=('self',)).select_related('team').filter(
        pk=member.pk, user=user, is_active=True).first()
    if not user.is_active or hasattr(user, 'account_closure') or not current:
        raise PacketError('当前团队成员权限已失效', 403)
    return current


def validate_send(data):
    amount, count = data.get('amount'), data.get('count')
    if type(amount) is not int or not 1 <= amount <= 2147483647:
        raise PacketError('红包总金额必须为 1–2147483647 的整数')
    if type(count) is not int or count < 1:
        raise PacketError('红包个数必须为正整数')
    if amount < count:
        raise PacketError('每份红包至少需要 1 RBC Coin')
    kind = data.get('kind', 'random')
    if kind not in ('random', 'equal'):
        raise PacketError('请选择拼手气红包或普通红包')
    if kind == 'equal' and amount % count:
        raise PacketError('普通红包的总金额必须能被红包个数整除')
    message = data.get('message', '')
    if not isinstance(message, str) or len(message.strip()) > 100:
        raise PacketError('祝福语不能超过 100 字')
    try:
        request_id = uuid.UUID(str(data.get('request_id', '')))
    except (ValueError, TypeError, AttributeError):
        raise PacketError('请提供有效的请求编号')
    expires_at = data.get('expires_at')
    if expires_at is not None:
        try:
            if not isinstance(expires_at, str) or len(expires_at) > 64:
                raise ValueError()
            expires_at = parse_datetime(expires_at)
            if expires_at is None or timezone.is_naive(expires_at):
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise PacketError('请选择有效的过期时间，或选择无限期')
    return amount, count, kind, message.strip() or '恭喜发财，大吉大利！', request_id, expires_at


@transaction.atomic
def send_packet(member, data):
    amount, count, kind, message, request_id, expires_at = validate_send(data)
    member = locked_member(member)
    previous = RedPacket.objects.filter(sender_id=member.user_id, request_id=request_id).first()
    if previous:
        if (previous.team_id, previous.total_amount, previous.total_count, previous.kind, previous.message, previous.expires_at) != (
                member.team_id, amount, count, kind, message, expires_at):
            raise PacketError('请求编号已用于其他红包，请刷新后重试', 409)
        return previous
    if not member.team.currency_enabled:
        raise PacketError('当前团队尚未启用 RBC Coins')
    if expires_at is not None and expires_at <= timezone.now():
        raise PacketError('过期时间必须晚于当前时间')
    packet = RedPacket.objects.create(team=member.team, sender_id=member.user_id,
        sender_member=member, request_id=request_id, message=message, kind=kind,
        total_amount=amount, total_count=count, remaining_amount=amount, remaining_count=count, expires_at=expires_at)
    deduct_currency(member, amount, 'red_packet_sent', f'发出红包 #{packet.pk}')
    return packet


def claim_packet(member, packet_id):
    # Commit any due refund separately: rejecting an expired claim must not undo it.
    expire_packet(packet_id, team_id=member.team_id)
    return _claim_packet(member, packet_id)


@transaction.atomic
def _claim_packet(member, packet_id):
    member = locked_member(member)
    packet = RedPacket.objects.select_for_update().filter(pk=packet_id, team_id=member.team_id).first()
    if not packet:
        raise PacketError('红包不存在或不属于当前团队', 404)
    previous = RedPacketClaim.objects.filter(packet=packet, user_id=member.user_id).first()
    if previous:
        return previous
    if not member.team.currency_enabled:
        raise PacketError('当前团队尚未启用 RBC Coins')
    if packet.refunded_at or (packet.remaining_count and packet.expires_at is not None and packet.expires_at <= timezone.now()):
        raise PacketError('红包已过期，不能继续领取', 409)
    if not packet.remaining_count:
        raise PacketError('红包已领完', 409)
    if packet.remaining_count == 1:
        amount = packet.remaining_amount
    elif packet.kind == 'equal':
        amount = packet.total_amount // packet.total_count
    else:
        # Double-mean allocation, reserving at least one Coin for each later claim.
        upper = min(packet.remaining_amount - packet.remaining_count + 1,
                    2 * packet.remaining_amount // packet.remaining_count)
        amount = secrets.randbelow(upper) + 1
    claim = RedPacketClaim.objects.create(packet=packet, user_id=member.user_id, member=member, amount=amount)
    add_currency(member, amount, 'red_packet_received', f'领取红包 #{packet.pk}')
    packet.remaining_amount -= amount
    packet.remaining_count -= 1
    packet.save(update_fields=['remaining_amount', 'remaining_count'])
    return claim


@transaction.atomic
def expire_packet(packet_id, *, team_id=None):
    """Refund escrow once, locking sender -> packet -> global wallet."""
    due = RedPacket.objects.filter(pk=packet_id, expires_at__lte=timezone.now(),
        remaining_amount__gt=0, refunded_at__isnull=True)
    if team_id is not None:
        due = due.filter(team_id=team_id)
    sender_id = due.values_list('sender_id', flat=True).first()
    if sender_id is None:
        return 0
    if not User.objects.select_for_update().filter(pk=sender_id).first():
        return 0
    packet = RedPacket.objects.select_for_update().filter(pk=packet_id).first()
    now = timezone.now()
    if (not packet or packet.refunded_at or not packet.remaining_amount
            or packet.expires_at is None or packet.expires_at > now):
        return 0
    wallet, _ = CurrencyBalance.objects.get_or_create(user_id=sender_id,
        defaults={'team_id': packet.team_id, 'member_id': packet.sender_member_id})
    wallet = CurrencyBalance.objects.select_for_update().get(pk=wallet.pk)
    amount = packet.remaining_amount
    if wallet.balance + amount > 2147483647:
        # Keep escrow intact and retry after the sender frees wallet capacity.
        return 0
    wallet.balance += amount
    wallet.save(update_fields=['balance', 'updated_at'])
    CurrencyTransaction.objects.create(user_id=sender_id, team_id=packet.team_id,
        member_id=packet.sender_member_id, amount=amount, balance_after=wallet.balance,
        transaction_type='red_packet_refund', description=f'红包 #{packet.pk} 过期，未领取金额退回')
    packet.refunded_amount = amount
    packet.refunded_at = now
    packet.remaining_amount = 0
    packet.save(update_fields=['refunded_amount', 'refunded_at', 'remaining_amount'])
    return amount


def settle_expired_packets(*, team_id=None, limit=100):
    overflow = CurrencyBalance.objects.filter(user_id=OuterRef('sender_id'),
        balance__gt=2147483647 - OuterRef('remaining_amount'))
    due = RedPacket.objects.filter(expires_at__lte=timezone.now(),
        remaining_amount__gt=0, refunded_at__isnull=True, sender__isnull=False).alias(
        wallet_full=Exists(overflow)).filter(wallet_full=False).order_by('expires_at', 'pk')
    if team_id is not None:
        due = due.filter(team_id=team_id)
    return sum(bool(expire_packet(pk, team_id=team_id)) for pk in list(due.values_list('pk', flat=True)[:limit]))
