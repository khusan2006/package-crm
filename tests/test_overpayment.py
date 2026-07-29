"""Paying more than is owed.

A client may hand over more than their open debt. The receipts are settled first
and the surplus becomes their advance (kredit) — real cash in the till that covers
their next purchase, never a silently dropped amount.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.models import (
    Client,
    Payment,
    Product,
    Sale,
    SaleItem,
    client_advance_balance,
    seller_cash_on_hand,
)

PASSWORD = "test-pass-123"


def _sale(client, seller, price, days_ago=0):
    product = Product.objects.filter(sku="OVP-001").first() or Product.objects.create(
        name="Overpay paket", sku="OVP-001",
        cost_price=Decimal("1000"), price=price,
    )
    date = timezone.localdate() - timedelta(days=days_ago)
    sale = Sale.objects.create(
        client=client, sales_rep=seller, date=date,
        debt_deadline=date + timedelta(days=7),
    )
    SaleItem.objects.create(
        sale=sale, product=product, dimension=Sale.Dimension.KG,
        weight=Decimal("1"), price=price, cost_price=Decimal("1000"),
    )
    return sale


@pytest.fixture
def debtor(db, seller_user):
    return Client.objects.create(name="Overpay mijoz", owner=seller_user)


def _post_pay(django_client, url, amount, on_date=None):
    return django_client.post(url, {
        "date": (on_date or timezone.localdate()).isoformat(),
        "amount": str(amount),
        "currency": Payment.Currency.UZS,
        "method": Payment.Method.CASH,
        "note": "",
    })


@pytest.mark.django_db
def test_single_sale_overpayment_becomes_advance(client, seller_user, debtor):
    sale = _sale(debtor, seller_user, Decimal("100000"))
    client.force_login(seller_user)

    _post_pay(client, reverse("sale_pay", args=[sale.pk]), Decimal("150000"))

    sale.refresh_from_db()
    assert sale.debt_remaining == Decimal("0")            # receipt settled
    assert client_advance_balance(debtor, seller_user) == Decimal("50000")
    assert seller_cash_on_hand(seller_user) == Decimal("150000")  # all cash counted


@pytest.mark.django_db
def test_overpayment_spills_onto_other_open_receipts(client, seller_user, debtor):
    old = _sale(debtor, seller_user, Decimal("100000"), days_ago=10)
    other = _sale(debtor, seller_user, Decimal("30000"), days_ago=3)
    client.force_login(seller_user)

    _post_pay(client, reverse("sale_pay", args=[old.pk]), Decimal("150000"))

    old.refresh_from_db()
    other.refresh_from_db()
    assert old.debt_remaining == Decimal("0")
    assert other.debt_remaining == Decimal("0")           # covered by the surplus
    assert client_advance_balance(debtor, seller_user) == Decimal("20000")


@pytest.mark.django_db
def test_fifo_overpayment_leaves_the_rest_as_advance(client, seller_user, debtor):
    first = _sale(debtor, seller_user, Decimal("60000"), days_ago=10)
    second = _sale(debtor, seller_user, Decimal("40000"), days_ago=2)
    client.force_login(seller_user)

    _post_pay(client, reverse("client_debt_pay", args=[debtor.pk]), Decimal("130000"))

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.debt_remaining == Decimal("0")
    assert second.debt_remaining == Decimal("0")
    assert client_advance_balance(debtor, seller_user) == Decimal("30000")


@pytest.mark.django_db
def test_overpayment_keeps_the_backdated_day(client, seller_user, debtor):
    sale = _sale(debtor, seller_user, Decimal("100000"), days_ago=30)
    paid_on = timezone.localdate() - timedelta(days=20)
    client.force_login(seller_user)

    _post_pay(client, reverse("sale_pay", args=[sale.pk]), Decimal("120000"), on_date=paid_on)

    dates = set(Payment.objects.filter(created_by=seller_user).values_list("date", flat=True))
    assert dates == {paid_on}     # both the debt slice and the advance surplus
