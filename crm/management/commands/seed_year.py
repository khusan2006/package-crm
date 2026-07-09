"""Seed ~1 year of sales so the dashboard graphs and KPI sparklines are full.

Uses the existing products/clients/users (run `seed_demo` first if the base data
is missing). Sales volume and size grow month-over-month so the line chart trends
upward; a share are sold on debt with varied deadlines to fill the debt donut.
"""

import random
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Expense, Payment, Product, Sale, SaleItem

USD_RATE = Decimal("12700")

EXPENSE_CATS = ["fuel", "meal", "salary", "rent", "purchase", "other"]
EXPENSE_NOTES = {
    "fuel": "Yetkazib berish — benzin",
    "meal": "Jamoa tushligi",
    "salary": "Oylik",
    "rent": "Sklad ijarasi",
    "purchase": "Paket xomashyosi",
    "other": "Kanstovarlar",
}


class Command(BaseCommand):
    help = "Bir yillik demo sotuvlar (grafiklarni to'ldirish uchun)."

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=12, help="Nechta oy (standart 12)")
        parser.add_argument(
            "--flush-sales", action="store_true",
            help="Avval barcha sotuv/to'lov/chiqimlarni o'chirish (toza boshlash)",
        )

    @transaction.atomic
    def handle(self, *args, **opts):
        products = list(Product.objects.all())
        clients = list(Client.objects.all())
        reps = list(User.objects.filter(role=User.Role.SALES)) or list(User.objects.all())
        admin = User.objects.filter(role=User.Role.ADMIN).first() or reps[0]
        if not products or not clients:
            self.stderr.write(self.style.ERROR(
                "Avval mahsulot va mijozlar kerak — 'python manage.py seed_demo' ni ishga tushiring."
            ))
            return

        if opts["flush_sales"]:
            Payment.objects.all().delete()
            Sale.objects.all().delete()
            Expense.objects.all().delete()

        rng = random.Random(2026)
        today = timezone.localdate()
        months = opts["months"]

        # Month buckets, oldest first, so the growth factor rises toward today.
        buckets = []
        y, m = today.year, today.month
        for _ in range(months):
            buckets.append((y, m))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        buckets.reverse()

        sales_made = 0
        for i, (yy, mm) in enumerate(buckets):
            growth = 1.0 + i * 0.09          # ~+9% momentum per month
            season = 1.0 + 0.15 * (mm in (3, 4, 8, 11, 12))  # a few busier months
            n_sales = int(round((10 + i) * season)) + rng.randint(-2, 3)
            last_day = monthrange(yy, mm)[1]

            for _ in range(max(n_sales, 4)):
                day = rng.randint(1, last_day)
                sale_date = date(yy, mm, day)
                if sale_date > today:
                    continue  # don't create future-dated rows in the current month

                client = rng.choice(clients)
                rep = client.owner if client.owner_id else rng.choice(reps)
                is_debt = rng.random() < 0.30
                sale = Sale.objects.create(
                    date=sale_date,
                    client=client,
                    debt_deadline=sale_date + timedelta(days=rng.choice([15, 30, 45])),
                    sales_rep=rep,
                )

                for product in rng.sample(products, rng.randint(1, min(4, len(products)))):
                    dimension = Sale.Dimension.KG if rng.random() < 0.82 else Sale.Dimension.G
                    if dimension == Sale.Dimension.KG:
                        weight = Decimal(rng.randint(5, int(120 * growth) + 40))
                        price = product.price + Decimal(rng.randint(-10, 25)) * 100
                    else:
                        weight = Decimal(rng.randint(200, 900))
                        price = (product.price + Decimal(rng.randint(-10, 25)) * 100) / 1000
                    SaleItem.objects.create(
                        sale=sale,
                        product=product,
                        dimension=dimension,
                        weight=weight,
                        price=price,
                        cost_price=product.cost_price_for(dimension),
                    )

                if not is_debt:
                    Payment.objects.create(
                        sale=sale,
                        amount=sale.total_price,
                        method=rng.choice([
                            Payment.Method.CASH, Payment.Method.CASH,
                            Payment.Method.CARD, Payment.Method.TRANSFER,
                        ]),
                        kind=Payment.Kind.SALE,
                        date=sale_date,
                        created_by=rep,
                    )
                elif rng.random() < 0.45:
                    # partial repayment on some debts → running balances + aging mix
                    Payment.objects.create(
                        sale=sale,
                        amount=(sale.total_price * Decimal("0.3")).quantize(Decimal("1")),
                        method=Payment.Method.CASH,
                        kind=Payment.Kind.DEBT,
                        date=sale_date + timedelta(days=rng.randint(1, 12)),
                        created_by=rep,
                    )
                sales_made += 1

            # A few kassa expenses each month for the cash-flow view.
            for _ in range(rng.randint(2, 4)):
                cat = rng.choice(EXPENSE_CATS)
                amount = Decimal(rng.randint(2, 40)) * Decimal("100000")
                Expense.objects.create(
                    date=date(yy, mm, rng.randint(1, last_day)),
                    amount=amount,
                    currency="uzs",
                    exchange_rate=Decimal("0"),
                    amount_original=amount,
                    category=cat,
                    method=rng.choice(["cash", "card", "transfer"]),
                    note=EXPENSE_NOTES[cat],
                    created_by=rng.choice([admin] + reps),
                )

        # A couple of USD debt repayments so the dollar till isn't empty.
        for sale in Sale.objects.outstanding().order_by("-date")[:3]:
            remaining = sale.debt_remaining
            if remaining <= 0:
                continue
            som = min(Decimal("635000"), remaining)
            Payment.objects.create(
                sale=sale,
                amount=som,
                currency=Payment.Currency.USD,
                exchange_rate=USD_RATE,
                amount_original=(som / USD_RATE).quantize(Decimal("0.01")),
                method=Payment.Method.CASH,
                kind=Payment.Kind.DEBT,
                date=today - timedelta(days=rng.randint(1, 5)),
                created_by=rng.choice(reps),
            )

        self.stdout.write(self.style.SUCCESS(
            f"Yaratildi: {sales_made} ta sotuv ({months} oy bo'yicha), to'lov va chiqimlar bilan. "
            "Dashboard grafiklari va sparkline'lar endi to'la."
        ))
