"""A load's route in a real browser: ↑ / ↓ reorder the stops, the driver's message
steps aside until saved, and Saqlash saves what is on screen."""

from decimal import Decimal

import pytest
from django.utils import timezone

from crm.models import Client, Load, LoadItem, Warehouse

pytestmark = pytest.mark.e2e


@pytest.fixture
def load_of_three(db, admin_user, seller_user):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.3"), Decimal("69.24")
    w.save()
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    for name, dlng in (("Yaqin", "0.01"), ("Orta", "0.02"), ("Uzoq", "0.03")):
        c = Client.objects.create(name=name, owner=seller_user, latitude=Decimal("41.3"),
                                  longitude=Decimal("69.24") + Decimal(dlng))
        LoadItem.objects.create(load=load, client=c, rolls=5)
    return load


def stop_names(page):
    return page.locator("[data-route-stop] .route-body strong").all_inner_texts()


def test_reorder_and_save(login, admin_user, live_server, load_of_three):
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load_of_three.pk}/")
    assert stop_names(page) == ["Yaqin", "Orta", "Uzoq"]
    assert page.locator("[data-route-share]").is_visible()

    page.locator("[data-route-stop]").nth(0).locator('[data-move="down"]').click()
    assert stop_names(page) == ["Orta", "Yaqin", "Uzoq"]
    assert page.locator("[data-route-n]").all_inner_texts() == ["1", "2", "3"]
    assert page.locator("[data-route-dirty]").is_visible()
    assert not page.locator("[data-route-share]").is_visible()
    assert page.locator("[data-route-share-stale]").is_visible()

    page.locator('button[form="route-form"]').click()
    page.wait_for_selector("text=Tartib saqlandi")
    assert stop_names(page) == ["Orta", "Yaqin", "Uzoq"]
    saved = list(load_of_three.items.order_by("position").values_list("client__name", flat=True))
    assert saved == ["Orta", "Yaqin", "Uzoq"]


def test_pick_clients_by_keyboard_and_add_them(login, admin_user, seller_user, live_server, db):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    for name in ("Anvar savdo", "Botir paket", "Davron plast"):
        Client.objects.create(name=name, owner=seller_user, address="Chilonzor")
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/mijoz/")
    search = page.locator("[data-pick-search]")
    assert search.evaluate("el => el === document.activeElement")  # ready to type

    # Type a name, Enter jumps to that client's rulon box, Enter goes back to search.
    search.fill("botir")
    assert page.locator("[data-pick-row]:visible").count() == 1
    search.press("Enter")
    page.keyboard.type("12")
    page.keyboard.press("Enter")
    assert search.evaluate("el => el === document.activeElement")

    search.fill("davron")
    search.press("ArrowDown")
    page.keyboard.type("3")
    assert page.locator("[data-pick-count]").inner_text() == "2"
    assert page.locator("[data-pick-rolls-total]").inner_text() == "15"

    # Clearing the search shows everyone again; typed numbers stay.
    search.fill("")
    assert page.locator("[data-pick-row]:visible").count() == 3
    page.check("[data-pick-chosen]")
    assert page.locator("[data-pick-row]:visible").count() == 2

    page.click("[data-pick-submit]")
    page.wait_for_selector("text=2 ta mijoz yukka qo'shildi")
    assert sorted(load.items.values_list("client__name", "rolls")) == [
        ("Botir paket", 12), ("Davron plast", 3)]
