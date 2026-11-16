from collections import defaultdict

from django.db import migrations, models


def refund_and_delete_paid_poker(apps, schema_editor):
    db = schema_editor.connection.alias
    rooms = apps.get_model('club', 'PokerRoom').objects.using(db).filter(mode='coins')
    seats = apps.get_model('club', 'PokerSeat').objects.using(db)
    chips = apps.get_model('club', 'PokerChipBalance').objects.using(db)
    wallets = apps.get_model('club', 'CurrencyBalance').objects.using(db)
    ledger = apps.get_model('club', 'CurrencyTransaction').objects.using(db)

    totals = defaultdict(int, chips.values_list('user_id', 'balance').iterator())
    for room in rooms.iterator():
        hand = room.hand
        players = hand.get('players', {}) if hand.get('street') != 'finished' else {}
        for seat in seats.filter(room_id=room.pk, user_id__isnull=False).iterator():
            totals[seat.user_id] += seat.stack
            # Cancel unfinished hands and return each player's committed bets.
            player = players.get(str(seat.user_id))
            if player and player['seat_id'] == seat.pk:
                totals[seat.user_id] += player['total']

    for user_id, total in sorted(totals.items()):
        coins = total // 1000
        if not coins:
            continue
        wallet, _ = wallets.select_for_update().get_or_create(user_id=user_id)
        wallet.balance += coins
        if wallet.balance > 2147483647:
            raise ValueError(f'Cannot refund poker chips: Coin balance limit for user {user_id}')
        wallet.save(using=db, update_fields=['balance', 'updated_at'])
        ledger.create(
            user_id=user_id, team_id=wallet.team_id, member_id=wallet.member_id,
            amount=coins, balance_after=wallet.balance, transaction_type='poker_cashout',
            description=f'旧版德州扑克筹码退还（{coins * 1000} 筹码）',
        )

    rooms.delete()


class Migration(migrations.Migration):
    dependencies = [('club', '0073_user_qualification_reminder')]
    operations = [
        # Schema rollback never recreates removed chips or reverses posted refunds.
        migrations.RunPython(refund_and_delete_paid_poker, migrations.RunPython.noop),
        migrations.DeleteModel(name='PokerChipBalance'),
        migrations.AlterField(
            model_name='pokerroom',
            name='mode',
            field=models.CharField(
                max_length=10,
                choices=[('friendly', '多人联机'), ('practice', '赠币人机练习')],
                default='friendly',
            ),
        ),
    ]
