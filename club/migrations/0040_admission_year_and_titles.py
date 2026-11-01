from datetime import date
from django.db import migrations, models
from django.utils import timezone


def backfill(apps,schema_editor):
    alias = schema_editor.connection.alias
    Profile = apps.get_model('club','UserProfile')
    Membership = apps.get_model('club','TeamMembership')
    today = timezone.localdate()
    current = today.year if today >= date(today.year,8,1) else today.year-1
    for profile in Profile.objects.using(alias).filter(admission_year__isnull=True,grade__isnull=False,user__is_superuser=False).iterator():
        level = profile.grade
        if level != 4 and profile.grade_date:
            start,end = sorted([profile.grade_date,today])
            changes = sum(start < date(y,m,d) <= end for y in range(start.year,end.year+1) for m,d in [(2,15),(8,1)])
            level = min(4,max(0,level+(changes if profile.grade_date<=today else -changes)))
        Profile.objects.using(alias).filter(pk=profile.pk).update(admission_year=max(2013,current-level//2))
    for member in Membership.objects.using(alias).iterator():
        if isinstance(member.member_title,dict):
            Membership.objects.using(alias).filter(pk=member.pk).update(member_title=[member.member_title] if member.member_title else [])


class Migration(migrations.Migration):
    dependencies = [('club','0039_membership_title')]
    operations = [
        migrations.AddField(model_name='userprofile',name='admission_year',field=models.PositiveSmallIntegerField(null=True,blank=True)),
        migrations.AlterField(model_name='teammembership',name='member_title',field=models.JSONField(default=list,blank=True)),
        migrations.RunPython(backfill,migrations.RunPython.noop),
    ]
