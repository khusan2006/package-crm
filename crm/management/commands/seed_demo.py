"""Seed demo users, products, clients, and orders for local development."""

import random
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import User
from crm.models import Client, Order, OrderItem, Product

DEMO_USERS = [
    ("admin", "Admin", "User", User.Role.ADMIN),
    ("manager", "Malika", "Karimova", User.Role.MANAGER),
    ("sales1", "Bekzod", "Rahimov", User.Role.SALES),
    ("sales2", "Dilnoza", "Yusupova", User.Role.SALES),
]

DEMO_PRODUCTS = [
    ("Corrugated box 40×30×30", "BOX-403030", Product.Unit.PIECE, "4500", 1200),
    ("Corrugated box 60×40×40", "BOX-604040", Product.Unit.PIECE, "7800", 800),
    ("Stretch film 500mm 2kg", "FILM-500-2", Product.Unit.ROLL, "58000", 150),
    ("Bubble wrap 1m×50m", "BUBL-1X50", Product.Unit.ROLL, "95000", 60),
    ("Kraft paper 80g", "KRFT-80", Product.Unit.KG, "14000", 500),
    ("Packing tape 48mm brown", "TAPE-48BR", Product.Unit.PIECE, "6500", 2000),
    ("Zip-lock bag 20×30", "ZIP-2030", Product.Unit.BOX, "42000", 90),
]

DEMO_CLIENTS = [
    ("Anvar Toshmatov", "Samarqand Sweets LLC", "+998901112233"),
    ("Gulnora Azimova", "Tashkent Textiles", "+998909876543"),
    ("Rustam Nazarov", "FreshFruit Export", "+998933334455"),
    ("Kamola Ergasheva", "Silk Road Ceramics", "+998971234567"),
    ("Javlon Mirzaev", "Bukhara Bakery Group", "+998935556677"),
    ("Nilufar Saidova", "GreenLeaf Pharma", "+998907778899"),
]


class Command(BaseCommand):
    help = "Create demo users (password: demo1234), products, clients, and orders."

    @transaction.atomic
    def handle(self, *args, **options):
        if User.objects.filter(username="admin").exists():
            self.stdout.write(self.style.WARNING("Demo data already seeded — skipping."))
            return

        rng = random.Random(42)

        users = {}
        for username, first, last, role in DEMO_USERS:
            user = User.objects.create_user(
                username=username,
                password="demo1234",
                first_name=first,
                last_name=last,
                email=f"{username}@example.com",
                role=role,
                is_staff=(role == User.Role.ADMIN),
                is_superuser=(role == User.Role.ADMIN),
            )
            users[username] = user

        products = [
            Product.objects.create(
                name=name, sku=sku, unit=unit, price=Decimal(price), stock=stock
            )
            for name, sku, unit, price, stock in DEMO_PRODUCTS
        ]

        reps = [users["sales1"], users["sales2"]]
        clients = [
            Client.objects.create(
                name=name, company=company, phone=phone, owner=rng.choice(reps)
            )
            for name, company, phone in DEMO_CLIENTS
        ]

        statuses = [
            Order.Status.DRAFT,
            Order.Status.CONFIRMED,
            Order.Status.SHIPPED,
            Order.Status.PAID,
            Order.Status.PAID,
            Order.Status.CANCELLED,
        ]
        for _ in range(18):
            client = rng.choice(clients)
            order = Order.objects.create(
                client=client,
                sales_rep=client.owner,
                status=rng.choice(statuses),
            )
            for product in rng.sample(products, rng.randint(1, 3)):
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    quantity=rng.randint(5, 200),
                    unit_price=product.price,
                )

        self.stdout.write(self.style.SUCCESS(
            "Seeded: 4 users (admin/manager/sales1/sales2, password demo1234), "
            f"{len(products)} products, {len(clients)} clients, 18 orders."
        ))
