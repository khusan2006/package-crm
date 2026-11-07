import uuid

from django.db import migrations, models
from django.db.models.functions import Coalesce

# Slices of one sum are written in one transaction, milliseconds apart; on the data this
# was written against every such gap was under 0.05 s and the next one was over 2 s, when
# someone had entered a second payment by hand.
MAX_GAP_SECONDS = 1


def backfill(apps, schema_editor):
    """Tie together the slices of the lump payments taken before `batch` existed. An
    advance being spent never joins the money that came in: it is not new money."""
    Payment = apps.get_model("crm", "Payment")
    rows = (
        Payment.objects.filter(kind__in=("debt", "advance_in", "advance_used"), is_opening=False)
        .annotate(client_pk=Coalesce("sale__client", "client"))
        .order_by("created_at", "pk")
    )
    last_of = {}
    groups = []
    for p in rows.iterator():
        key = (
            p.client_pk, p.kind == "advance_used", p.date, p.method, p.currency,
            p.created_by_id, p.note, p.commission_percent, p.commission_payer,
        )
        group = last_of.get(key)
        if group and (p.created_at - group[-1].created_at).total_seconds() <= MAX_GAP_SECONDS:
            group.append(p)
        else:
            group = [p]
            groups.append(group)
            last_of[key] = group
    for group in groups:
        if len(group) > 1:
            Payment.objects.filter(pk__in=[p.pk for p in group]).update(batch=uuid.uuid4())


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0049_expense_wage_month'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='batch',
            field=models.UUIDField(blank=True, db_index=True, editable=False, null=True, verbose_name="To'lov guruhi"),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
