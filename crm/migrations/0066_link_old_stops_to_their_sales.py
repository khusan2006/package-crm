from datetime import timedelta

from django.db import migrations
from django.utils import timezone

#: How long after its stop a sale typed in Sotuvlar still counts as that stop's: the
#: orders were written the day before and went (and were sold) the next day.
LINK_DAYS = 2


def put_stops_on_their_sales_day(apps, schema_editor):
    """Before this release a Yuk carried the date its orders were WRITTEN — the day
    before — while the goods went, and were sold, the next day. The board's day is
    now the delivery day, so each old stop is put where its sale is:

    - a stop sold with ✓ moves to its sale's date;
    - a stop never ✓-ed takes the sale typed for it in Sotuvlar that day or up to
      LINK_DAYS later (the first one no stop holds yet), as a sale typed now would
      (views._track_sale_delivery): the sale becomes the stop's, its unsold planned
      products give way to what was sold, and it moves to the sale's date.

    A moved stop is marked as carried («09.11 dan»), so its first day shows it went
    on. Sold by today, it is delivered. One with no sale at all stays where 0062 left
    it («Bekor qilindi» on a past day)."""
    Load = apps.get_model("crm", "Load")
    LoadItem = apps.get_model("crm", "LoadItem")
    LoadLine = apps.get_model("crm", "LoadLine")
    Sale = apps.get_model("crm", "Sale")
    SaleItem = apps.get_model("crm", "SaleItem")
    today = timezone.localdate()

    def move(stop, day):
        if stop.load.date == day:
            return
        load = (Load.objects.filter(date=day).exclude(items__client_id=stop.client_id)
                .order_by("created_at").first())
        if load is None:
            busy = Load.objects.filter(date=day).exists()
            load = Load.objects.create(date=day, note="Kechagidan" if busy else "")
        stop.carried_from = stop.carried_from or stop.load.date
        stop.load = load
        stop.position = stop.route_group = None

    def settle(stop, sale_date):
        if sale_date <= today:
            stop.status = "done"  # sold: before this release ✓ / a sale meant it went
        elif stop.status == "cancel":
            stop.status = "new"

    fields = ["sale", "purpose", "status", "load", "carried_from", "position", "route_group"]
    # Sold with ✓: onto the sale's day.
    for stop in LoadItem.objects.filter(sale__isnull=False).select_related("load", "sale"):
        if stop.sale.date != stop.load.date:
            move(stop, stop.sale.date)
            settle(stop, stop.sale.date)
            stop.save(update_fields=fields)

    taken = set(LoadItem.objects.filter(sale__isnull=False).values_list("sale_id", flat=True))
    taken |= set(LoadLine.objects.filter(sale__isnull=False).values_list("sale_id", flat=True))
    stops = (LoadItem.objects.filter(sale__isnull=True, via_sotuvlar=False)
             .exclude(lines__sale__isnull=False)
             .select_related("load").order_by("load__date", "created_at"))
    for stop in stops:
        day = stop.load.date
        sale = (Sale.objects.filter(client_id=stop.client_id, is_opening=False,
                                    date__gte=day, date__lte=day + timedelta(days=LINK_DAYS))
                .exclude(pk__in=taken).order_by("date", "created_at", "pk").first())
        if sale is None:
            continue
        taken.add(sale.pk)
        LoadLine.objects.filter(item=stop, sale__isnull=True).delete()
        for line in SaleItem.objects.filter(sale=sale).order_by("pk"):
            kg = line.weight / 1000 if line.dimension == "g" else line.weight
            LoadLine.objects.create(item=stop, product_id=line.product_id, rolls=line.rolls or 0,
                                    price=line.price, kg=kg, size=line.size, micron=line.micron,
                                    sale=sale)
        stop.sale = sale
        if stop.purpose == "collect":
            stop.purpose = "both"
        move(stop, sale.date)
        settle(stop, sale.date)
        stop.save(update_fields=fields)


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0065_load_line_sale'),
    ]

    operations = [
        migrations.RunPython(put_stops_on_their_sales_day, migrations.RunPython.noop),
    ]
