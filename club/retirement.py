"""Durable retirement subsidy: once per account in each eligible team."""
from django.contrib.auth.models import User
from django.db import transaction

from .grades import grade_on, is_retired
from .models import CurrencyTransaction, RetirementSubsidy, TeamMembership, UserProfile


@transaction.atomic
def ensure_retirement_subsidies(user_id):
    user = User.objects.select_for_update().get(pk=user_id)
    if not user.is_active or user.is_superuser or not is_retired(user_id):
        return 0
    from .currency_services import add_currency
    members = TeamMembership.objects.select_for_update(of=('self',)).select_related('team').filter(
        user=user, is_active=True, team__currency_enabled=True).order_by('pk')
    granted = 0
    for member in members:
        subsidy, created = RetirementSubsidy.objects.get_or_create(user=user, team=member.team,
                                                                  defaults={'member': member})
        if not created:
            continue
        add_currency(member, 10000, CurrencyTransaction.RETIREMENT_SUBSIDY, '退休金补贴')
        subsidy.transaction = CurrencyTransaction.objects.get(member=member, transaction_type=CurrencyTransaction.RETIREMENT_SUBSIDY)
        subsidy.save(update_fields=['transaction'])
        granted += 1
    return granted


def sync_retirement_subsidies():
    granted, failed = 0, []
    profiles = UserProfile.objects.filter(grade__isnull=False, user__is_active=True,
                                         user__is_superuser=False).iterator()
    for profile in profiles:
        if grade_on(profile) != 4:
            continue
        try:
            granted += ensure_retirement_subsidies(profile.user_id)
        except ValueError:
            failed.append(profile.user_id)
    return {'granted': granted, 'failed_users': failed}
