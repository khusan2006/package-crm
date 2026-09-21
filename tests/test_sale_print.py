"""Yuk xati — the sale as the paper form the goods travel with.

Pins down what the printout has to carry (client, goods, money), that it is printed
twice on one sheet so seller and client each keep a half, and that a seller cannot
print someone else's receipt.
"""

from decimal import Decimal

import pytest

from django.utils import timezone

from crm.models import Payment, Sale

pytestmark = pytest.mark.django_db


def test_print_page_carries_the_receipt(client, admin_user, sample_data):
    sale = sample_data["sale"]
    client.force_login(admin_user)
    resp = client.get(f"/sales/{sale.pk}/yuk-xati/")

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "YUK XATI/НАКЛАДНАЯ" in body
    assert "Test Mijoz" in body       # Kimga — title-cased, client names are stored SHOUTING
    assert "Sel Ler" in body           # Kimdan — the seller who released them
    assert "Test paket" in body
    # 10 kg × 15 000 = 150 000, space-grouped the way every screen prints money.
    assert "150\xa0000" in body


def test_sheet_holds_two_copies(client, admin_user, sample_data):
    """One half stays with the seller, the other is cut off and goes with the load."""
    client.force_login(admin_user)
    resp = client.get(f"/sales/{sample_data['sale'].pk}/yuk-xati/")

    assert resp.content.decode().count("YUK XATI/НАКЛАДНАЯ") == 2


def test_short_receipt_keeps_the_ruled_lines(client, admin_user, sample_data):
    """A one-line sale still looks like the pre-printed pad: 21 lines, the last of
    the goods rows numbered 21."""
    client.force_login(admin_user)
    resp = client.get(f"/sales/{sample_data['sale'].pk}/yuk-xati/")

    body = resp.content.decode()
    assert body.count('<td class="c-no">21</td>') == 2


def test_opening_balance_has_nothing_to_hand_over(client, admin_user, sample_data):
    opening = Sale.objects.create(
        client=sample_data["client"],
        sales_rep=admin_user,
        is_opening=True,
        opening_amount=Decimal("100000"),
    )
    client.force_login(admin_user)

    assert client.get(f"/sales/{opening.pk}/yuk-xati/").status_code == 404


def test_seller_cannot_print_another_sellers_receipt(
    client, seller_user, admin_user, sample_data
):
    other = Sale.objects.create(client=sample_data["client"], sales_rep=admin_user)
    client.force_login(seller_user)

    assert client.get(f"/sales/{other.pk}/yuk-xati/").status_code == 404


def test_the_money_lines_add_up_from_the_goods_total(
    client, admin_user, seller_user, sample_data
):
    """150 000 of goods, 50 000 already handed over, 100 000 left — every step of
    that written down, so nobody has to guess why the debt is not the total."""
    client.force_login(admin_user)
    body = client.get(f"/sales/{sample_data['sale'].pk}/yuk-xati/").content.decode()

    assert "150\xa0000" in body           # Jami — the goods
    assert "Оплачено" in body
    assert "50\xa0000" in body            # what has come off it
    assert "Ushbu yuk xati qarzi" in body
    assert "100\xa0000" in body           # what is left


def test_advance_spent_on_the_load_is_written_down(
    client, admin_user, seller_user, sample_data
):
    """Credit the client had lying with the seller and the part of it this load ate —
    the two figures they ask about when the debt looks smaller than the goods."""
    mijoz = sample_data["client"]
    Payment.objects.create(
        client=mijoz, amount=Decimal("120000"), amount_original=Decimal("120000"),
        method=Payment.Method.CASH, kind=Payment.Kind.ADVANCE_IN,
        date=timezone.localdate(), created_by=seller_user,
    )
    Payment.objects.create(
        sale=sample_data["sale"], client=mijoz,
        amount=Decimal("100000"), amount_original=Decimal("100000"),
        method=Payment.Method.CASH, kind=Payment.Kind.ADVANCE_USED,
        date=timezone.localdate(), created_by=seller_user,
    )
    client.force_login(admin_user)
    body = client.get(f"/sales/{sample_data['sale'].pk}/yuk-xati/").content.decode()

    assert "Avansdan" in body
    assert "100\xa0000" in body           # spent on this load
    assert "Остаток аванса" in body
    assert "20\xa0000" in body            # still theirs
    assert "Ushbu yuk xati qarzi" not in body   # 150 000 − 50 000 − 100 000 = nothing


def test_money_lines_stay_on_the_forms_own_rows(client, admin_user, sample_data):
    """Written on the ruled lines the pad already has — the sheet must not grow a
    block of its own underneath."""
    client.force_login(admin_user)
    body = client.get(f"/sales/{sample_data['sale'].pk}/yuk-xati/").content.decode()

    assert body.count('<td class="c-no">21</td>') == 2   # still 21 lines
    assert body.count('<td class="c-no">22</td>') == 0


def test_print_carries_the_clients_balance(
    client, admin_user, seller_user, sample_data
):
    """Old debt, this load's debt and the two added up — the figures the driver is
    asked about at the gate."""
    Sale.objects.create(
        client=sample_data["client"],
        sales_rep=seller_user,
        is_opening=True,
        opening_amount=Decimal("300000"),
    )
    client.force_login(admin_user)
    resp = client.get(f"/sales/{sample_data['sale'].pk}/yuk-xati/")

    body = resp.content.decode()
    assert "Oldingi qarz" in body
    assert "300\xa0000" in body          # the earlier balance
    assert "100\xa0000" in body          # this receipt: 150 000 sold, 50 000 paid
    assert "400\xa0000" in body          # what they owe once this load is signed for


def test_settled_receipt_with_a_clean_client_prints_no_balance(
    client, admin_user, seller_user, sample_data
):
    """Nothing owed either way — the form stays the plain waybill it was."""
    sale = sample_data["sale"]
    Payment.objects.create(
        sale=sale, amount=Decimal("100000"), amount_original=Decimal("100000"),
        method=Payment.Method.CASH, kind=Payment.Kind.DEBT,
        date=timezone.localdate(), created_by=seller_user,
    )
    client.force_login(admin_user)

    assert "Oldingi qarz" not in client.get(f"/sales/{sale.pk}/yuk-xati/").content.decode()


def test_paid_load_still_shows_the_old_balance_without_a_total(
    client, admin_user, seller_user, sample_data
):
    """This load adds nothing, so there is nothing to add up — only the old debt."""
    sale = sample_data["sale"]
    Payment.objects.create(
        sale=sale, amount=Decimal("100000"), amount_original=Decimal("100000"),
        method=Payment.Method.CASH, kind=Payment.Kind.DEBT,
        date=timezone.localdate(), created_by=seller_user,
    )
    Sale.objects.create(
        client=sample_data["client"], sales_rep=seller_user,
        is_opening=True, opening_amount=Decimal("300000"),
    )
    client.force_login(admin_user)
    body = client.get(f"/sales/{sale.pk}/yuk-xati/").content.decode()

    assert "Oldingi qarz" in body
    assert "Jami qarz" not in body


def test_seller_reads_only_the_balance_they_can_see(
    client, seller_user, admin_user, sample_data
):
    """A debt booked by another seller is not on their page, so it is not on their
    paper either."""
    Sale.objects.create(
        client=sample_data["client"], sales_rep=admin_user,
        is_opening=True, opening_amount=Decimal("300000"),
    )
    client.force_login(seller_user)
    body = client.get(f"/sales/{sample_data['sale'].pk}/yuk-xati/").content.decode()

    assert "300\xa0000" not in body
    assert "100\xa0000" in body
