"""The toolbar search box: one field that finds a client by name OR phone.

Phones are stored formatted ("+998 90 123 45 67"), so a plain `icontains` never
matched the digits people actually type. `_client_search` strips the formatting
from both sides before comparing.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from crm.models import Client, Product, Sale, SaleItem
from crm.views import _client_search


@pytest.fixture
def sale_with_phone(db, seller_user):
    client = Client.objects.create(
        name="Telefonli mijoz", owner=seller_user, phone="+998 90 123 45 67",
    )
    product = Product.objects.create(
        name="Qidiruv paket", sku="SRCH-001",
        cost_price=Decimal("1000"), price=Decimal("5000"),
    )
    sale = Sale.objects.create(
        client=client, sales_rep=seller_user,
        debt_deadline=timezone.localdate() + timedelta(days=7),
    )
    SaleItem.objects.create(
        sale=sale, product=product, dimension=Sale.Dimension.KG,
        weight=Decimal("1"), price=Decimal("5000"), cost_price=Decimal("1000"),
    )
    return sale


@pytest.mark.parametrize("term", [
    "901234567",            # bare digits, no country code
    "998901234567",         # with country code
    "+998 90 123 45 67",    # exactly as stored
    "90 123 45",            # a spaced fragment
    "Telefonli",            # still finds by name
])
@pytest.mark.django_db
def test_search_finds_the_client(sale_with_phone, term):
    assert _client_search(Sale.objects.all(), term, "client").count() == 1


@pytest.mark.django_db
def test_search_does_not_match_a_different_number(sale_with_phone):
    assert _client_search(Sale.objects.all(), "999888777", "client").count() == 0


@pytest.mark.django_db
def test_client_list_page_finds_by_bare_digits(client, seller_user, sale_with_phone):
    """The Mijozlar page searches the Client rows themselves (no `base` path)."""
    client.force_login(seller_user)
    found = client.get("/clients/", {"q": "901234567"})
    assert b"Telefonli mijoz" in found.content

    missed = client.get("/clients/", {"q": "999888777"})
    assert b"Telefonli mijoz" not in missed.content
