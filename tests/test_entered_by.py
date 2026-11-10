"""«Kiritgan»: every new sale remembers who typed it in, beside whose sale it is —
a seller writing their own, or an admin writing it in a seller's name."""

from decimal import Decimal

import pytest
from django.urls import reverse

from crm.models import Client, Product, Sale
from crm.tests import one_item, sale_post


@pytest.fixture
def product(db):
    return Product.objects.create(name="Paket", sku="P-1", cost_price=Decimal("10000"),
                                  price=Decimal("15000"))


@pytest.fixture
def anvar(seller_user):
    return Client.objects.create(name="Anvar", owner=seller_user)


def test_a_sellers_own_sale_is_entered_by_them(client, seller_user, anvar, product):
    client.force_login(seller_user)
    client.post(reverse("sale_create"), sale_post(anvar.pk, [one_item(product)]))
    sale = Sale.objects.get()
    assert (sale.sales_rep, sale.entered_by) == (seller_user, seller_user)


def test_an_admin_writing_for_a_seller_is_kept_as_kiritgan(client, admin_user, seller_user,
                                                          anvar, product):
    client.force_login(admin_user)
    client.post(reverse("sale_create"),
                sale_post(anvar.pk, [one_item(product)], seller=seller_user.pk))
    sale = Sale.objects.get()
    assert (sale.sales_rep, sale.entered_by) == (seller_user, admin_user)
    detail = client.get(reverse("sale_detail", args=[sale.pk])).content.decode()
    assert "Kiritgan" in detail and str(admin_user) in detail
    listing = client.get(reverse("sale_list")).content.decode()
    assert f"kiritgan: {admin_user}" in listing
