from bisect import bisect_right
from collections import defaultdict
from datetime import timedelta

from django.db import migrations
from django.db.models import F

# Frozen copy of crm.models.PAYING_KINDS — a migration must not import the live model.
PAYING_KINDS = ("sale", "debt", "advance_used")


def recompute_deadlines(apps, schema_editor):
    """Bring every receipt's deadline in line with `recompute_client_debt_deadlines`.

    0040 only stored the term. Receipts whose client had already repaid before the
    "counter restarts on repayment" rule shipped kept their old sale-date deadline, so
    a debt paid into on 06.09 still read as late since June. The rule is replayed here
    on the historical models: the agreed term from the sale date, or the client's last
    repayment after the receipt was written if that is later. A repayment is money on
    any of the client's receipts dated after that receipt's own day.
    """
    Sale = apps.get_model("crm", "Sale")
    Payment = apps.get_model("crm", "Payment")
    repayments = defaultdict(set)
    for client_id, day in Payment.objects.filter(
        sale__isnull=False, kind__in=PAYING_KINDS, date__gt=F("sale__date")
    ).values_list("sale__client_id", "date"):
        repayments[client_id].add(day)
    repayments = {client_id: sorted(days) for client_id, days in repayments.items()}

    for pk, client_id, sale_date, deadline, term in Sale.objects.values_list(
        "pk", "client_id", "date", "debt_deadline", "debt_term_days"
    ):
        if term is None:
            if deadline is None:
                continue  # nothing was ever agreed; the first recompute will pin it
            term = max((deadline - sale_date).days, 0)
        agreed = sale_date + timedelta(days=term)
        days = repayments.get(client_id, [])
        repaid_on = days[-1] if bisect_right(days, sale_date) < len(days) else None
        new = max(agreed, repaid_on) if repaid_on else agreed
        Sale.objects.filter(pk=pk).exclude(debt_deadline=new, debt_term_days=term).update(
            debt_deadline=new, debt_term_days=term
        )


class Migration(migrations.Migration):

    dependencies = [
        ("crm", "0040_sale_debt_term_days"),
    ]

    operations = [
        migrations.RunPython(recompute_deadlines, migrations.RunPython.noop),
    ]
