from collections import defaultdict

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def snapshot_refunds(apps, schema_editor):
    connection = schema_editor.connection
    db = connection.alias
    chips = apps.get_model('club', 'PokerChipBalance')
    if chips._meta.db_table not in connection.introspection.table_names():
        # Sites that ran the original 0074 have already settled every account.
        schema_editor.create_model(chips)
        return

    totals = defaultdict(int, chips.objects.using(db).values_list('user_id', 'balance'))
    seats = apps.get_model('club', 'PokerSeat').objects.using(db).filter(
        room__mode='coins', user_id__isnull=False,
    ).values_list('pk', 'user_id', 'stack', 'room__hand')
    for seat_id, user_id, stack, hand in seats.iterator():
        totals[user_id] += stack
        player = hand.get('players', {}).get(str(user_id))
        if hand.get('street') != 'finished' and player and player['seat_id'] == seat_id:
            totals[user_id] += player['total']

    refund = apps.get_model('club', 'LegacyPokerRefund')
    refund.objects.using(db).bulk_create(
        [refund(user_id=user_id, coins=total // 1000) for user_id, total in totals.items()],
        batch_size=1000,
    )


class Migration(migrations.Migration):
    dependencies = [('club', '0083_structure_training_date')]
    operations = [
        migrations.CreateModel(
            name='LegacyPokerRefund',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('coins', models.PositiveBigIntegerField()),
                ('settled_at', models.DateTimeField(db_index=True, null=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.RunPython(snapshot_refunds, migrations.RunPython.noop),
    ]
