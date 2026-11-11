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


#: «Qo'shish» — puts the new clients in (everything else saves by itself).
ADD = "[data-load-items] button[name=add]"


def open_row(page, item):
    """Open a client's row on the day page: its products, kg and ✓ show."""
    page.click(f"#stop-{item.pk} > summary .lane-who")
    page.locator(f"#stop-{item.pk} .lane-editor").wait_for()


def open_add(page):
    """«Mijoz qo'shish» — the round button; there is no bar for it on the page."""
    add = page.locator("#add-client")
    if not add.evaluate("el => el.open"):
        page.click("[data-open-add]")
        add.locator("[data-new-row]").first.wait_for()


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
    page.click(ADD)
    page.wait_for_selector("text=Qo'shildi")
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
    open_row(page, item)
    page.fill(f'[name="kg_{line.pk}"]', "123,5")
    page.click(f'button[name="confirm"][value="{item.pk}"]')
    page.wait_for_selector("text=sotuvga aylandi")
    sale = Sale.objects.get()
    assert sale.items.get().weight == Decimal("123.5")
    assert page.locator(f"#stop-{item.pk} >> text=Sotuv #{sale.pk}").is_visible()
    assert page.locator(f'[name="kg_{line.pk}"]').count() == 0  # locked



def test_kg_box_adds_up_like_a_calculator(login, admin_user, seller_user, live_server, db, paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    item = stop(load, Client.objects.create(name="Anvar", owner=seller_user), paket, rolls=3)
    line = item.lines.get()
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    open_row(page, item)
    box = page.locator(f'[name="kg_{line.pk}"]')
    box.fill("50+48,5+51")
    assert page.locator(f'#stop-{item.pk} .kg-sum').inner_text() == "= 149,5 kg"
    box.press("Enter")
    assert box.input_value() == "149.5"
    assert page.locator(f'#stop-{item.pk} .kg-sum').count() == 0
    box.fill("10+20")
    box.press("Tab")  # leaving the box settles it — and saves it, no button
    assert box.input_value() == "30"
    page.wait_for_selector(f"#stop-{item.pk} [data-autosave-note]:has-text('Saqlandi')")
    line.refresh_from_db()
    assert line.kg == Decimal("30")

def test_a_product_is_added_to_a_stop(login, admin_user, seller_user, live_server, db, paket):
    Product.objects.create(name="Plyonka", sku="P2", cost_price=1, price=Decimal("26000"))
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    item = stop(load, Client.objects.create(name="Anvar", owner=seller_user), paket)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    open_row(page, item)
    page.click(f'[data-add-line="{item.pk}"]')
    added = page.locator("[data-add-line-row]").first
    pick(page, added, 0, "Plyonka")
    added.locator("input[name$=_rolls]").type("3")
    # The Narx box filled itself with the catalogue price.
    assert added.locator("input[name$=_price]").input_value().replace(" ", "").replace("\u00a0", "") == "26000"
    added.locator("input[name$=_rolls]").press("Tab")  # product and rulon in: it goes in
    page.wait_for_selector("text=Saqlandi")
    assert page.locator(f"#stop-{item.pk}").evaluate("el => el.open")  # the row stays open
    assert sorted(item.lines.values_list("product__name", "rolls")) == [("Paket katta", 5), ("Plyonka", 3)]


def test_enter_in_the_picker_never_submits(login, admin_user, seller_user, live_server, db, paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    stop(load, Client.objects.create(name="Bektemir savdo", owner=seller_user), paket)
    Client.objects.create(name="Chilonzor plast", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    open_add(page)
    picker = page.locator("[data-new-row] .combobox-input").first
    picker.fill("chilon")
    picker.press("Enter")                     # takes the match, moves on, no submit
    assert page.locator("[data-new-row] select[name=new_client]").first.input_value() != ""
    picker.focus()
    picker.press("Escape")
    picker.press("Enter")
    page.wait_for_timeout(300)
    assert load.items.count() == 1            # nobody was added behind the user's back
    assert page.url.endswith(f"/yetkazish/{load.pk}/")


def test_a_client_not_yet_added_is_noted(login, admin_user, seller_user, live_server, db, paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    stop(load, Client.objects.create(name="A", owner=seller_user), paket, rolls=3)
    Client.objects.create(name="Botir", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    open_add(page)
    note = page.locator("[data-items-dirty]")
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
    page.click(ADD)
    page.wait_for_selector("text=Qo'shildi")
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
    page.click(ADD)
    page.wait_for_selector("text=Qo'shildi")
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


def test_a_client_moves_to_tomorrow_from_its_own_icon(login, admin_user, seller_user,
                                                     live_server, db, paket):
    from datetime import timedelta
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    a = stop(load, Client.objects.create(name="Anvar", owner=seller_user), paket)
    b = stop(load, Client.objects.create(name="Botir", owner=seller_user), paket)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    open_row(page, a)
    page.click(f'#stop-{a.pk} a[href*="boshqa-kunga"]')
    dialog = page.locator("[data-move-dialog]")
    dialog.wait_for()
    submit = dialog.locator("[data-move-submit]")
    assert submit.inner_text() == "1 ta mijozni o'tkazish"
    dialog.locator("[data-move-all]").click()
    assert submit.inner_text() == "2 ta mijozni o'tkazish"
    dialog.locator(f'input[name=move][value="{b.pk}"]').uncheck()
    dialog.locator(f'input[name=move][value="{a.pk}"]').uncheck()
    assert submit.is_disabled()
    dialog.locator(f'input[name=move][value="{a.pk}"]').check()
    submit.click()
    page.wait_for_selector("text=1 ta mijoz Yuk")
    a.refresh_from_db()
    assert a.load != load and a.load.date == load.date + timedelta(days=1)
    assert list(load.items.values_list("client__name", flat=True)) == ["Botir"]


def test_one_product_of_a_client_moves_to_tomorrow(login, admin_user, seller_user, live_server,
                                                   db, paket):
    film = Product.objects.create(name="Plyonka", sku="P2", cost_price=1, price=Decimal("26000"))
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    a = stop(load, Client.objects.create(name="Anvar", owner=seller_user), paket)
    LoadLine.objects.create(item=a, product=film, rolls=2, price=film.price)
    bag = a.lines.get(product=paket)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    open_row(page, a)
    page.click(f'#stop-{a.pk} a[href*="boshqa-kunga"]')
    dialog = page.locator("[data-move-dialog]")
    dialog.wait_for()
    whole = dialog.locator("[data-move-whole]")
    assert whole.is_checked()
    dialog.locator(f'[data-move-line][value="{bag.pk}"]').uncheck()   # the bags stay today
    assert whole.evaluate("el => el.indeterminate && !el.checked")
    submit = dialog.locator("[data-move-submit]")
    assert submit.inner_text() == "1 ta mijozni o'tkazish (1 tasi qisman)"
    submit.click()
    # The toast, not the button — its label says "(1 tasi qisman)" too.
    page.wait_for_selector(".toast-msg:has-text('1 tasi qisman')")
    assert list(a.lines.values_list("product__name", flat=True)) == ["Paket katta"]
    moved = LoadLine.objects.get(product=film)
    assert moved.item.load != load and moved.item.client == a.client


def test_another_product_starts_as_a_copy_of_the_last(login, admin_user, seller_user,
                                                      live_server, db):
    oq = Product.objects.create(name="ОҚ плёнка", sku="oq", cost_price=1, price=Decimal("22000"),
                                has_size=True, has_micron=True)
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    saved = stop(load, Client.objects.create(name="Anvar", owner=seller_user), oq, rolls=2)
    saved.lines.update(size="1,5м", micron="05", price=Decimal("23000"))
    Client.objects.create(name="Bobur", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    open_row(page, saved)

    # A saved stop: the copy has the saved line's product, metr, mikron, rulon and price,
    # and the cursor waits in Metr.
    page.click(f'[data-add-line="{saved.pk}"]')
    copy = page.locator(f'[data-add-lines][data-item="{saved.pk}"] [data-add-line-row]').first
    assert copy.locator(".combobox-input").input_value() == "ОҚ плёнка"
    size = copy.locator("input[name$=_size]")
    assert size.input_value() == "1,5м" and size.evaluate("el => el === document.activeElement")
    assert copy.locator("input[name$=_micron]").input_value() == "05"
    assert copy.locator("input[name$=_rolls]").input_value() == "2"
    size.fill("2м")
    copy.locator("input[name$=_micron]").fill("08")

    # A new client: «+ Mahsulot» copies the row above it.
    open_add(page)
    block = page.locator("[data-new-row]").first
    first = block.locator(".new-stop-first")
    pick(page, block, 0, "Bobur")
    pick(page, first, 0, "ОҚ плёнка")
    first.locator("[name=new_size]").fill("1,5м")
    first.locator("[name=new_micron]").fill("06")
    first.locator("[name=new_rolls]").fill("3")
    block.locator("[data-add-new-line]").click()
    extra = block.locator("[data-add-line-row]").first
    assert extra.locator("input[name$=_size]").input_value() == "1,5м"
    extra.locator("input[name$=_micron]").fill("08")

    page.click(ADD)
    page.wait_for_selector("text=Qo'shildi")
    assert sorted(saved.lines.values_list("size", "micron", "rolls", "price")) == [
        ("1,5м", "05", 2, Decimal("23000")), ("2м", "08", 2, Decimal("23000"))]
    bobur = load.items.get(client__name="Bobur")
    assert sorted(bobur.lines.values_list("size", "micron", "rolls")) == [
        ("1,5м", "06", 3), ("1,5м", "08", 3)]


def test_a_late_start_is_backdated_and_the_route_shows_arrival_times(login, admin_user,
                                                                     live_server, load_of_three):
    import os
    from datetime import timedelta
    load = load_of_three
    page = login(admin_user)
    page.set_viewport_size({"width": 1400, "height": 900})
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    when = timezone.localtime() - timedelta(minutes=45)
    page.fill("[data-load-start] input[name=started_at]", when.strftime("%Y-%m-%dT%H:%M"))
    shots = os.environ.get("SHOT_DIR")
    if shots:
        page.locator(".load-head").screenshot(path=f"{shots}/eta-1-start.png")
    page.click("[data-load-start] button[type=submit]")
    page.wait_for_selector("text=Yuk jo'natildi")
    load.refresh_from_db()
    assert timezone.localtime(load.started_at).strftime("%H:%M") == when.strftime("%H:%M")
    page.wait_for_selector("[data-route-stop]")  # the route opens by itself
    etas = page.locator("[data-route-stop] [data-route-eta]").all_inner_texts()
    assert len(etas) == 3 and all(t.startswith("≈ ") for t in etas)
    assert etas == sorted(etas)
    if shots:
        page.locator(".load-head").screenshot(path=f"{shots}/eta-2-started.png")
        page.locator(".route-summary").screenshot(path=f"{shots}/eta-3-summary.png")
        page.locator(".route-card").first.screenshot(path=f"{shots}/eta-4-stops.png")
    # A move makes the times stale: they step aside until the order is saved.
    page.locator("[data-route-stop] [data-move=down]").first.click()
    assert page.locator("[data-route-stop] [data-route-eta]:visible").count() == 0


def test_cancel_empties_and_closes_the_add_form(login, admin_user, seller_user, live_server, db,
                                                paket):
    load = Load.objects.create(date=timezone.localdate(), created_by=admin_user)
    stop(load, Client.objects.create(name="A", owner=seller_user), paket)
    Client.objects.create(name="Botir", owner=seller_user)
    page = login(admin_user)
    page.goto(f"{live_server.url}/yetkazish/{load.pk}/")
    assert not page.locator("#add-client").is_visible()   # no bar on the page
    open_add(page)
    pick(page, page.locator("[data-new-row]").first, 0, "Botir")
    page.click("[data-add-cancel]")
    assert not page.locator("#add-client").is_visible()
    open_add(page)
    assert page.locator("[data-new-row] select[name=new_client]").first.input_value() == ""
    assert load.items.count() == 1
