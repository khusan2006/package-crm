"""A load in a real browser: stops added by keyboard, a product added to a stop,
kg typed then ✓ to a sale, ↑ / ↓ on the route, and the safety nets — Enter never
submits, the unsaved note follows real changes."""

from decimal import Decimal

import pytest
from django.utils import timezone

from crm.models import Client, Load, LoadItem, LoadLine, Product, Sale, Warehouse

pytestmark = pytest.mark.e2e


@pytest.fixture
def paket(db):
    return Product.objects.create(name="Paket katta", sku="P", cost_price=1, price=Decimal("15000"))


def stop(load, client, product, rolls=5, kg=None):
    item = LoadItem.objects.create(load=load, client=client)
    LoadLine.objects.create(item=item, product=product, rolls=rolls, price=product.price, kg=kg)
    return item


@pytest.fixture
def load_of_three(db, admin_user, seller_user, paket):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.3"), Decimal("69.24")
    w.save()
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    for name, dlng in (("Yaqin", "0.01"), ("Orta", "0.02"), ("Uzoq", "0.03")):
        c = Client.objects.create(name=name, owner=seller_user, latitude=Decimal("41.3"),
                                  longitude=Decimal("69.24") + Decimal(dlng))
        stop(load, c, paket)
    return load


def stop_names(page):
    return page.locator("[data-route-stop] .route-body strong").all_inner_texts()


def pick(page, row, nth, text):
    """Type into the nth picker of a row and choose the matching option."""
    row.locator(".combobox-input").nth(nth).fill(text)
    page.locator(".combobox-option:visible", has_text=text).first.click()


def test_reorder_and_save(login, admin_user, live_server, load_of_three):
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load_of_three.pk}/")
    assert page.locator("[data-route-stop]").count() == 0
    page.click("[data-route-build]")
    page.wait_for_selector("[data-route-stop]")
    page.wait_for_selector("[data-route-start]", state="hidden")
    assert stop_names(page) == ["Yaqin", "Orta", "Uzoq"]

    page.locator("[data-route-stop]").nth(0).locator('[data-move="down"]').click()
    assert stop_names(page) == ["Orta", "Yaqin", "Uzoq"]
    assert not page.locator("[data-route-share]").is_visible()
    # The way back to the suggestion appears once there's a move to undo.
    page.click("[data-route-suggest]")
    page.wait_for_function("document.querySelector('[data-route-stop] .route-body strong').innerText === 'Yaqin'")
    page.locator("[data-route-stop]").nth(0).locator('[data-move="down"]').click()

    page.locator('button[form="route-form"]').click()
    page.wait_for_selector("text=Tartib saqlandi")
    page.wait_for_selector("[data-route-stop]")
    assert stop_names(page) == ["Orta", "Yaqin", "Uzoq"]


def test_add_stops_by_keyboard_and_save(login, admin_user, seller_user, live_server, db, paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    for name in ("Anvar savdo", "Botir plast"):
        Client.objects.create(name=name, owner=seller_user, address="Chilonzor")
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")

    row = page.locator("[data-new-row]").first
    pick(page, row, 0, "Botir plast")          # client → focus moves to its product
    assert row.locator(".combobox-input").nth(1).evaluate("el => el === document.activeElement")
    pick(page, row, 1, "Paket katta")          # product → focus moves to its rulon
    rolls = row.locator("[name=new_rolls]")
    assert rolls.evaluate("el => el === document.activeElement")
    rolls.type("12")
    rolls.press("Enter")                       # a new row, not a submit
    assert page.locator("[data-new-row]").count() == 2

    second = page.locator("[data-new-row]").nth(1)
    pick(page, second, 0, "Anvar savdo")
    second.locator("select[name=new_purpose]").select_option("collect")
    page.click("[data-load-items] .items-actions button[type=submit]")
    page.wait_for_selector("text=Saqlandi")
    stops = {i.client.name: i for i in load.items.all()}
    assert stops["Botir plast"].lines.get().rolls == 12
    assert stops["Anvar savdo"].purpose == "collect" and not stops["Anvar savdo"].lines.exists()


def test_type_kg_and_tick_turns_the_stop_into_a_sale(login, admin_user, seller_user, live_server,
                                                     db, paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    item = stop(load, Client.objects.create(name="Anvar", owner=seller_user), paket, rolls=6)
    line = item.lines.get()
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    page.fill(f'[name="kg_{line.pk}"]', "123,5")
    page.click(f'button[name="confirm"][value="{item.pk}"]')
    page.wait_for_selector("text=sotuvga aylandi")
    sale = Sale.objects.get()
    assert sale.items.get().weight == Decimal("123.5")
    assert page.locator(f"#stop-{item.pk} >> text=Sotuv #{sale.pk}").is_visible()
    assert page.locator(f'[name="kg_{line.pk}"]').count() == 0  # locked


def test_a_product_is_added_to_a_stop(login, admin_user, seller_user, live_server, db, paket):
    Product.objects.create(name="Plyonka", sku="P2", cost_price=1, price=Decimal("26000"))
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    item = stop(load, Client.objects.create(name="Anvar", owner=seller_user), paket)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    page.click(f'[data-add-line="{item.pk}"]')
    added = page.locator("[data-add-line-row]").first
    pick(page, added, 0, "Plyonka")
    added.locator("input[name$=_rolls]").type("3")
    # The Narx box filled itself with the catalogue price.
    assert added.locator("input[name$=_price]").input_value().replace(" ", "").replace("\u00a0", "") == "26000"
    assert page.locator("[data-items-dirty]").is_visible()
    page.click("[data-load-items] .items-actions button[type=submit]")
    page.wait_for_selector("text=Saqlandi")
    assert sorted(item.lines.values_list("product__name", "rolls")) == [("Paket katta", 5), ("Plyonka", 3)]


def test_enter_in_the_picker_never_submits(login, admin_user, seller_user, live_server, db, paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    item = stop(load, Client.objects.create(name="Bektemir savdo", owner=seller_user), paket,
                kg=Decimal("120"))
    line = item.lines.get()
    Client.objects.create(name="Chilonzor plast", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    page.fill(f'[name="kg_{line.pk}"]', "121")
    picker = page.locator("[data-new-row] .combobox-input").first
    picker.fill("chilon")
    picker.press("Enter")                     # takes the match, moves on, no submit
    assert page.locator("[data-new-row] select[name=new_client]").first.input_value() != ""
    picker.focus()
    picker.press("Escape")
    picker.press("Enter")
    page.wait_for_timeout(300)
    line.refresh_from_db()
    assert line.kg == Decimal("120")          # the 121 was never saved behind the user's back
    assert page.url.endswith(f"/yetkazish/{load.pk}/")


def test_undoing_a_change_clears_the_unsaved_note(login, admin_user, seller_user, live_server, db, paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    item = stop(load, Client.objects.create(name="A", owner=seller_user), paket, rolls=3)
    line = item.lines.get()
    Client.objects.create(name="Botir", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    note = page.locator("[data-items-dirty]")
    assert not note.is_visible()
    page.fill(f'[name="rolls_{line.pk}"]', "4")
    assert note.is_visible()
    page.fill(f'[name="rolls_{line.pk}"]', "3")
    assert not note.is_visible()
    pick(page, page.locator("[data-new-row]").first, 0, "Botir")
    assert note.is_visible()
    page.click("[data-new-row] [data-new-row-remove]")  # the only new row: emptied
    assert not note.is_visible()


def test_the_first_stop_cannot_move_up(login, admin_user, live_server, load_of_three):
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load_of_three.pk}/")
    page.click("[data-route-build]")
    page.wait_for_selector("[data-route-stop]")
    stops = page.locator("[data-route-stop]")
    assert stops.nth(0).locator('[data-move="up"]').is_disabled()
    assert stops.nth(2).locator('[data-move="down"]').is_disabled()
    stops.nth(0).locator('[data-move="down"]').click()
    assert stops.nth(0).locator('[data-move="up"]').is_disabled()
    assert not stops.nth(1).locator('[data-move="up"]').is_disabled()
    assert page.locator("[data-route-status]").inner_text() == "Marshrut tayyor."


def test_picking_a_product_fills_its_price_and_a_typed_price_stays(login, admin_user, seller_user,
                                                                   live_server, db, paket):
    Product.objects.create(name="Plyonka", sku="P2", cost_price=1, price=Decimal("26000"))
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    Client.objects.create(name="Anvar", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    row = page.locator("[data-new-row]").first
    price = row.locator("[name=new_price]")
    pick(page, row, 0, "Anvar")
    pick(page, row, 1, "Paket katta")
    assert price.input_value().replace(" ", "").replace(" ", "") == "15000"
    pick(page, row, 1, "Plyonka")            # an automatic price follows the product
    assert price.input_value().replace(" ", "").replace(" ", "") == "26000"
    price.fill("24500")                      # typed by hand: stays
    pick(page, row, 1, "Paket katta")
    assert price.input_value().replace(" ", "").replace(" ", "") == "24500"
    row.locator("[name=new_rolls]").fill("3")
    page.click("[data-load-items] .items-actions button[type=submit]")
    page.wait_for_selector("text=Saqlandi")
    line = LoadLine.objects.get()
    assert (line.product.name, line.price) == ("Paket katta", Decimal("24500"))


def test_a_new_client_gets_a_second_product_with_metr_and_mikron(login, admin_user, seller_user,
                                                                 live_server, db, paket):
    Product.objects.create(name="ОҚ плёнка", sku="oq", cost_price=1, price=Decimal("22000"),
                           has_size=True, has_micron=True)
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    Client.objects.create(name="Anvar", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    block = page.locator("[data-new-row]").first
    first = block.locator(".new-stop-first")
    pick(page, block, 0, "Anvar")
    pick(page, first, 0, "Paket katta")
    # A bag has no metr / mikron: greyed out, and focus skips to the rulon.
    assert first.locator("[name=new_size]").evaluate("el => el.readOnly")
    assert first.locator("[name=new_rolls]").evaluate("el => el === document.activeElement")
    first.locator("[name=new_rolls]").fill("4")

    block.locator("[data-add-new-line]").click()
    extra = block.locator("[data-add-line-row]").first
    pick(page, extra, 0, "ОҚ плёнка")
    size = extra.locator("input[name$=_size]")
    assert not size.evaluate("el => el.readOnly")
    assert size.evaluate("el => el === document.activeElement")  # a film: on to its metr
    size.fill("1,5м")
    extra.locator("input[name$=_micron]").fill("05")
    extra.locator("input[name$=_rolls]").fill("2")
    page.click("[data-load-items] .items-actions button[type=submit]")
    page.wait_for_selector("text=Saqlandi")
    item = load.items.get()
    assert sorted(item.lines.values_list("product__name", "size", "micron", "rolls")) == [
        ("Paket katta", "", "", 4), ("ОҚ плёнка", "1,5м", "05", 2)]


def test_a_stop_moves_to_the_other_truck(login, admin_user, seller_user, live_server, db, paket):
    from crm.models import Truck
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.3"), Decimal("69.24")
    w.save()
    Truck.objects.create(name="Isuzu", capacity_kg=5000)
    Truck.objects.create(name="Labo", capacity_kg=5000)
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user, strategy="fast")
    for name, dlng in (("G1", "-0.05"), ("G2", "-0.04"), ("S1", "0.04"), ("S2", "0.05")):
        c = Client.objects.create(name=name, owner=seller_user, latitude=Decimal("41.3"),
                                  longitude=Decimal("69.24") + Decimal(dlng))
        stop(load, c, paket, kg=Decimal("100"))
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    page.click("[data-route-build]")
    page.wait_for_selector("[data-route-stop]")
    cards = page.locator("[data-route-truck]")
    moved = cards.nth(0).locator("[data-route-stop]").nth(1)
    name = moved.locator(".route-body strong").inner_text()
    moved.locator("[data-move-truck]").click()
    assert cards.nth(0).locator("[data-route-stop]").count() == 1
    assert cards.nth(1).locator("[data-route-stop]").count() == 3
    # Even a truck's last stop can go: the truck is then shown as not going.
    cards.nth(0).locator("[data-move-truck]").click()
    assert cards.nth(0).locator("[data-route-empty]").is_visible()
    cards.nth(1).locator("[data-route-stop]").last.locator("[data-move-truck]").click()
    assert not cards.nth(0).locator("[data-route-empty]").is_visible()
    assert not page.locator("[data-route-share]").first.is_visible()
    page.locator('button[form="route-form"]').click()
    page.wait_for_selector("text=Tartib saqlandi")
    page.wait_for_selector("[data-route-stop]")
    second = page.locator("[data-route-truck]").nth(1).locator(".route-body strong").all_inner_texts()
    assert name in second and len(second) == 3
