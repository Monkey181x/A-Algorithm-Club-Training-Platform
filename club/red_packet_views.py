"""Team-scoped red packet square, details, sends and idempotent claims."""
from django.db.models import Case, IntegerField, Max, OuterRef, Q, Subquery, Value, When
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from .models import CurrencyBalance, RedPacket, RedPacketClaim
from .red_packet_services import PacketError, claim_packet, expire_packet, send_packet, settle_expired_packets
from .views import api_error, api_login_required, body, membership_for
from .avatar_images import url_for


def context(request):
    member = membership_for(request)
    if not member:
        raise PacketError('请先加入团队', 403)
    return member


def wallet(user_id):
    return CurrencyBalance.objects.filter(user_id=user_id).values_list('balance', flat=True).first() or 0


def display_name(user, member):
    if not user or not user.is_active or hasattr(user, 'account_closure'):
        return '已注销用户'
    return member.display_name if member else '原团队成员'


def packet_data(packet, user_id, *, detail=False):
    my_amount = getattr(packet, 'my_amount', None)
    if not hasattr(packet, 'my_amount'):
        my_amount = packet.claims.filter(user_id=user_id).values_list('amount', flat=True).first()
    sender = packet.sender
    member = packet.sender_member
    avatar = None
    if sender and sender.is_active and member and member.is_active and not hasattr(sender, 'account_closure'):
        avatar = url_for(getattr(sender, 'personal_profile', None), member.pk, 'small')
    result = {'id': packet.pk, 'sender': display_name(sender, member), 'sender_avatar': avatar,
        'is_mine': packet.sender_id == user_id, 'message': packet.message, 'kind': packet.kind,
        'is_claimed': my_amount is not None, 'is_exhausted': packet.remaining_count == 0,
        'created_at': packet.created_at.isoformat(), 'my_amount': my_amount}
    expired = bool(packet.refunded_at or (packet.remaining_count and packet.expires_at and packet.expires_at <= timezone.now()))
    result.update(expires_at=packet.expires_at.isoformat() if packet.expires_at else None,
        is_expired=expired, is_refunded=packet.refunded_at is not None,
        refund_pending=expired and packet.remaining_amount > 0)
    if my_amount is not None:
        result.update(total_amount=packet.total_amount, total_count=packet.total_count,
            remaining_amount=packet.remaining_amount, remaining_count=packet.remaining_count,
            refunded_amount=packet.refunded_amount)
    if detail and packet.kind == 'random' and packet.remaining_count == 0:
        best = packet.claims.aggregate(amount=Max('amount'))['amount']
        result['best_luck'] = [{'name': display_name(c.user, c.member), 'is_me': c.user_id == user_id}
            for c in packet.claims.filter(amount=best).select_related('user__account_closure', 'member')]
    if detail and my_amount is not None:
        claims = list(packet.claims.select_related('user__account_closure', 'member'))
        best_amount = max((c.amount for c in claims), default=None) if packet.kind == 'random' and not packet.remaining_count else None
        best_count = sum(c.amount == best_amount for c in claims)
        result['claims'] = [{'name': display_name(c.user, c.member), 'amount': c.amount,
            'created_at': c.created_at.isoformat(), 'is_me': c.user_id == user_id,
            'is_best_luck': c.amount == best_amount, 'is_tied_best': c.amount == best_amount and best_count > 1}
            for c in claims]
    return result


@api_login_required
@never_cache
@require_http_methods(['GET', 'POST'])
def packets(request):
    try:
        member = context(request)
        settle_expired_packets(team_id=member.team_id)
        if request.method == 'POST':
            if not member.pk:
                raise PacketError('请使用已加入团队的成员身份发红包', 403)
            packet = send_packet(member, body(request))
            return JsonResponse({'packet': packet_data(packet, request.user.pk), 'balance': wallet(request.user.pk)})
        try:
            page = int(request.GET.get('page', '1'))
            if not 1 <= page <= 2147483647:
                raise ValueError()
        except (TypeError, ValueError):
            raise PacketError('页码必须为有效正整数')
        own_claims = RedPacketClaim.objects.filter(packet_id=OuterRef('pk'), user_id=request.user.pk)
        queryset = RedPacket.objects.filter(team=member.team).select_related(
            'sender__account_closure', 'sender__personal_profile', 'sender_member').annotate(
            my_amount=Subquery(own_claims.values('amount')[:1]),
            availability=Case(When(Q(remaining_count__gt=0) & (Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())),
                then=Value(0)), default=Value(1), output_field=IntegerField())
        ).order_by('availability', '-created_at', '-id')
        total = queryset.count()
        page_size = 24
        return JsonResponse({'packets': [packet_data(p, request.user.pk)
            for p in queryset[(page - 1) * page_size:page * page_size]],
            'page': page, 'pages': max(1, (total + page_size - 1) // page_size), 'total': total,
            'enabled': member.team.currency_enabled, 'can_send': bool(member.pk and member.team.currency_enabled),
            'balance': wallet(request.user.pk)})
    except ValueError as error:
        return api_error(str(error), getattr(error, 'status', 400))


@api_login_required
@never_cache
@require_http_methods(['GET'])
def detail(request, packet_id):
    try:
        member = context(request)
        expire_packet(packet_id, team_id=member.team_id)
        packet = RedPacket.objects.select_related('sender__account_closure', 'sender__personal_profile', 'sender_member').filter(
            pk=packet_id, team=member.team).first()
        if not packet:
            raise PacketError('红包不存在或不属于当前团队', 404)
        return JsonResponse({'packet': packet_data(packet, request.user.pk, detail=True),
            'can_claim': bool(member.pk and member.team.currency_enabled)})
    except ValueError as error:
        return api_error(str(error), getattr(error, 'status', 400))


@api_login_required
@never_cache
@require_http_methods(['POST'])
def claim(request, packet_id):
    try:
        member = context(request)
        if not member.pk:
            raise PacketError('请使用已加入团队的成员身份领取红包', 403)
        receipt = claim_packet(member, packet_id)
        return JsonResponse({'amount': receipt.amount, 'balance': wallet(request.user.pk)})
    except ValueError as error:
        return api_error(str(error), getattr(error, 'status', 400))
