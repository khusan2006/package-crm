"""Yuk xati — the sale as the paper form the goods travel with.

Pins down what the printout has to carry (client, goods, money), that it is printed
twice on one sheet so seller and client each keep a half, and that a seller cannot
print someone else's receipt.
"""

from decimal import Decimal

import pytest

from crm.models import Sale

pytestmark = pytest.mark.django_db


def test_print_page_carries_the_receipt(client, admin_user, sample_data):
    sale = sample_data["sale"]
    client.force_login(admin_user)
    resp = client.get(f"/sales/{sale.pk}/yuk-xati/")

    assert resp.status_code == 200
    body = resp.content.decode()
    assert "YUK XATI/НАКЛАДНАЯ" in body
    assert "Test mijoz" in body
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
