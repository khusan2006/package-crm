"""Seed the client's real catalogue: 7 products sold by the kg, with the tannarx
(cost) and sotuv narxi (sale price) the client supplied on 2026-07-24.

These are the names the client actually sells under and records in their own
HISOBOT sheet (ОҚ, ҚОРА, НОВВОТ, …) — the micron/size 56-SKU breakdown
(`seed_paket_products`) is a separate reference axis and is not used for selling.

Idempotent — matches on SKU, so re-running is safe and updates the prices in place.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from crm.models import Product

# (name, sku, cost_price, price, has_size, has_micron) — so'm per 1 kg.
# The 5 film products come in razmer (1,5м/2м/6м) and mikron (015…02); the two
# ҚОП (bag) products have neither.
PRODUCTS = [
    ("ОҚ", "oq", 23000, 24000, True, True),
    ("ҚОРА", "qora", 14000, 16000, True, True),
    ("НОВВОТ ҚОП", "novvot-qop", 22000, 24000, False, False),
    ("ОҚ ҚОП", "oq-qop", 26000, 28000, False, False),
    ("НОВВОТ", "novvot", 18000, 19000, True, True),
    ("ОҚ ЯЛТИРОҚ", "oq-yaltiroq", 25000, 30000, True, True),
    ("КЎК", "kok", 18000, 19000, True, True),
]


class Command(BaseCommand):
    help = "Mijozning 7 ta haqiqiy mahsulotini narxi bilan qo'shadi/yangilaydi (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        created = updated = 0
        for name, sku, cost, price, has_size, has_micron in PRODUCTS:
            cost = Decimal(cost)
            price = Decimal(price)
            obj, was_created = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    "name": name,
                    "cost_price": cost,
                    "price": price,
                    "has_size": has_size,
                    "has_micron": has_micron,
                    "is_active": True,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(f"  + {sku:<12} {name:<14} tannarx={cost:,.0f}  narx={price:,.0f}")
            else:
                changed = []
                if obj.name != name:
                    obj.name = name
                    changed.append("name")
                if obj.cost_price != cost:
                    obj.cost_price = cost
                    changed.append("cost_price")
                if obj.price != price:
                    obj.price = price
                    changed.append("price")
                if obj.has_size != has_size:
                    obj.has_size = has_size
                    changed.append("has_size")
                if obj.has_micron != has_micron:
                    obj.has_micron = has_micron
                    changed.append("has_micron")
                if not obj.is_active:
                    obj.is_active = True
                    changed.append("is_active")
                if changed:
                    obj.save(update_fields=changed)
                    updated += 1
                    self.stdout.write(f"  ~ {sku:<12} {name:<14} ({', '.join(changed)})")
        self.stdout.write(self.style.SUCCESS(
            f"Tayyor: {created} ta qo'shildi, {updated} ta yangilandi "
            f"(jami {len(PRODUCTS)} ta mahsulot)."
        ))
