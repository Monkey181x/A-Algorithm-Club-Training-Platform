from django.db.models import Max

from .models import UserProfile


def qualification_admission_years():
    # Registered accounts determine the window even before joining a team.
    latest = UserProfile.objects.filter(
        user__is_superuser=False, admission_year__gte=2013
    ).aggregate(latest=Max("admission_year"))["latest"]
    return range(max(2013, latest - 1), latest + 1) if latest else ()


EVENT_FLAGS = {
    "submission_report": "report_enabled",
    "item_used": "item_use_enabled",
    "physical_purchase": "physical_purchase_enabled",
    "weekly_unqualified": "qualification_enabled",
}
