"""Shared pytest fixtures for the CRM test suite.

Provides ready-made users (admin + seller) and a small slice of sample data, plus
a Playwright helper that logs a browser session in through the real login form.
Everything lives in the isolated SQLite test database (see config/settings_test).
"""

import os
from datetime import timedelta
from decimal import Decimal

# Playwright's sync API runs the test inside a live event loop; Django's ORM
# guards against being called from an async context. The two are safe together
# here (single-threaded greenlet), so opt out of the guard for the test process.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")

import pytest
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Payment, Product, Sale, SaleItem

PASSWORD = "test-pass-123"


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        username="e2e_admin", password=PASSWORD, role=User.Role.ADMIN,
        first_name="Adm", last_name="In", is_staff=True, is_superuser=True,
    )


@pytest.fixture
def seller_user(db):
    return User.objects.create_user(
        username="e2e_seller", password=PASSWORD, role=User.Role.SALES,
        first_name="Sel", last_name="Ler",
    )


@pytest.fixture
def sample_data(db, admin_user, seller_user):
    """A minimal but realistic dataset: a product, a client, and one credit sale
    with a partial payment — enough to render every list, the dashboard and kassa."""
    product = Product.objects.create(
        name="Test paket", sku="TST-001", cost_price=Decimal("10000"),
        price=Decimal("15000"), low_stock_threshold=Decimal("5"),
    )
    client = Client.objects.create(name="Test mijoz", owner=seller_user)
    sale = Sale.objects.create(
        client=client, sales_rep=seller_user,
        debt_deadline=timezone.localdate() + timedelta(days=7),
    )
    SaleItem.objects.create(
        sale=sale, product=product, dimension=Sale.Dimension.KG,
        weight=Decimal("10"), price=Decimal("15000"), cost_price=Decimal("10000"),
    )
    Payment.objects.create(
        sale=sale, amount=Decimal("50000"), amount_original=Decimal("50000"),
        method=Payment.Method.CASH, kind=Payment.Kind.SALE,
        date=timezone.localdate(), created_by=seller_user,
    )
    return {"product": product, "client": client, "sale": sale}


@pytest.fixture
def login(page, live_server):
    """Return a helper: login(user) signs `page` in through the login form and
    lands on the dashboard. Password is the shared test password."""
    def _login(user):
        page.goto(f"{live_server.url}/login/")
        page.fill('input[name="username"]', user.username)
        page.fill('input[name="password"]', PASSWORD)
        page.click('button[type="submit"]')
        page.wait_for_url(f"{live_server.url}/")
        return page

    return _login
