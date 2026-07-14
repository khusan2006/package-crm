"""End-to-end happy path: buy material -> produce -> transfer to seller, driven
through a real browser against Django's live server (Playwright).

Mirrors the interaction pattern in tests/test_e2e_smoke.py for the
`_searchable_select` combobox fields: the native <select> is hidden behind the
combobox JS enhancement, so options are picked via the visible
`.combobox-input` / `.combobox-option` elements rather than `select_option`.
"""

import pytest
from decimal import Decimal

from crm.models import Product
from manufacturing.models import RawMaterial

pytestmark = pytest.mark.e2e


def _pick_combobox(page, field_id, query):
    """Open the searchable combobox for `#id_<field_id>` and choose the option
    whose text contains `query`. Matches the pattern used in test_e2e_smoke.py."""
    box = page.locator(f".combobox:has(#id_{field_id})")
    box.locator(".combobox-input").click()
    box.locator(".combobox-input").fill(query)
    box.locator(".combobox-option", has_text=query).first.click()


def test_buy_produce_transfer_flow(page, live_server, login, admin_user, seller_user):
    material = RawMaterial.objects.create(name="Karton", sku="E2E-M1")
    product = Product.objects.create(name="Paket E2E", sku="E2E-P1", price=Decimal("20000"))

    login(admin_user)

    # Buy material.
    page.goto(f"{live_server.url}/ishlab-chiqarish/xaridlar/yangi/")
    _pick_combobox(page, "material", material.name)
    page.fill('input[name="date"]', "2026-07-01")
    page.fill('input[name="quantity_kg"]', "100")
    page.fill('input[name="price_per_kg"]', "1000")
    page.get_by_role("button", name="Saqlash").click()
    page.wait_for_url(f"{live_server.url}/ishlab-chiqarish/xaridlar/")
    material.refresh_from_db()
    assert material.avg_cost == Decimal("1000.00")

    # Produce.
    page.goto(f"{live_server.url}/ishlab-chiqarish/ishlab-chiqarish/yangi/")
    _pick_combobox(page, "product", product.name)
    page.fill('input[name="output_kg"]', "40")
    _pick_combobox(page, "items-0-material", material.name)
    page.fill('input[name="items-0-quantity_kg"]', "50")
    page.get_by_role("button", name="Saqlash").click()
    page.wait_for_url(f"{live_server.url}/ishlab-chiqarish/ishlab-chiqarish/")
    product.refresh_from_db()
    assert product.cost_price == Decimal("1250.00")

    # Transfer to seller.
    page.goto(f"{live_server.url}/ishlab-chiqarish/topshiruvlar/yangi/")
    _pick_combobox(page, "product", product.name)
    _pick_combobox(page, "seller", seller_user.get_full_name() or seller_user.username)
    page.fill('input[name="date"]', "2026-07-02")
    page.fill('input[name="quantity_kg"]', "15")
    page.get_by_role("button", name="Saqlash").click()
    page.wait_for_url(f"{live_server.url}/ishlab-chiqarish/topshiruvlar/")

    from manufacturing.queries import seller_ombor, sklad_stock
    assert sklad_stock(product) == Decimal("25.000")
    assert seller_ombor(seller_user, product) == Decimal("15.000")
