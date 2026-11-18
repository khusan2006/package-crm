"""In a real browser: «⇅ Teskari» and «⚡ Zarur» on the route, and the «Do'kon» box in
«Mijoz qo'shish» for a client with more than one do'kon
(docs/specs/2026-11-18-route-locations.md)."""

from decimal import Decimal

import pytest
from django.utils import timezone

from crm.models import Client, ClientLocation, Load, LoadItem, LoadLine, Product, Warehouse

pytestmark = pytest.mark.e2e


@pytest.fixture
def paket(db):
    return Product.objects.create(name="Paket katta", sku="P", cost_price=1, price=Decimal("15000"))


@pytest.fixture
def load_of_three(db, admin_user, seller_user, paket):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.3"), Decimal("69.24")
    w.save()
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    for name, dlng in (("Yaqin", "0.01"), ("Orta", "0.02"), ("Uzoq", "0.03")):
        c = Client.objects.create(name=name, owner=seller_user, latitude=Decimal("41.3"),
                                  longitude=Decimal("69.24") + Decimal(dlng))
        item = LoadItem.objects.create(load=load, client=c)
        LoadLine.objects.create(item=item, product=paket, rolls=5, price=paket.price)
    return load


def stop_names(page):
    return page.locator("[data-route-stop] .route-body strong").all_inner_texts()


def open_route(page, live_server, load):
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    page.click("[data-route-build]")
    page.wait_for_selector("[data-route-stop]")
    page.wait_for_selector("[data-route-start]", state="hidden")


def test_teskari_turns_the_route_round_in_one_press(login, admin_user, live_server,
                                                    load_of_three):
    page = login(admin_user)
    open_route(page, live_server, load_of_three)
    assert stop_names(page) == ["Yaqin", "Orta", "Uzoq"]
    page.click("[data-route-reverse]")
    page.wait_for_selector("text=Tartib teskari qilindi")
    page.wait_for_selector("[data-route-stop]")
    assert stop_names(page) == ["Uzoq", "Orta", "Yaqin"]


def test_zarur_puts_a_stop_first(login, admin_user, live_server, load_of_three):
    page = login(admin_user)
    open_route(page, live_server, load_of_three)
    page.locator("[data-route-stop]").nth(1).locator('button[name="urgent"]').click()
    page.wait_for_selector("text=birinchi bo'lib boriladi")
    page.wait_for_selector("[data-route-stop]")
    assert stop_names(page)[0] == "Orta"
    assert page.locator("[data-route-stop]").first.locator(".route-urgent-badge").is_visible()


def test_zarur_keeps_the_page_on_the_stop_and_the_message_in_the_log(login, admin_user,
                                                                      live_server, load_of_three):
    """The page comes back where it was — on the stop ⚡ was pressed on — not at its top,
    and what the server said can be read again under «Oxirgi xabarlar»."""
    page = login(admin_user)
    page.set_viewport_size({"width": 1200, "height": 520})
    open_route(page, live_server, load_of_three)
    last = page.locator("[data-route-stop]").nth(2)
    last.scroll_into_view_if_needed()
    last.locator('button[name="urgent"]').click()
    page.wait_for_selector("text=birinchi bo'lib boriladi")
    page.wait_for_selector("[data-route-start]", state="hidden")
    page.wait_for_function("""() => {
        const first = document.querySelector('[data-route-stop]');
        const r = first && first.getBoundingClientRect();
        return window.scrollY > 0 && r && r.top >= 0 && r.bottom <= window.innerHeight;
    }""")
    assert stop_names(page)[0] == "Uzoq"
    page.click("[data-msglog-toggle]")
    panel = page.locator("[data-msglog-panel]")
    assert panel.is_visible()
    assert "birinchi bo'lib boriladi" in panel.inner_text()


def test_the_dokon_box_shows_for_a_client_with_shops(login, admin_user, seller_user, live_server,
                                                     db, paket):
    Warehouse.load()
    one = Client.objects.create(name="Bitta joyli", owner=seller_user)
    two = Client.objects.create(name="Ikki do'konli", owner=seller_user, latitude=Decimal("41.3"),
                                longitude=Decimal("69.25"), location_note="Chorsu")
    bozor = ClientLocation.objects.create(client=two, note="Bozor", latitude=Decimal("41.31"),
                                          longitude=Decimal("69.26"))
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/")
    page.click("[data-open-add]")
    row = page.locator("#add-client [data-new-row]").first
    row.wait_for()
    place = row.locator("select[name=new_place]")
    row.locator(".combobox-input").first.fill("Ikki")
    page.locator(".combobox-option:visible", has_text="Ikki do'konli").first.click()
    assert place.is_visible()
    assert place.locator("option").all_inner_texts() == ["Asosiy — Chorsu", "Bozor"]
    place.select_option(str(bozor.pk))
    row.locator(".combobox-input").first.fill("Bitta")
    page.locator(".combobox-option:visible", has_text="Bitta joyli").first.click()
    assert not place.is_visible()
    assert one.pk  # a client with a single place: no box
