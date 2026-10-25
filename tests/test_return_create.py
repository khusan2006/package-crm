"""Yangi vazvrat — one visit, many receipts.

Pins down what the dialog promises: every typed row becomes its own return settled
exactly like a single one, each receipt's return cancels that receipt's own debt
first (credit from one row never pays down a receipt whose return comes next), the
save is all-or-nothing, and a seller can only reach their own clients' lines.
"""

import html
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Expense, Payment, Return, Sale, SaleItem

pytestmark = pytest.mark.django_db

AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def _sale(client, seller, product, weight, paid="0"):
    sale = Sale.objects.create(client=client, sales_rep=seller)
    item = SaleItem.objects.create(
        sale=sale, product=product, dimension=Sale.Dimension.KG,
        weight=Decimal(weight), price=Decimal("15000"), cost_price=Decimal("10000"),
    )
    if Decimal(paid):
        Payment.objects.create(
            sale=sale, amount=Decimal(paid), amount_original=Decimal(paid),
            method=Payment.Method.CASH, kind=Payment.Kind.SALE,
            date=timezone.localdate(), created_by=seller,
        )
    return sale, item


def _post(client, customer, rows, **extra):
    data = {
        "client": customer.pk,
        "date": timezone.localdate().isoformat(),
        "settlement": "advance",
        "restock": "on",
        "note": "",
    }
    data.update({f"ret_{item.pk}": qty for item, qty in rows})
    data.update(extra)
    return client.post("/vazvrat/yangi/", data, **AJAX)


def test_dialog_lists_the_clients_returnable_lines(client, admin_user, sample_data):
    client.force_login(admin_user)
    resp = client.get("/vazvrat/yangi/", {"client": sample_data["client"].pk}, **AJAX)
    assert resp.status_code == 200
    lines = resp.context["lines"]
    assert [line.left for line in lines] == [Decimal("10")]
    assert lines[0].open_debt == Decimal("100000")
    assert 'name="ret_%d"' % lines[0].pk in resp.content.decode()


def test_rows_from_several_receipts_save_as_separate_returns(
    client, admin_user, seller_user, sample_data
):
    """A paid receipt hands its value back as credit; an unpaid one only shrinks its
    own debt — even when the paid one is saved first. Spending the credit after each
    row would have paid the unpaid receipt down and reported its return as credit."""
    customer, product = sample_data["client"], sample_data["product"]
    paid_sale, paid_item = _sale(customer, seller_user, product, "5", paid="75000")
    unpaid_sale, unpaid_item = _sale(customer, seller_user, product, "5")

    client.force_login(admin_user)
    resp = _post(client, customer, [(paid_item, "5"), (unpaid_item, "5")])
    assert resp.status_code == 204
    assert resp["X-Redirect"] == "/vazvrat/"

    paid_ret = Return.objects.get(sale=paid_sale)
    unpaid_ret = Return.objects.get(sale=unpaid_sale)
    assert paid_ret.settlement.kind == Payment.Kind.RETURN_CREDIT
    assert paid_ret.settlement.amount == Decimal("75000")
    assert unpaid_ret.settlement is None  # 75 000 off its own debt, no money moved
    assert paid_ret.created_by == seller_user  # booked to the receipt's seller
    assert paid_ret.restock and unpaid_ret.restock

    totals = client.get("/vazvrat/").context["totals"]
    assert totals["to_debt"] == Decimal("75000")
    assert totals["advance"] == Decimal("75000")


def test_two_lines_off_one_receipt_split_against_the_shrinking_debt(
    client, admin_user, seller_user, sample_data
):
    """Receipt of 2 × 150 000 with 250 000 paid owes 50 000. The first 100 000 line
    cancels that and parks 50 000; the second sees no debt left and parks it all."""
    customer, product = sample_data["client"], sample_data["product"]
    sale = Sale.objects.create(client=customer, sales_rep=seller_user)
    first = SaleItem.objects.create(
        sale=sale, product=product, dimension=Sale.Dimension.KG,
        weight=Decimal("10"), price=Decimal("15000"), cost_price=Decimal("10000"),
    )
    second = SaleItem.objects.create(
        sale=sale, product=product, dimension=Sale.Dimension.KG,
        weight=Decimal("10"), price=Decimal("15000"), cost_price=Decimal("10000"),
    )
    Payment.objects.create(
        sale=sale, amount=Decimal("250000"), amount_original=Decimal("250000"),
        method=Payment.Method.CASH, kind=Payment.Kind.SALE,
        date=timezone.localdate(), created_by=seller_user,
    )

    client.force_login(admin_user)
    assert _post(client, customer, [(first, "4"), (second, "4")]).status_code == 204
    credits = {
        r.sale_item_id: r.settlement.amount for r in Return.objects.filter(sale=sale)
    }
    # 60 000 each: the first cancels the 50 000 debt and parks 10 000, the second
    # finds no debt left and parks all 60 000.
    assert credits == {first.pk: Decimal("10000"), second.pk: Decimal("60000")}
    sale = Sale.objects.get(pk=sale.pk)
    assert sale.debt_remaining == Decimal("0")


def test_one_bad_row_saves_nothing(client, admin_user, seller_user, sample_data):
    customer, product = sample_data["client"], sample_data["product"]
    _, other_item = _sale(customer, seller_user, product, "5")
    item = sample_data["sale"].items.first()

    client.force_login(admin_user)
    resp = _post(client, customer, [(item, "2"), (other_item, "9")])  # only 5 left
    assert resp.status_code == 422
    assert Return.objects.count() == 0
    assert "ko'pi bilan 5" in html.unescape(resp.content.decode())
    # The table comes back filled in.
    assert 'value="2"' in resp.content.decode()


def test_refunds_that_together_outrun_the_till_roll_back(
    client, admin_user, seller_user, sample_data
):
    """Each refund fits the till on its own; the second does not fit once the first
    has gone out. The till check sees the earlier row's cash, so nothing is saved."""
    customer, product = sample_data["client"], sample_data["product"]
    _, first = _sale(customer, seller_user, product, "20", paid="300000")
    _, second = _sale(customer, seller_user, product, "20", paid="300000")
    # Till: 50 000 + 300 000 + 300 000 in, 100 000 out → 550 000.
    Expense.objects.create(
        date=timezone.localdate(), amount=Decimal("100000"),
        amount_original=Decimal("100000"), category="Ijara", method="cash",
        created_by=seller_user,
    )
    client.force_login(admin_user)
    resp = _post(client, customer, [(first, "20"), (second, "20")], settlement="refund")
    assert resp.status_code == 422
    assert Return.objects.count() == 0
    assert not Payment.objects.filter(kind=Payment.Kind.REFUND_OUT).exists()
    assert "kassada pul yetarli emas" in resp.content.decode()

    # Either one alone goes through, in cash.
    assert _post(client, customer, [(first, "20")], settlement="refund").status_code == 204
    assert Return.objects.get().settlement.kind == Payment.Kind.REFUND_OUT


def test_unchecked_restock_keeps_goods_off_the_shelf(client, admin_user, sample_data):
    item = sample_data["sale"].items.first()
    client.force_login(admin_user)
    data = {"restock": ""}
    resp = _post(client, sample_data["client"], [(item, "1")], **data)
    assert resp.status_code == 204
    assert Return.objects.get().restock is False


def test_seller_cannot_reach_another_sellers_client(client, seller_user, sample_data):
    other = User.objects.create_user(username="boshqa", password="x", role=User.Role.SALES)
    stranger = Client.objects.create(name="Boshqa mijoz", owner=other)
    _, item = _sale(stranger, other, sample_data["product"], "5")

    client.force_login(seller_user)
    resp = _post(client, stranger, [(item, "1")])
    assert resp.status_code == 422
    assert Return.objects.count() == 0

    # Nor a stranger's line smuggled under their own client.
    resp = _post(client, sample_data["client"], [(item, "1")])
    assert resp.status_code == 422
    assert Return.objects.count() == 0
