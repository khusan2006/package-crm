"""Editing a Kirim (payment) from the Kassa ledger.

`sample_data` gives a 150 000 so'm sale with a single 50 000 so'm cash payment
(kind=SALE, created_by=seller), so the sale's remaining debt starts at 100 000.
These pin down that an edit re-derives the debt, can't over-pay the sale, and is
scoped to the payment's owner for sellers.
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import User
from crm.models import Payment

pytestmark = pytest.mark.django_db


def _payment(sample_data):
    return sample_data["sale"].payments.get()


def _post(pk, **overrides):
    data = {
        "date": timezone.localdate().isoformat(),
        "amount": "70000",
        "currency": "uzs",
        "exchange_rate": "",
        "method": "cash",
        "commission_percent": "",
        "note": "",
    }
    data.update(overrides)
    return f"/payments/{pk}/edit/", data


def test_admin_edit_amount_rederives_debt(client, admin_user, sample_data):
    client.force_login(admin_user)
    payment = _payment(sample_data)
    url, data = _post(payment.pk, amount="70000")
    resp = client.post(url, data)
    assert resp.status_code in (200, 204, 302)
    payment.refresh_from_db()
    assert payment.amount == Decimal("70000")
    # 150 000 total − 70 000 paid = 80 000 still owed.
    assert sample_data["sale"].debt_remaining == Decimal("80000")


def test_overpayment_rejected(client, admin_user, sample_data):
    client.force_login(admin_user)
    payment = _payment(sample_data)
    # Net ceiling is remaining (100 000) + this payment's own net (50 000) = 150 000.
    url, data = _post(payment.pk, amount="200000")
    resp = client.post(url, data)
    assert resp.status_code in (200, 422)  # invalid form re-render, not a redirect
    payment.refresh_from_db()
    assert payment.amount == Decimal("50000")  # unchanged
    assert sample_data["sale"].debt_remaining == Decimal("100000")


def test_edit_up_to_full_settlement_allowed(client, admin_user, sample_data):
    client.force_login(admin_user)
    payment = _payment(sample_data)
    url, data = _post(payment.pk, amount="150000")  # exactly settles the sale
    resp = client.post(url, data)
    assert resp.status_code in (200, 204, 302)
    payment.refresh_from_db()
    assert payment.amount == Decimal("150000")
    assert sample_data["sale"].debt_remaining == Decimal("0")
    assert sample_data["sale"].is_paid


def test_seller_can_edit_own_payment(client, seller_user, sample_data):
    client.force_login(seller_user)  # seller_user owns the sample payment
    payment = _payment(sample_data)
    url, data = _post(payment.pk, amount="60000")
    resp = client.post(url, data)
    assert resp.status_code in (200, 204, 302)
    payment.refresh_from_db()
    assert payment.amount == Decimal("60000")


def test_seller_cannot_edit_others_payment(client, sample_data):
    other = User.objects.create_user(
        username="other_seller", password="x", role=User.Role.SALES,
    )
    client.force_login(other)
    payment = _payment(sample_data)  # created_by the fixture's seller, not `other`
    assert client.get(f"/payments/{payment.pk}/edit/").status_code == 404
    url, data = _post(payment.pk, amount="70000")
    assert client.post(url, data).status_code == 404
    payment.refresh_from_db()
    assert payment.amount == Decimal("50000")  # untouched
