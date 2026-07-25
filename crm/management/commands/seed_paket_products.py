"""Seed the paket catalogue: 7 types (size + colour) × 8 micron thicknesses = 56
products. Idempotent — matches on SKU, so it is safe to re-run and safe to run on
production after it has run locally. Prices default to 0; fill them in afterwards
(the sale form takes the price by hand anyway)."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from crm.models import Product

# (colour name, colour code, size name, size code). Only the size+colour pairs that
# actually exist are listed — оқ/қора come in 1,5м and 2м, новот also in 6м. The name
# is just the colour in CAPS (e.g. "ОҚ"); the size and micron live in the SKU,
# "{size}-{micron}-{colour}" (e.g. "1,5m-01-oq") — the colour is in the SKU too, so it
# stays unique across the three colours that share a size+micron.
TYPES = [
    ("ОҚ", "oq", "1,5м", "1,5m"),
    ("ОҚ", "oq", "2м", "2m"),
    ("ҚОРА", "qora", "1,5м", "1,5m"),
    ("ҚОРА", "qora", "2м", "2m"),
    ("НОВОТ", "novot", "1,5м", "1,5m"),
    ("НОВОТ", "novot", "2м", "2m"),
    ("НОВОТ", "novot", "6м", "6m"),
]

# Micron (thickness) grades, exactly as written in the source list.
MICRONS = ["015", "01", "08", "06", "05", "04", "03", "02"]


class Command(BaseCommand):
    help = "Katalogga 56 ta paket mahsulotini qo'shadi (7 tur × 8 mikron), narxsiz."

    def add_arguments(self, parser):
        parser.add_argument(
            "--price", type=str, default="0",
            help="Barcha mahsulotlarga sotish narxi (1 kg). Default 0.",
        )
        parser.add_argument(
            "--cost", type=str, default="0",
            help="Barcha mahsulotlarga tannarx (1 kg). Default 0.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        price = Decimal(options["price"])
        cost = Decimal(options["cost"])
        created = existing = 0
        for color_name, color_code, size_name, size_code in TYPES:
            for micron in MICRONS:
                name = color_name  # just the colour in CAPS; size+micron live in the SKU
                sku = f"{size_code}-{micron}-{color_code}"
                obj, was_created = Product.objects.get_or_create(
                    sku=sku,
                    defaults={
                        "name": name,
                        "price": price,
                        "cost_price": cost,
                        "is_active": True,
                    },
                )
                if was_created:
                    created += 1
                    self.stdout.write(f"  + {sku}  {name}")
                else:
                    existing += 1
                    # Re-running after a naming change fixes the name in place, but never
                    # touches a price the user may already have filled in.
                    if obj.name != name:
                        obj.name = name
                        obj.save(update_fields=["name"])
                        self.stdout.write(f"  ~ {sku}  {name}")
        self.stdout.write(self.style.SUCCESS(
            f"Tayyor: {created} ta yangi mahsulot qo'shildi, "
            f"{existing} ta allaqachon bor edi (jami {created + existing})."
        ))
