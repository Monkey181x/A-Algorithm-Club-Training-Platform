from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from types import SimpleNamespace

from club.currency_services import reconcile_submission_day
from club.models import CheckinRecord, MakeupPermission, Semester, Submission, TeamMembership


class Command(BaseCommand):
    help = 'Preview or repair orphan sign-ins and completed make-up permissions in current semesters.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--team-id', type=int)

    def handle(self, *args, **options):
        today = timezone.localdate()
        semesters = Semester.objects.filter(is_active=True, archived_at__isnull=True,
            starts_on__lte=today, ends_on__gte=today).exclude(team__code__startswith='TST-')
        if options['team_id']:
            semesters = semesters.filter(team_id=options['team_id'])
        candidates = []
        for semester in semesters:
            valid = set(Submission.objects.filter(team=semester.team, is_valid=True,
                record_date__range=(semester.starts_on, today)).values_list('member_id', 'record_date'))
            dates = set(CheckinRecord.objects.filter(team=semester.team,
                checkin_date__range=(semester.starts_on, today)).values_list('member_id', 'checkin_date'))
            dates.update(MakeupPermission.objects.filter(semester=semester, completed_at__isnull=False,
                record_date__lte=today).values_list('member_id', 'record_date'))
            candidates.extend((semester.pk, member_id, day) for member_id, day in sorted(dates-valid))
        self.stdout.write(f'orphan_dates={len(candidates)} apply={options["apply"]}')
        from club.reward_recovery import build_plan, public_plan, lock_recovery_state
        for semester_id, member_id, day in candidates:
            member = TeamMembership.objects.select_related('team').get(pk=member_id)
            plan = public_plan(build_plan(SimpleNamespace(member=member,record_date=day,pk=None,version=0)))
            self.stdout.write(f'team={member.team_id} member={member.pk} date={day} impact={plan}')
        if options['apply']:
            for semester_id, member_id, day in candidates:
                with transaction.atomic():
                    member = TeamMembership.objects.select_related('team').get(pk=member_id)
                    User.objects.select_for_update().get(pk=member.user_id)
                    semester = Semester.objects.select_for_update().get(pk=semester_id)
                    lock_recovery_state(member)
                    plan = build_plan(SimpleNamespace(member=member,record_date=day,pk=None,version=0))
                    reconcile_submission_day(member, day, semester, recoveries=plan['recoveries'])
