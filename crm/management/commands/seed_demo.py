"""Seed demo users, products, clients, and sales for local development."""

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Expense, Payment, Product, Sale, SaleItem, StockEntry

USD_RATE = Decimal("12700")

# (days ago, category, method, currency, amount|usd, note, user key)
DEMO_EXPENSES = [
    (2, "fuel", "cash", "uzs", "450000", "Yetkazib berish — benzin", "sales1"),
    (2, "meal", "cash", "uzs", "120000", "Tushlik", "sales2"),
    (5, "salary", "card", "uzs", "3500000", "Oylik avans", "admin"),
    (8, "rent", "transfer", "uzs", "4000000", "Sklad ijarasi", "manager"),
    (10, "purchase", "cash", "usd", "150", "Xomashyo (dollarda)", "admin"),
    (12, "fuel", "cash", "uzs", "380000", "Benzin", "sales1"),
    (15, "other", "cash", "uzs", "250000", "Kanstovarlar", "manager"),
    (18, "meal", "cash", "uzs", "160000", "Jamoa tushligi", "sales2"),
    (22, "purchase", "transfer", "uzs", "2200000", "Paket xomashyosi", "admin"),
    (26, "salary", "card", "uzs", "3500000", "Oylik", "admin"),
]

DEMO_USERS = [
    ("admin", "Admin", "User", User.Role.ADMIN),
    ("manager", "Malika", "Karimova", User.Role.MANAGER),
    ("sales1", "Bekzod", "Rahimov", User.Role.SALES),
    ("sales2", "Dilnoza", "Yusupova", User.Role.SALES),
]

# (name, sku, tannarx per kg, sotish narxi per kg, low-stock threshold kg)
DEMO_PRODUCTS = [
    ("Polietilen paket 24×37", "PKT-2437", "18000", "24000", "300"),
    ("Polietilen paket 30×40", "PKT-3040", "19000", "26000", "300"),
    ("Polietilen paket 40×50", "PKT-4050", "20000", "27500", "300"),
    ("Mayka paket 28×50", "MYK-2850", "17000", "23000", "200"),
    ("Rulonli paket 25×35", "RUL-2535", "21000", "28000", "200"),
    ("Zip paket 15×20", "ZIP-1520", "35000", "48000", "100"),
]

DEMO_CLIENTS = [
    ("Anvar Toshmatov", "Samarqand Shirinliklari MChJ", "+998901112233"),
    ("Gulnora Azimova", "Toshkent To'qimachilik", "+998909876543"),
    ("Rustam Nazarov", "FreshFruit Export", "+998933334455"),
    ("Kamola Ergasheva", "Ipak Yo'li Kulolchilik", "+998971234567"),
    ("Javlon Mirzaev", "Buxoro Non Guruhi", "+998935556677"),
    ("Nilufar Saidova", "GreenLeaf Pharma", "+998907778899"),
]


class Command(BaseCommand):
    help = "Demo ma'lumotlar: foydalanuvchilar (parol: demo1234), mahsulotlar, mijozlar, sotuvlar."

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(username="admin").exists():
            self.stdout.write(self.style.WARNING("Demo ma'lumotlar allaqachon mavjud."))
            return

        rng = random.Random(42)
        today = timezone.localdate()

        users = {}
        for username, first, last, role in DEMO_USERS:
            users[username] = User.objects.create_user(
                username=username,
                password="demo1234",
                first_name=first,
                last_name=last,
                email=f"{username}@example.com",
                role=role,
                is_staff=(role == User.Role.ADMIN),
                is_superuser=(role == User.Role.ADMIN),
            )

        products = [
            Product.objects.create(
                name=name,
                sku=sku,
                cost_price=Decimal(cost),
                price=Decimal(price),
                low_stock_threshold=Decimal(threshold),
            )
            for name, sku, cost, price, threshold in DEMO_PRODUCTS
        ]

        # Opening stock (kirim) per product so warehouse balances start positive
        for product in products:
            StockEntry.objects.create(
                product=product,
                date=today - timedelta(days=55),
                quantity_kg=Decimal(rng.randint(2000, 3500)),
                note="Boshlang'ich qoldiq",
                created_by=users["admin"],
            )
            StockEntry.objects.create(
                product=product,
                date=today - timedelta(days=rng.randint(5, 25)),
                quantity_kg=Decimal(rng.randint(400, 900)),
                note="To'ldirish",
                created_by=users["manager"],
            )

        reps = [users["sales1"], users["sales2"]]
        clients = [
            Client.objects.create(
                name=name, company=company, phone=phone, owner=rng.choice(reps)
            )
            for name, company, phone in DEMO_CLIENTS
        ]

        for _ in range(30):
            client = rng.choice(clients)
            is_debt = rng.random() < 0.3
            sale_date = today - timedelta(days=rng.randint(0, 45))
            # Every sale is a receivable with a deadline; whether it reads as
            # "paid" depends only on the payments recorded below.
            sale = Sale.objects.create(
                date=sale_date,
                client=client,
                debt_deadline=sale_date + timedelta(days=rng.choice([15, 30, 45])),
                sales_rep=client.owner,
            )
            # Each receipt carries 1–4 product lines
            for product in rng.sample(products, rng.randint(1, 4)):
                dimension = Sale.Dimension.KG if rng.random() < 0.8 else Sale.Dimension.G
                if dimension == Sale.Dimension.KG:
                    weight = Decimal(rng.randint(5, 300))
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
                    method=rng.choice([Payment.Method.CASH, Payment.Method.CARD]),
                    kind=Payment.Kind.SALE,
                    date=sale_date,
                    created_by=sale.sales_rep,
                )
            elif rng.random() < 0.4:
                # a partial repayment on some debts, to show running balances
                Payment.objects.create(
                    sale=sale,
                    amount=(sale.total_price * Decimal("0.3")).quantize(Decimal("1")),
                    method=Payment.Method.CASH,
                    kind=Payment.Kind.DEBT,
                    date=sale_date + timedelta(days=rng.randint(1, 10)),
                    created_by=sale.sales_rep,
                )

        # Chiqimlar (kassa rasxotlari) — turli turkum, hamyon va valyutada
        for days_ago, category, method, currency, amount, note, ukey in DEMO_EXPENSES:
            original = Decimal(amount)
            som = original * USD_RATE if currency == "usd" else original
            Expense.objects.create(
                date=today - timedelta(days=days_ago),
                amount=som,
                currency=currency,
                exchange_rate=USD_RATE if currency == "usd" else Decimal("0"),
                amount_original=original,
                category=category,
                method=method,
                note=note,
                created_by=users[ukey],
            )

        # Bir nechta dollar qarz to'lovi — dollar sandig'i bo'sh qolmasligi uchun
        # (har biri qarzdan oshib ketmasligi uchun qoldiqqacha cheklanadi).
        for sale in Sale.objects.outstanding()[:3]:
            remaining = sale.debt_remaining
            if remaining <= 0:
                continue
            som = min(Decimal("635000"), remaining)  # ~$50 chamasida
            Payment.objects.create(
                sale=sale,
                amount=som,
                currency=Payment.Currency.USD,
                exchange_rate=USD_RATE,
                amount_original=(som / USD_RATE).quantize(Decimal("0.01")),
                method=Payment.Method.CASH,
                kind=Payment.Kind.DEBT,
                date=today - timedelta(days=rng.randint(1, 5)),
                created_by=sale.sales_rep,
            )

        self.stdout.write(self.style.SUCCESS(
            "Yaratildi: 4 foydalanuvchi (admin/manager/sales1/sales2, parol demo1234), "
            f"{len(products)} mahsulot (ombor kirimlari bilan), {len(clients)} mijoz, 30 sotuv, "
            f"{len(DEMO_EXPENSES)} chiqim va dollar to'lovlar."
        ))
