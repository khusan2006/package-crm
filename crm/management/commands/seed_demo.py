"""Seed demo users, products, clients, and sales for local development."""

import random
from datetime import timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Product, Sale

DEMO_USERS = [
    ("admin", "Admin", "User", User.Role.ADMIN),
    ("manager", "Malika", "Karimova", User.Role.MANAGER),
    ("sales1", "Bekzod", "Rahimov", User.Role.SALES),
    ("sales2", "Dilnoza", "Yusupova", User.Role.SALES),
]

# (name, sku, tannarx per kg, sotish narxi per kg)
DEMO_PRODUCTS = [
    ("Polietilen paket 24×37", "PKT-2437", "18000", "24000"),
    ("Polietilen paket 30×40", "PKT-3040", "19000", "26000"),
    ("Polietilen paket 40×50", "PKT-4050", "20000", "27500"),
    ("Mayka paket 28×50", "MYK-2850", "17000", "23000"),
    ("Rulonli paket 25×35", "RUL-2535", "21000", "28000"),
    ("Zip paket 15×20", "ZIP-1520", "35000", "48000"),
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
                name=name, sku=sku, cost_price=Decimal(cost), price=Decimal(price)
            )
            for name, sku, cost, price in DEMO_PRODUCTS
        ]

        reps = [users["sales1"], users["sales2"]]
        clients = [
            Client.objects.create(
                name=name, company=company, phone=phone, owner=rng.choice(reps)
            )
            for name, company, phone in DEMO_CLIENTS
        ]

        for _ in range(30):
            client = rng.choice(clients)
            product = rng.choice(products)
            dimension = Sale.Dimension.KG if rng.random() < 0.8 else Sale.Dimension.G
            if dimension == Sale.Dimension.KG:
                weight = Decimal(rng.randint(5, 300))
                price = product.price + Decimal(rng.randint(-10, 25)) * 100
            else:
                weight = Decimal(rng.randint(200, 900))
                price = (product.price + Decimal(rng.randint(-10, 25)) * 100) / 1000
            is_debt = rng.random() < 0.3
            sale_date = today - timedelta(days=rng.randint(0, 45))
            Sale.objects.create(
                date=sale_date,
                client=client,
                product=product,
                dimension=dimension,
                weight=weight,
                price=price,
                cost_price=product.cost_price_for(dimension),
                is_debt=is_debt,
                debt_deadline=(
                    sale_date + timedelta(days=rng.choice([15, 30, 45])) if is_debt else None
                ),
                sales_rep=client.owner,
            )

        self.stdout.write(self.style.SUCCESS(
            "Yaratildi: 4 foydalanuvchi (admin/manager/sales1/sales2, parol demo1234), "
            f"{len(products)} mahsulot, {len(clients)} mijoz, 30 sotuv."
        ))
