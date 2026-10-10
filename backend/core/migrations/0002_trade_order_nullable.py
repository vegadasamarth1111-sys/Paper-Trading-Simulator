"""Phase 2: settlement/adjustment trades carry no order (expiry cash settlement)."""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trade",
            name="order",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Originating order (null for order-less settlement fills such as "
                    "ITM option expiry cash settlement, and for adjusting entries)"
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="trades",
                to="core.order",
            ),
        ),
    ]
