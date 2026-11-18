"""Yetkazish: a client with several do'konlar, «⇅ Teskari», «⚡ Zarur» and «$ Yo'l-yo'lakay»
(docs/specs/2026-11-18-route-locations.md).

A client may have do'konlar besides the main joylashuv, each with a note; a stop names
the one the truck goes to. The driver may start from the far end (Teskari, one press);
a client who can't wait goes first and the rest of the truck goes on from there
(Zarur); debtors the route passes close by are shown with «$» and added with one press.
The pure route maths is at the end."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.utils import timezone

from crm import routing
from crm.models import (
    AuditLog, Client, ClientLocation, Load, LoadItem, LoadLine, Product, Sale, SaleItem, Warehouse,
)

TODAY = timezone.localdate
DELIVER, COLLECT = "deliver", "collect"
XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.fixture
def warehouse(db):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.300000"), Decimal("69.240000")
    w.save()
    return w


@pytest.fixture
def paket(db):
    return Product.objects.create(name="Paket", sku="P-1", cost_price=Decimal("10000"),
                                  price=Decimal("15000"))


@pytest.fixture
def load(db, admin_user):
    return Load.objects.create(date=TODAY(), created_by=admin_user)


def make_client(owner, name, dlng=None, dlat=0.0):
    loc = {} if dlng is None else {
        "latitude": Decimal("41.300000") + Decimal(str(dlat)),
        "longitude": Decimal("69.240000") + Decimal(str(dlng))}
    return Client.objects.create(name=name, owner=owner, address=f"{name} ko'chasi", **loc)


def shop(client, note, dlng, dlat=0.0):
    return ClientLocation.objects.create(
        client=client, note=note, latitude=Decimal("41.300000") + Decimal(str(dlat)),
        longitude=Decimal("69.240000") + Decimal(str(dlng)))


def stop(load, client, *lines, purpose=DELIVER, **fields):
    item = LoadItem.objects.create(load=load, client=client, purpose=purpose, **fields)
    for product, rolls in lines:
        LoadLine.objects.create(item=item, product=product, rolls=rolls, price=product.price)
    return item


def owes(client, seller, paket, amount=Decimal("1200000"), due=-1):
    """A sale the client hasn't paid: `amount` of debt, its deadline `due` days from
    today — overdue by default."""
    sale = Sale.objects.create(client=client, sales_rep=seller, date=TODAY() - timedelta(days=5),
                               debt_deadline=TODAY() + timedelta(days=due))
    SaleItem.objects.create(sale=sale, product=paket, weight=Decimal("10"), price=amount / 10,
                            cost_price=Decimal("1"))
    return sale


def route(client, user, load):
    client.force_login(user)
    return client.get(f"/yetkazish/{load.pk}/marshrut/")


def names(resp, n=0):
    return [s["client"].name for s in resp.context["routes"][n]["stops"]]


def post_order(client, user, load, items, **extra):
    client.force_login(user)
    return client.post(f"/yetkazish/{load.pk}/", {"action": "save",
                                                  "stop": [i.pk for i in items], **extra})


@pytest.fixture
def three(load, seller_user, warehouse, paket):
    """Three stops east of the warehouse: B nearest, then C, then A."""
    return [stop(load, make_client(seller_user, n, d), (paket, 10))
            for n, d in (("A", 0.03), ("B", 0.01), ("C", 0.02))]


@pytest.fixture
def loop(load, seller_user, warehouse, paket):
    """A round trip, not a line: B and C east of the warehouse, A north of C — the way
    back from A is diagonal, so a place between B and C is on the way out only."""
    return [stop(load, make_client(seller_user, n, d, dlat=lat), (paket, 10))
            for n, d, lat in (("A", 0.02, 0.02), ("B", 0.01, 0.0), ("C", 0.02, 0.0))]


# --- do'konlar on the client form ---------------------------------------------------

def post_client(client, user, url, **data):
    client.force_login(user)
    base = {"name": "Anvar", "phone": "", "address": "", "location": "", "notes": ""}
    return client.post(url, {**base, **data}, **XHR)


def test_a_client_is_saved_with_its_dokonlar_and_notes(client, seller_user):
    resp = post_client(client, seller_user, "/clients/new/",
                       location="41.3, 69.24", location_note="Chorsu, asosiy",
                       shops_id=["", ""], shops_note=["Bozor, 2-do'kon", ""],
                       shops_location=["41.31, 69.25", "41.32, 69.26"])
    assert resp.status_code == 204
    c = Client.objects.get(name="Anvar")
    assert c.location_note == "Chorsu, asosiy"
    assert [(s.note, s.coordinates) for s in c.locations.all()] == [
        ("Bozor, 2-do'kon", "41.31, 69.25"), ("", "41.32, 69.26")]


def test_editing_keeps_a_dokon_in_place_and_a_removed_one_sends_its_stops_home(
        client, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    kept, gone = shop(c, "Bozor", 0.02), shop(c, "Eski", 0.03)
    to_kept = stop(load, c, (paket, 1), location=kept)
    other = Load.objects.create(date=TODAY())
    to_gone = stop(other, c, (paket, 1), location=gone)
    resp = post_client(client, seller_user, f"/clients/{c.pk}/edit/", name="Anvar",
                       location="41.3, 69.25",
                       shops_id=[str(kept.pk)], shops_note=["Bozor, yangi nomi"],
                       shops_location=["41.3, 69.26"])
    assert resp.status_code == 204
    kept.refresh_from_db()
    assert (kept.note, kept.coordinates) == ("Bozor, yangi nomi", "41.3, 69.26")
    assert not ClientLocation.objects.filter(pk=gone.pk).exists()
    to_kept.refresh_from_db()
    to_gone.refresh_from_db()
    assert to_kept.location == kept and to_gone.location is None
    summary = AuditLog.objects.filter(target_type="Mijoz", target_id=c.pk).latest("pk").summary
    assert "Bozor, yangi nomi" in summary


@pytest.mark.parametrize("fields,error", [
    ({"shops_note": ["Bozor"], "shops_location": ["41.31, 69.25"]}, "Avval asosiy joylashuvni"),
    ({"location": "41.3, 69.24", "shops_note": ["Bozor"], "shops_location": [""]},
     "uchun joylashuvni kiriting"),
])
def test_a_wrong_dokon_is_refused(client, seller_user, fields, error):
    resp = post_client(client, seller_user, "/clients/new/", shops_id=[""], **fields)
    assert resp.status_code == 422
    assert error in resp.content.decode()
    assert not Client.objects.filter(name="Anvar").exists()


def test_the_form_and_the_location_window_show_the_dokonlar(client, seller_user):
    c = make_client(seller_user, "Anvar", 0.01)
    c.location_note = "Asosiy"
    c.save()
    shop(c, "Bozor, 2-do'kon", 0.02)
    client.force_login(seller_user)
    body = client.get(f"/clients/{c.pk}/edit/").content.decode()
    assert 'name="shops_note" value="Bozor, 2-do&#x27;kon"' in body
    assert 'name="shops_location" value="41.3, 69.26"' in body
    window = client.get(f"/clients/{c.pk}/location/", **XHR).content.decode()
    assert "Boshqa do'konlar" in window and "Bozor, 2-do&#x27;kon" in window


# --- which do'kon a stop goes to ------------------------------------------------------

def test_a_stop_picks_the_dokon_on_the_day_page(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    bozor = shop(c, "Bozor", 0.02)
    item = stop(load, c, (paket, 2))
    client.force_login(admin_user)
    page = client.get(f"/yetkazish/{load.pk}/").content.decode()
    assert f'name="place_{item.pk}"' in page and ">Bozor</option>" in page
    resp = client.post(f"/yetkazish/{load.pk}/", {"action": "items",
                                                   f"place_{item.pk}": str(bozor.pk)})
    assert resp.status_code == 302
    item.refresh_from_db()
    assert item.location == bozor
    assert AuditLog.objects.filter(summary__contains="do'kon — Bozor").exists()


def test_another_clients_dokon_is_refused(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    stranger = shop(make_client(seller_user, "Boshqa", 0.05), "Begona", 0.06)
    item = stop(load, c, (paket, 2))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "items",
                                           f"place_{item.pk}": str(stranger.pk)})
    item.refresh_from_db()
    assert item.location is None


def test_a_single_place_client_has_no_dokon_box(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 2))
    client.force_login(admin_user)
    assert f'name="place_{item.pk}"' not in client.get(f"/yetkazish/{load.pk}/").content.decode()


def test_a_new_stop_is_added_for_a_dokon(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    bozor = shop(c, "Bozor", 0.02)
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/", {
        "action": "items", "new_client": [c.pk], "new_purpose": [DELIVER], "new_key": ["1"],
        "new_method": ["truck"], "new_place": [str(bozor.pk)],
        "new_product": [paket.pk], "new_rolls": ["3"]})
    assert resp.status_code == 302
    assert load.items.get().location == bozor
    assert str(c.pk) in client.get(f"/yetkazish/{load.pk}/").context["client_shops"].__repr__() \
        or c.pk in client.get(f"/yetkazish/{load.pk}/").context["client_shops"]


def add_rows(client, user, load, rows):
    """«Mijoz qo'shish» with several rows: (client, do'kon or None, rolls) each."""
    client.force_login(user)
    return client.post(f"/yetkazish/{load.pk}/", {
        "action": "items", "new_client": [c.pk for c, _, _ in rows],
        "new_purpose": [DELIVER] * len(rows), "new_key": [str(n) for n in range(1, len(rows) + 1)],
        "new_method": ["truck"] * len(rows),
        "new_place": [str(s.pk) if s else "" for _, s, _ in rows],
        "new_product": [rows[0][2][0].pk] * len(rows), "new_rolls": [str(r[2][1]) for r in rows]})


def test_one_client_is_added_for_two_dokonlar_at_once(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Sarvar", 0.01)
    bozor = shop(c, "Bozor", 0.02)
    resp = add_rows(client, admin_user, load, [(c, None, (paket, 3)), (c, bozor, (paket, 5))])
    assert resp.status_code == 302
    items = LoadItem.objects.filter(client=c).order_by("pk")
    assert [(i.location, i.lines.get().rolls) for i in items] == [(None, 3), (bozor, 5)]
    # A client is once per Yuk: the second row went into the day's next one.
    assert items[0].load == load and items[1].load != load and items[1].load.date == load.date
    said = [m.message for m in get_messages(resp.wsgi_request)]
    assert "Qo'shildi: Sarvar (asosiy), Sarvar (Bozor)." in said


def test_one_client_twice_for_the_same_dokon_is_refused(client, admin_user, seller_user, load,
                                                        paket):
    c = make_client(seller_user, "Sarvar", 0.01)
    bozor = shop(c, "Bozor", 0.02)
    resp = add_rows(client, admin_user, load, [(c, bozor, (paket, 3)), (c, bozor, (paket, 5))])
    assert resp.status_code == 200 and not LoadItem.objects.exists()
    assert "bir do&#x27;kon bilan ikki qatorda tanlangan" in resp.content.decode()


def test_the_route_goes_to_the_dokon_and_tells_the_driver(client, admin_user, seller_user,
                                                           load, warehouse, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    bozor = shop(c, "Bozor, 2-do'kon", 0.05)
    stop(load, c, (paket, 2), location=bozor)
    resp = route(client, admin_user, load)
    card = resp.context["routes"][0]
    assert card["stops"][0]["shop"] == "Bozor, 2-do'kon"
    assert resp.context["map_data"]["routes"][0]["stops"][0]["lng"] == pytest.approx(69.29)
    text = card["share"]["text"]
    assert "Anvar (Bozor, 2-do'kon)" in text and bozor.google_maps_url in text
    assert "Anvar ko'chasi" not in text  # the main joylashuv's address isn't this shop's


def test_a_stop_moved_to_another_day_keeps_its_dokon_and_loses_zarur(client, admin_user,
                                                                       seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    bozor = shop(c, "Bozor", 0.02)
    item = stop(load, c, (paket, 2), location=bozor, urgent_at=timezone.now())
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/boshqa-kunga/", {"move": [item.pk], "target": "tomorrow"})
    item.refresh_from_db()
    assert item.load.date == load.date + timedelta(days=1)
    assert item.location == bozor and item.urgent_at is None


# --- ⇅ Teskari ------------------------------------------------------------------------

def test_teskari_saves_the_order_the_page_turned_round(client, admin_user, load, three):
    a, b, c = three
    assert names(route(client, admin_user, load)) == ["B", "C", "A"]
    resp = post_order(client, admin_user, load, [a, c, b], why="reverse")
    assert resp.status_code == 302
    assert names(route(client, admin_user, load)) == ["A", "C", "B"]
    assert AuditLog.objects.filter(summary__contains="teskari qilindi").exists()


def test_the_route_has_teskari_and_zarur_buttons(client, admin_user, load, three):
    body = route(client, admin_user, load).content.decode()
    assert "data-route-reverse" in body and 'name="urgent"' in body


# --- ⚡ Zarur -------------------------------------------------------------------------

def test_zarur_puts_the_stop_first_and_the_rest_on_from_it(client, admin_user, load, three):
    a, b, c = three
    resp = post_order(client, admin_user, load, [b, c, a], urgent=str(a.pk))
    assert resp.status_code == 302
    a.refresh_from_db()
    assert a.urgent_at is not None
    # From A (furthest) the nearest is C, then B — not B, C as from the warehouse.
    shown = route(client, admin_user, load)
    assert names(shown) == ["A", "C", "B"]
    assert shown.context["routes"][0]["stops"][0]["urgent"]
    assert "⚡ ZARUR — A" in shown.context["routes"][0]["share"]["text"]


def test_the_last_one_pressed_goes_first(client, admin_user, load, three):
    a, b, c = three
    post_order(client, admin_user, load, [b, c, a], urgent=str(a.pk))
    post_order(client, admin_user, load, [a, c, b], urgent=str(c.pk))
    assert names(route(client, admin_user, load)) == ["C", "A", "B"]


def test_zarur_pressed_again_comes_off_and_keeps_the_order(client, admin_user, load, three):
    a, b, c = three
    post_order(client, admin_user, load, [b, c, a], urgent=str(a.pk))
    post_order(client, admin_user, load, [a, c, b], urgent=str(a.pk))
    a.refresh_from_db()
    assert a.urgent_at is None
    assert names(route(client, admin_user, load)) == ["A", "C", "B"]


def test_the_suggestion_keeps_zarur_first(client, admin_user, load, three):
    a, b, c = three
    LoadItem.objects.filter(pk=a.pk).update(urgent_at=timezone.now())
    assert names(route(client, admin_user, load)) == ["A", "C", "B"]


def test_zarur_shows_on_the_day_page(client, admin_user, load, three):
    LoadItem.objects.filter(pk=three[0].pk).update(urgent_at=timezone.now())
    client.force_login(admin_user)
    assert "⚡ Zarur" in client.get(f"/yetkazish/{load.pk}/").content.decode()


def test_zarur_only_for_admins_and_managers(client, seller_user, load, three):
    a, b, c = three
    resp = post_order(client, seller_user, load, [b, c, a], urgent=str(a.pk))
    assert resp.status_code == 403
    a.refresh_from_db()
    assert a.urgent_at is None


# --- $ Yo'l-yo'lakay ------------------------------------------------------------------

def test_a_debtor_near_the_route_is_offered(client, admin_user, seller_user, load, loop, paket):
    near = make_client(seller_user, "Yaqin", 0.015, dlat=0.001)
    owes(near, seller_user, paket)
    far = make_client(seller_user, "Uzoq", 0.02, dlat=0.1)  # ~11 km north
    owes(far, seller_user, paket)
    on_day = loop[0].client
    owes(on_day, seller_user, paket)  # already a stop today: not offered again
    paid = make_client(seller_user, "Toza", 0.015, dlat=0.0005)  # no debt
    resp = route(client, admin_user, load)
    offered = [w["client"].name for w in resp.context["on_way"]]
    assert offered == ["Yaqin"]
    w = resp.context["routes"][0]["onway"][0]
    assert w["debt_text"] == "1 200 000" and w["at"] == "1-manzildan keyin" and w["km"] < 1
    assert resp.context["map_data"]["onway"][0]["name"] == "Yaqin"
    assert "+ Marshrutga" in resp.content.decode()
    assert paid.name not in offered


def test_only_money_due_by_the_day_is_offered(client, admin_user, seller_user, load, loop, paket):
    """«$» asks only for money due by the route's day: past its deadline, or due that
    very day. A debt still inside its term isn't asked for — only shown in the whole
    debt beside the due amount."""
    late = make_client(seller_user, "Kechikkan", 0.015, dlat=0.001)
    owes(late, seller_user, paket, due=-3)
    owes(late, seller_user, paket, Decimal("500000"), due=7)  # not due yet
    today = make_client(seller_user, "Bugun", 0.015, dlat=0.0015)
    owes(today, seller_user, paket, Decimal("300000"), due=0)
    fresh = make_client(seller_user, "Yangi", 0.015, dlat=0.0012)
    owes(fresh, seller_user, paket, due=10)  # bought on 14 days: nothing due yet
    resp = route(client, admin_user, load)
    by_name = {w["client"].name: w for w in resp.context["on_way"]}
    assert set(by_name) == {"Kechikkan", "Bugun"}
    assert by_name["Kechikkan"]["debt_text"] == "1 200 000"
    assert by_name["Kechikkan"]["total_text"] == "1 700 000"
    assert by_name["Kechikkan"]["due_text"] == "muddati 3 kun o'tgan"
    assert by_name["Bugun"]["due_text"] == "muddati shu kuni"
    assert by_name["Bugun"]["total_text"] == ""
    assert "(jami qarz 1 700 000)" in resp.content.decode()


def test_a_route_for_tomorrow_asks_for_what_is_due_tomorrow(client, admin_user, seller_user,
                                                            load, loop, paket):
    near = make_client(seller_user, "Ertaga", 0.015, dlat=0.001)
    owes(near, seller_user, paket, due=1)
    assert route(client, admin_user, load).context["on_way"] == []
    Load.objects.filter(pk=load.pk).update(date=TODAY() + timedelta(days=1))
    load.refresh_from_db()
    [w] = route(client, admin_user, load).context["on_way"]
    assert (w["client"], w["due_text"]) == (near, "muddati shu kuni")


def test_marshrutga_adds_a_money_stop_where_it_is_on_the_way(client, admin_user, seller_user,
                                                             load, loop, paket):
    a, b, c = loop
    assert names(route(client, admin_user, load)) == ["B", "C", "A"]
    near = make_client(seller_user, "Yaqin", 0.015, dlat=0.001)
    owes(near, seller_user, paket)
    resp = post_order(client, admin_user, load, [b, c, a], onway=f"{near.pk}:")
    assert resp.status_code == 302
    added = LoadItem.objects.get(client=near)
    assert (added.purpose, added.method, added.load.date) == (COLLECT, "truck", load.date)
    assert names(route(client, admin_user, load)) == ["B", "Yaqin", "C", "A"]
    assert AuditLog.objects.filter(summary__contains="Yaqin yo'l-yo'lakay qo'shildi").exists()


def test_a_debtors_dokon_on_the_way_is_offered_and_added(client, admin_user, seller_user,
                                                          load, three, paket):
    a, b, c = three
    debtor = make_client(seller_user, "Ikki do'konli", 0.02, dlat=0.2)  # main far away
    bozor = shop(debtor, "Bozor", 0.025, dlat=0.0005)
    owes(debtor, seller_user, paket)
    w = route(client, admin_user, load).context["on_way"][0]
    assert (w["client"], w["shop"]) == (debtor, bozor)
    post_order(client, admin_user, load, [b, c, a], onway=f"{debtor.pk}:{bozor.pk}")
    assert LoadItem.objects.get(client=debtor).location == bozor


def test_marshrutga_twice_adds_once(client, admin_user, seller_user, load, three, paket):
    a, b, c = three
    near = make_client(seller_user, "Yaqin", 0.015, dlat=0.001)
    owes(near, seller_user, paket)
    post_order(client, admin_user, load, [b, c, a], onway=f"{near.pk}:")
    added = LoadItem.objects.get(client=near)
    post_order(client, admin_user, load, [b, added, c, a], onway=f"{near.pk}:")
    assert LoadItem.objects.filter(client=near).count() == 1


# --- the route maths ------------------------------------------------------------------

def S(key, lng, lat=0.0):
    return routing.Stop(key, 41.3 + lat, 69.24 + lng, Decimal("0"))


DEPOT = (41.3, 69.24)


def test_order_from_goes_on_from_the_given_point():
    stops = [S("b", 0.01), S("c", 0.02), S("d", 0.04)]
    assert [s.key for s in routing.order_from((41.3, 69.27), DEPOT, stops)] == ["c", "b", "d"] \
        or [s.key for s in routing.order_from((41.3, 69.27), DEPOT, stops)] == ["d", "c", "b"]
    assert routing.order_from((41.3, 69.27), DEPOT, []) == []


def test_urgent_first_keeps_the_given_order_of_the_urgent_ones():
    stops = [S("a", 0.01), S("b", 0.02), S("c", 0.03), S("d", 0.04)]
    out = [s.key for s in routing.urgent_first(DEPOT, stops, ["c", "a"])]
    assert out[:2] == ["c", "a"] and sorted(out[2:]) == ["b", "d"]
    assert routing.urgent_first(DEPOT, stops, ["zzz"]) == stops


def test_cheapest_insert_finds_the_gap_on_the_way():
    stops = [S("a", 0.01), S("b", 0.03)]
    at, km = routing.cheapest_insert(DEPOT, stops, (41.3, 69.26))
    assert at == 1 and km == pytest.approx(0, abs=1e-6)


def test_on_way_keeps_one_place_per_client_and_the_limit():
    # Out east to a, diagonally on to b, diagonally back: (0.02, 0.0105) lies on a → b.
    routes = [[S("a", 0.01), S("b", 0.03, 0.02)]]
    places = [((1, None), (41.4, 69.26)),        # far
              ((1, 7), (41.3105, 69.26)),        # the same client's shop, near
              ((2, None), (41.35, 69.26))]       # ~4 km off: over the limit
    found = routing.on_way(DEPOT, routes, places)
    assert [w.key for w in found] == [(1, 7)]
    assert found[0].route == 0 and found[0].after == 1
