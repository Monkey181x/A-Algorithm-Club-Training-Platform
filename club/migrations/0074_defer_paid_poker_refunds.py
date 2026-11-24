from django.db import migrations, models


class Migration(migrations.Migration):
    replaces = [('club', '0074_remove_paid_poker')]
    dependencies = [('club', '0073_user_qualification_reminder')]
    # Preserve the old rooms, seats and chip wallets through the host's audit.
    operations = [
        migrations.AlterField(
            model_name='pokerroom',
            name='mode',
            field=models.CharField(
                max_length=10, default='friendly',
                choices=[('friendly', '多人联机'), ('practice', '赠币人机练习')],
            ),
        ),
    ]
