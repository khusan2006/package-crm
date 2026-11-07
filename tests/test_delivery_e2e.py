"""The Yetkazish page's hand corrections in a real browser: ↑ / ↓ reorder a truck's
stops, ⇄ moves one to the other truck, and Saqlash saves what is on screen."""

from decimal import Decimal

import pytest
from django.utils import timezone

from crm.models import Client, Product, RouteStop, Sale, SaleItem, Truck, Warehouse

pytestmark = pytest.mark.e2e


@pytest.fixture
def day_with_two_trucks(db, seller_user):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.3"), Decimal("69.24")
    w.save()
    Truck.objects.create(name="Birinchi", capacity_kg=1000)
    Truck.objects.create(name="Ikkinchi", capacity_kg=1000)
    product = Product.objects.create(name="Paket", sku="P", cost_price=1, price=2)
    # Two clients west of the warehouse, two east: one side per truck.
    for name, dlng in (("G1", "-0.05"), ("G2", "-0.04"), ("S1", "0.04"), ("S2", "0.05")):
        c = Client.objects.create(name=name, owner=seller_user, latitude=Decimal("41.3"),
                                  longitude=Decimal("69.24") + Decimal(dlng))
        sale = Sale.objects.create(client=c, sales_rep=seller_user, date=timezone.localdate())
        SaleItem.objects.create(sale=sale, product=product, weight=Decimal("450"),
                                price=2, cost_price=1, fulfilled_kg=Decimal("450"),
                                fulfilled_at=timezone.localdate())


def cards(page):
    return page.locator("[data-route-truck]")


def stop_names(card):
    return card.locator("[data-route-stop] .route-body strong").all_inner_texts()


def test_reorder_move_and_save(login, admin_user, live_server, day_with_two_trucks):
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/")
    first, second = cards(page).nth(0), cards(page).nth(1)
    before = stop_names(first)
    assert len(before) == 2 and len(stop_names(second)) == 2

    # ↓ on the first stop swaps the two, and the numbers follow.
    first.locator("[data-route-stop]").nth(0).locator('[data-move="down"]').click()
    assert stop_names(first) == before[::-1]
    assert first.locator("[data-route-n]").all_inner_texts() == ["1", "2"]
    assert page.locator("[data-route-dirty]").is_visible()

    # ⇄ moves it to the other truck: 3 × 450 kg is over its 1 000 kg.
    moved = stop_names(first)[0]
    first.locator("[data-route-stop]").nth(0).locator("[data-move-to]:visible").click()
    assert stop_names(first) == [before[0]]
    assert stop_names(second)[-1] == moved
    assert second.locator("[data-route-kg]").inner_text().replace(" ", "") == "1350"
    assert second.locator("[data-route-over]").is_visible()
    assert not first.locator("[data-route-over]").is_visible()

    page.locator('button[form="route-form"]').click()
    page.wait_for_selector("text=Yetkazish rejasi saqlandi")
    second_truck = Truck.objects.get(pk=int(second.get_attribute("data-route-truck")))
    saved = list(RouteStop.objects.filter(truck=second_truck).values_list("client__name", flat=True))
    assert len(saved) == 3 and saved[-1] == moved
