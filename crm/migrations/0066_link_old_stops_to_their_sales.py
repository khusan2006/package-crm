from django.db import migrations
from django.utils import timezone


def link_stops_to_their_sales(apps, schema_editor):
    """Before Sotuvlar sales went onto the Yetkazish board, a client's stop in a Yuk
    and the sale typed for it in Sotuvlar the same day stayed apart: the day's page
    showed only the plan — a past one, since 0062, as «Bekor qilindi» — and the sale
    was in Sotuvlar alone. Each such stop now takes its sale, as a sale typed today
    does (views._track_sale_delivery): the sale becomes the stop's, the stop's
    unsold planned products give way to what was sold, and a past day's stop is
    delivered. Only sales no stop holds yet; each goes to one stop."""
    LoadItem = apps.get_model("crm", "LoadItem")
    LoadLine = apps.get_model("crm", "LoadLine")
    Sale = apps.get_model("crm", "Sale")
    SaleItem = apps.get_model("crm", "SaleItem")
    today = timezone.localdate()
    taken = set(LoadItem.objects.filter(sale__isnull=False).values_list("sale_id", flat=True))
    taken |= set(LoadLine.objects.filter(sale__isnull=False).values_list("sale_id", flat=True))
    stops = (LoadItem.objects.filter(sale__isnull=True, via_sotuvlar=False)
             .exclude(lines__sale__isnull=False)
             .select_related("load").order_by("load__date", "created_at"))
    for stop in stops:
        sale = (Sale.objects.filter(client_id=stop.client_id, date=stop.load.date, is_opening=False)
                .exclude(pk__in=taken).order_by("created_at", "pk").first())
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
        if stop.load.date < today:
            stop.status = "done"  # a past day's sale: it went
        stop.save(update_fields=["sale", "purpose", "status"])


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0065_load_line_sale'),
    ]

    operations = [
        migrations.RunPython(link_stops_to_their_sales, migrations.RunPython.noop),
    ]
