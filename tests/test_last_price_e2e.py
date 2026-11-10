"""The sale form's «Oxirgi narx» in a real browser: a product this mijoz bought
before opens at the price they paid last time (not the catalogue price), the figure
shows under the box, a price typed by hand stays put, and the hint puts it back."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from crm.models import Client, Product, Sale, SaleItem

pytestmark = pytest.mark.e2e


@pytest.fixture
def bought_before(db, seller_user):
    product = Product.objects.create(
        name="Stretch plyonka", sku="STR-1", cost_price=Decimal("12000"),
        price=Decimal("15000"),
    )
    regular = Client.objects.create(name="Doimiy mijoz", owner=seller_user)
    fresh = Client.objects.create(name="Yangi mijoz", owner=seller_user)
    sale = Sale.objects.create(
        client=regular, sales_rep=seller_user,
        date=timezone.localdate() - timedelta(days=3),
        debt_deadline=timezone.localdate() + timedelta(days=4),
    )
    SaleItem.objects.create(
        sale=sale, product=product, dimension=Sale.Dimension.KG,
        weight=Decimal("50"), price=Decimal("17500"), cost_price=Decimal("12000"),
    )
    return {"product": product, "regular": regular, "fresh": fresh}


def _pick(page, select_id, text):
    box = page.locator(f".combobox:has(#{select_id})")
    box.locator(".combobox-input").click()
    box.locator(".combobox-input").fill(text)
    box.locator(".combobox-option").first.click()


def test_last_price_prefills_and_can_be_changed(login, seller_user, live_server, bought_before):
    page = login(seller_user)
    page.goto(f"{live_server.url}/sales/new/")
    price = page.locator("[name=items-0-price]")
    hint = page.locator(".item-row [data-last-price-hint]").first

    # Product first, no mijoz yet: the catalogue price, no hint.
    _pick(page, "id_items-0-product", "Stretch")
    assert price.input_value() == "15 000"
    assert hint.is_hidden()

    # The regular mijoz: the auto-filled box follows them to what they paid last time.
    _pick(page, "id_client", "Doimiy")
    assert price.input_value() == "17 500"
    assert hint.is_visible()
    assert "Oxirgi narx: 17 500 so'm/kg" in hint.inner_text()

    # Typed over: it is the seller's now; the hint offers the old price back.
    price.fill("18000")
    assert price.input_value() == "18 000"
    assert "is-other" in (hint.get_attribute("class") or "")

    # Another mijoz who never bought it: the typed price stays, the hint goes.
    _pick(page, "id_client", "Yangi")
    assert price.input_value() == "18 000"
    assert hint.is_hidden()

    # Back to the regular one, and the hint puts their last price back.
    _pick(page, "id_client", "Doimiy")
    assert price.input_value() == "18 000"
    hint.click()
    assert price.input_value() == "17 500"
    assert "is-other" not in (hint.get_attribute("class") or "")
