"""
Paper Trading Simulator — Live-paper trial records (Phase 8)
===============================================================
One row per trading day of the trial, written by the nightly pipeline.
The acceptance report reads ONLY these rows (plus rebuilt ledger state),
so the verdict is reproducible from the database alone — never from
somebody's memory of how the trial went.
"""

from decimal import Decimal

from django.db import models


class TrialDay(models.Model):
    """
    Daily trial evidence. Every field is machine-written by trial_daily,
    except operator_notes (crashes, interventions — the honest log of
    anything the automation did not do by itself).
    """

    id = models.BigAutoField(primary_key=True)
    trial_date = models.DateField(unique=True, db_index=True)

    # Data intake
    ticks_total = models.PositiveIntegerField(default=0)
    bad_ticks = models.PositiveIntegerField(default=0)
    data_suspect = models.BooleanField(
        default=False,
        help_text="From the data-quality report (suspect days excluded from stats)",
    )

    # Trading activity
    orders_placed = models.PositiveIntegerField(default=0)
    fills = models.PositiveIntegerField(default=0)
    realized_pnl = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0.0000"))
    total_charges = models.DecimalField(max_digits=18, decimal_places=4, default=Decimal("0.0000"))

    # Money integrity
    reconcile_ok = models.BooleanField(
        default=False,
        help_text="Nightly rebuild matched on every active account",
    )
    reconcile_mismatches = models.PositiveIntegerField(default=0)

    # Robustness
    crash_count = models.PositiveIntegerField(
        default=0,
        help_text="Operator-logged unhandled crashes during market hours",
    )
    restarts = models.PositiveIntegerField(
        default=0,
        help_text="Forced restarts verified recovered (see operator_notes)",
    )

    # Explainability (all rejection classes carry reasons by construction;
    # these counters prove the property held on the day, not just in tests)
    unexplained_rejects = models.PositiveIntegerField(
        default=0,
        help_text="Ticks/orders/risk events WITHOUT a reason (must stay 0)",
    )

    # Paper-vs-replay divergence for the day (percent, None = not computed)
    divergence_pct = models.DecimalField(
        max_digits=8,
        decimal_places=4,
        null=True,
        blank=True,
        help_text="|paper - replay| / |paper| on realized PnL; None when n/a",
    )

    operator_notes = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "trial_days"
        ordering = ["trial_date"]

    def __str__(self):
        verdict = (
            "SUSPECT-DATA" if self.data_suspect else ("OK" if self.reconcile_ok else "UNCHECKED")
        )
        return f"Trial {self.trial_date} [{verdict}] PnL={self.realized_pnl}"
