"""Mijozlar to'lovlari — the page that answers "what has this client paid us".

Pins down the three things the page promises: it lists the money clients actually
handed over (and only that), it groups the same money per client, and a seller sees
their own work only.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Payment, Sale

pytestmark = pytest.mark.django_db


def _advance(client, seller, amount, **kwargs):
    return Payment.objects.create(
        client=client, amount=Decimal(amount), amount_original=Decimal(amount),
        method=Payment.Method.CASH, kind=Payment.Kind.ADVANCE_IN,
        date=timezone.localdate(), created_by=seller, **kwargs,
    )


def test_page_lists_todays_payments(client, admin_user, sample_data):
    client.force_login(admin_user)
    resp = client.get("/tolovlar/")
    assert resp.status_code == 200
    assert resp.context["totals"]["total"] == Decimal("50000")
    assert resp.context["totals"]["count"] == 1
    assert resp.context["totals"]["clients"] == 1
    assert "Test mijoz" in resp.content.decode()


def _advance_used(sale, seller, amount):
    return Payment.objects.create(
        sale=sale, amount=Decimal(amount), amount_original=Decimal(amount),
        method=Payment.Method.CASH, kind=Payment.Kind.ADVANCE_USED,
        date=timezone.localdate(), created_by=seller,
    )


def test_advance_deposit_counts_but_its_consumption_does_not(
    client, admin_user, seller_user, sample_data
):
    """A deposit is money handed over; spending that credit on a receipt is not —
    counting both would show the same so'm twice. The spend is still listed, so the
    page can say how the receipt got closed; it just never reaches a total."""
    _advance(sample_data["client"], seller_user, "30000")
    _advance_used(sample_data["sale"], seller_user, "30000")

    client.force_login(admin_user)
    resp = client.get("/tolovlar/")
    totals = resp.context["totals"]
    assert totals["total"] == Decimal("80000")  # 50 000 sale + 30 000 advance
    assert totals["count"] == 2
    assert totals["from_advance"] == Decimal("30000")

    rows = list(resp.context["page"])
    assert len(rows) == 3  # the spend is shown…
    spent = [r for r in rows if not r["counted"]]
    assert len(spent) == 1 and spent[0]["amount"] == Decimal("30000")
    assert "jamiga kirmaydi" in resp.content.decode()


def test_opening_advance_is_left_out(client, admin_user, seller_user, sample_data):
    """Its cash arrived before the CRM (or earlier, outside the drawer), so it is not
    money received on its date."""
    _advance(sample_data["client"], seller_user, "70000", is_opening=True)
    client.force_login(admin_user)
    assert client.get("/tolovlar/").context["totals"]["total"] == Decimal("50000")


def test_date_window_bites_but_a_client_filter_searches_every_date(
    client, admin_user, seller_user, sample_data
):
    old = _advance(sample_data["client"], seller_user, "25000")
    old.date = timezone.localdate() - timedelta(days=40)
    old.save(update_fields=["date"])

    client.force_login(admin_user)
    assert client.get("/tolovlar/").context["totals"]["total"] == Decimal("50000")
    resp = client.get("/tolovlar/", {"client": sample_data["client"].pk})
    assert resp.context["totals"]["total"] == Decimal("75000")


def test_client_view_groups_the_same_money(client, admin_user, seller_user, sample_data):
    _advance(sample_data["client"], seller_user, "30000")
    _advance_used(sample_data["sale"], seller_user, "30000")
    client.force_login(admin_user)
    resp = client.get("/tolovlar/", {"korinish": "mijoz"})
    assert resp.status_code == 200
    rows = list(resp.context["page"])
    assert len(rows) == 1
    assert rows[0]["client"] == sample_data["client"]
    assert rows[0]["total"] == Decimal("80000")
    assert rows[0]["count"] == 2
    # Reported beside the total, never inside it.
    assert rows[0]["from_advance"] == Decimal("30000")
    # The view is chosen in the filter drawer, which must show which one is open.
    assert 'value="mijoz" selected' in resp.content.decode()


def test_a_spent_advance_opens_read_only(client, admin_user, seller_user, sample_data):
    """It carries no edit/delete: the row is written by the reconciliation, so a
    hand-typed figure would be recalculated away. The panel still opens."""
    spent = _advance_used(sample_data["sale"], seller_user, "10000")
    client.force_login(admin_user)
    resp = client.get(f"/kassa/amal/advance_used/{spent.pk}/")
    assert resp.status_code == 200
    assert "edit_url" not in resp.context
    assert "delete_url" not in resp.context
    assert "Avansdan yechildi" in resp.content.decode()


def test_last_payment_ignores_an_advance_being_spent(
    client, admin_user, seller_user, sample_data
):
    """"Oxirgi to'lov" must mean the last time they PAID; a receipt closed from credit
    they left months ago is not a fresh payment."""
    sale_payment = sample_data["sale"].payments.first()
    sale_payment.date = timezone.localdate() - timedelta(days=40)
    sale_payment.save(update_fields=["date"])
    _advance_used(sample_data["sale"], seller_user, "10000")

    client.force_login(admin_user)
    resp = client.get(
        "/tolovlar/", {"korinish": "mijoz", "client": sample_data["client"].pk}
    )
    row = list(resp.context["page"])[0]
    assert row["last"] == timezone.localdate() - timedelta(days=40)


def test_seller_sees_only_their_own_clients_payments(
    client, seller_user, admin_user, sample_data
):
    other_seller = User.objects.create_user(
        username="other_seller", password="x", role=User.Role.SALES,
    )
    other_client = Client.objects.create(name="Boshqa mijoz", owner=other_seller)
    other_sale = Sale.objects.create(client=other_client, sales_rep=other_seller)
    Payment.objects.create(
        sale=other_sale, amount=Decimal("11000"), amount_original=Decimal("11000"),
        method=Payment.Method.CASH, kind=Payment.Kind.DEBT,
        date=timezone.localdate(), created_by=other_seller,
    )
    client.force_login(seller_user)
    resp = client.get("/tolovlar/")
    assert resp.context["totals"]["total"] == Decimal("50000")
    assert "Boshqa mijoz" not in resp.content.decode()


def test_export_downloads_a_workbook(client, admin_user, sample_data):
    client.force_login(admin_user)
    resp = client.get("/tolovlar/export/")
    assert resp.status_code == 200
    assert resp["Content-Type"].startswith("application/vnd.openxmlformats")
