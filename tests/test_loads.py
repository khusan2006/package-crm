"""Yetkazish as loads that become sales (docs/specs/2026-11-08-loads-to-sales.md).

The day before, sellers enter stops: a client, a purpose (deliver, collect money,
both) and the products agreed on the call — rulon and price, no kg. On delivery day
kg is entered once weighed and ✓ turns a stop into a real sale. Sellers see the whole
load and change only their own clients' stops. The route maths is in
test_routing.py, the road service in test_road.py."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.models import User
from crm import road
from crm.models import (
    AuditLog, Client, Load, LoadItem, LoadLine, Payment, Product, Sale, SaleItem, Truck, Warehouse,
)

TODAY = timezone.localdate
DELIVER, COLLECT, BOTH = "deliver", "collect", "both"


@pytest.fixture
def warehouse(db):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.300000"), Decimal("69.240000")
    w.save()
    return w


@pytest.fixture
def manager(db):
    return User.objects.create_user(username="mgr", password="x", role=User.Role.MANAGER)


@pytest.fixture
def other_seller(db):
    return User.objects.create_user(username="seller2", password="x", role=User.Role.SALES,
                                    first_name="Ikkinchi")


@pytest.fixture
def paket(db):
    return Product.objects.create(name="Paket", sku="P-1", cost_price=Decimal("10000"),
                                  price=Decimal("15000"))


@pytest.fixture
def plyonka(db):
    return Product.objects.create(name="Plyonka", sku="P-2", cost_price=Decimal("20000"),
                                  price=Decimal("26000"))


@pytest.fixture
def load(db, admin_user):
    return Load.objects.create(date=TODAY(), created_by=admin_user)


def make_client(owner, name, dlng=None):
    loc = {} if dlng is None else {"latitude": Decimal("41.300000"),
                                   "longitude": Decimal("69.240000") + Decimal(str(dlng))}
    return Client.objects.create(name=name, owner=owner, address=f"{name} ko'chasi", **loc)


def stop(load, client, *lines, purpose=DELIVER):
    """A stop with lines given as (product, rolls) or (product, rolls, kg)."""
    item = LoadItem.objects.create(load=load, client=client, purpose=purpose)
    for line in lines:
        product, rolls, kg = (line + (None,))[:3]
        LoadLine.objects.create(item=item, product=product, rolls=rolls, price=product.price,
                                kg=None if kg is None else Decimal(str(kg)))
    return item


def get(client, user, url):
    client.force_login(user)
    return client.get(url)


def detail(client, user, load):
    return get(client, user, f"/yetkazish/{load.pk}/")


def route(client, user, load):
    return get(client, user, f"/yetkazish/{load.pk}/marshrut/")


def names(resp, route_n=0):
    return [s["client"].name for s in resp.context["routes"][route_n]["stops"]]


def form_of(load):
    """The Yuk tarkibi form as the page would post it untouched: every editable box."""
    data = {"action": "items"}
    for item in load.items.all():
        data[f"purpose_{item.pk}"] = item.purpose
        for line in item.lines.all():
            data[f"rolls_{line.pk}"] = str(line.rolls)
            data[f"price_{line.pk}"] = f"{line.price.normalize():f}"
            data[f"kg_{line.pk}"] = f"{line.kg.normalize():f}" if line.kg is not None else ""
    return data


def save(client, user, load, **extra):
    """Post Yuk tarkibi: the untouched form, overridden by `extra`."""
    client.force_login(user)
    data = form_of(load)
    data.update(extra)
    return client.post(f"/yetkazish/{load.pk}/", data)


# --- who ----------------------------------------------------------------------------

def test_every_role_reaches_yetkazish(client, admin_user, manager, seller_user, load):
    for user in (admin_user, manager, seller_user):
        assert get(client, user, "/yetkazish/").status_code == 200
        assert detail(client, user, load).status_code == 200
        assert 'href="/yetkazish/"' in get(client, user, "/").content.decode()


def test_a_seller_creates_a_load(client, seller_user):
    client.force_login(seller_user)
    resp = client.post("/yetkazish/yangi/", {"sana": TODAY().isoformat()})
    load = Load.objects.get()
    assert resp.url == f"/yetkazish/{load.pk}/" and load.created_by == seller_user


def test_trucks_and_warehouse_stay_admin_only(client, manager, seller_user):
    for user in (manager, seller_user):
        assert get(client, user, "/mashinalar/").status_code == 403


def test_a_seller_sees_other_sellers_stops_but_cannot_edit_them(client, seller_user, other_seller,
                                                                load, paket):
    mine = stop(load, make_client(seller_user, "Meniki", 0.01), (paket, 5))
    theirs = stop(load, make_client(other_seller, "Begona", 0.02), (paket, 7))
    body = detail(client, seller_user, load).content.decode()
    assert "Begona" in body and "Meniki" in body
    assert f'name="purpose_{mine.pk}"' in body
    assert f'name="purpose_{theirs.pk}"' not in body  # read-only for this seller
    their_line = theirs.lines.get()
    # A forged post for the other seller's stop changes nothing.
    save(client, seller_user, load, **{f"purpose_{theirs.pk}": DELIVER,
                                       f"rolls_{their_line.pk}": "99", f"price_{their_line.pk}": "1",
                                       f"kg_{their_line.pk}": "5"})
    their_line.refresh_from_db()
    assert (their_line.rolls, their_line.kg) == (7, None)


def test_a_seller_adds_only_their_own_clients(client, seller_user, other_seller, load, paket):
    theirs = make_client(other_seller, "Begona", 0.02)
    resp = save(client, seller_user, load, new_client=[theirs.pk], new_purpose=[DELIVER],
                new_product=[paket.pk], new_rolls=["3"])
    assert resp.status_code == 200 and not load.items.exists()
    assert resp.context["new_rows"][0]["client_bad"]
    assert theirs not in resp.context["clients"]


def test_a_seller_cannot_remove_or_confirm_another_sellers_stop(client, seller_user, other_seller,
                                                               load, paket):
    theirs = stop(load, make_client(other_seller, "Begona", 0.02), (paket, 7, 100))
    client.force_login(seller_user)
    assert client.post(f"/yetkazish/{load.pk}/mijoz/{theirs.pk}/delete/").status_code == 403
    assert save(client, seller_user, load, confirm=str(theirs.pk)).status_code == 403
    assert not Sale.objects.exists()


def test_only_the_loads_creator_or_an_admin_changes_the_load(client, admin_user, seller_user,
                                                            other_seller):
    by_other = Load.objects.create(date=TODAY(), created_by=other_seller)
    client.force_login(seller_user)
    assert client.get(f"/yetkazish/{by_other.pk}/edit/").status_code == 403
    assert client.post(f"/yetkazish/{by_other.pk}/delete/").status_code == 403
    own = Load.objects.create(date=TODAY(), created_by=seller_user)
    assert client.post(f"/yetkazish/{own.pk}/delete/").status_code == 302
    assert not Load.objects.filter(pk=own.pk).exists()


# --- the day's loads ----------------------------------------------------------------

def test_a_day_lists_its_loads_with_totals(client, admin_user, seller_user, paket, plyonka):
    a = Load.objects.create(date=TODAY())
    stop(a, make_client(seller_user, "X", 0.01), (paket, 4, 100), (plyonka, 2))
    stop(a, make_client(seller_user, "Y", 0.02), (paket, 6))
    Load.objects.create(date=TODAY())
    Load.objects.create(date=TODAY() - timedelta(days=1))
    loads = list(get(client, admin_user, "/yetkazish/").context["loads"])
    assert len(loads) == 2
    first = loads[0]
    assert (first.client_count, first.roll_total, first.kg_total, first.unweighed, first.confirmed) == (
        2, 12, Decimal(100), 2, 0)


# --- stops and product lines --------------------------------------------------------

def test_a_new_stop_takes_its_products_price(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER],
                new_product=[paket.pk], new_rolls=["12"])
    assert resp.status_code == 302
    item = load.items.get()
    line = item.lines.get()
    assert (item.client, item.purpose, line.product, line.rolls, line.price, line.kg) == (
        c, DELIVER, paket, 12, Decimal("15000"), None)


def test_a_money_only_stop_has_no_products(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    save(client, admin_user, load, new_client=[c.pk], new_purpose=[COLLECT],
         new_product=[""], new_rolls=[""])
    item = load.items.get()
    assert item.purpose == COLLECT and not item.lines.exists()


def test_a_money_only_stop_refuses_a_product(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[COLLECT],
                new_product=[paket.pk], new_rolls=["3"])
    assert resp.status_code == 200 and not load.items.exists()


def test_a_delivery_needs_a_product(client, admin_user, seller_user, load):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER],
                new_product=[""], new_rolls=["3"])
    assert resp.status_code == 200 and resp.context["new_rows"][0]["product_bad"]


def test_more_products_on_a_stop(client, admin_user, seller_user, load, paket, plyonka):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    save(client, admin_user, load, **{f"addline_{item.pk}_product": [plyonka.pk],
                                      f"addline_{item.pk}_rolls": ["2"]})
    assert sorted(item.lines.values_list("product__name", "rolls")) == [("Paket", 5), ("Plyonka", 2)]


def test_a_lines_rulon_price_and_kg_are_edited_in_place(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    line = item.lines.get()
    save(client, admin_user, load, **{f"rolls_{line.pk}": "6", f"price_{line.pk}": "15 500",
                                      f"kg_{line.pk}": "120,5"})
    line.refresh_from_db()
    assert (line.rolls, line.price, line.kg) == (6, Decimal("15500"), Decimal("120.5"))


def test_a_line_is_removed_but_a_delivery_keeps_at_least_one(client, admin_user, seller_user,
                                                            load, paket, plyonka):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5), (plyonka, 2))
    a, b = item.lines.order_by("pk")
    save(client, admin_user, load, **{f"drop_{b.pk}": "1"})
    assert list(item.lines.all()) == [a]
    resp = save(client, admin_user, load, **{f"drop_{a.pk}": "1"})
    assert resp.status_code == 200 and item.lines.count() == 1  # refused, nothing saved


def test_switching_to_money_only_needs_the_products_gone(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    line = item.lines.get()
    resp = save(client, admin_user, load, **{f"purpose_{item.pk}": COLLECT})
    assert resp.status_code == 200
    item.refresh_from_db()
    assert item.purpose == DELIVER
    save(client, admin_user, load, **{f"purpose_{item.pk}": COLLECT, f"drop_{line.pk}": "1"})
    item.refresh_from_db()
    assert item.purpose == COLLECT and not item.lines.exists()


def test_a_failed_save_keeps_everything_typed(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    line = item.lines.get()
    b = make_client(seller_user, "Botir", 0.02)
    resp = save(client, admin_user, load, **{f"kg_{line.pk}": "abc", f"rolls_{line.pk}": "6",
                                             "new_client": [b.pk], "new_purpose": [DELIVER],
                                             "new_product": [paket.pk], "new_rolls": [""]})
    assert resp.status_code == 200 and load.items.count() == 1
    line.refresh_from_db()
    assert (line.rolls, line.kg) == (5, None)
    body = resp.content.decode()
    assert f'name="kg_{line.pk}" value="abc"' in body and f'name="rolls_{line.pk}" value="6"' in body
    assert f'<option value="{b.pk}" selected>' in body
    assert resp.context["new_rows"][0]["rolls_bad"] and "data-unsaved" in body


def test_the_same_client_is_never_twice_in_a_load(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    stop(load, c, (paket, 5))
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER],
                new_product=[paket.pk], new_rolls=["3"])
    assert resp.status_code == 200 and load.items.count() == 1


# --- ✓: a stop becomes a sale -------------------------------------------------------

@pytest.fixture
def weighed(load, seller_user, paket, plyonka):
    return stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5, 120), (plyonka, 2, 30.5))


def test_confirming_a_stop_makes_the_sale(client, admin_user, seller_user, load, weighed):
    resp = save(client, admin_user, load, confirm=str(weighed.pk))
    assert resp.status_code == 302
    sale = Sale.objects.get()
    weighed.refresh_from_db()
    assert weighed.sale == sale
    assert (sale.date, sale.client, sale.sales_rep, sale.note) == (
        load.date, weighed.client, seller_user, f"Yuk #{load.pk}")
    assert sale.debt_term_days == 7 and sale.debt_deadline == load.date + timedelta(days=7)
    lines = {i.product.name: i for i in sale.items.all()}
    assert (lines["Paket"].weight, lines["Paket"].rolls, lines["Paket"].price, lines["Paket"].cost_price) == (
        Decimal("120"), 5, Decimal("15000"), Decimal("10000"))
    assert lines["Plyonka"].weight == Decimal("30.5")
    assert all(i.fulfilled_at == sale.date for i in sale.items.all())
    assert AuditLog.objects.filter(target_type="Sotuv", target_id=sale.pk,
                                   summary__contains=f"Yuk #{load.pk}").exists()


def test_kg_typed_just_before_the_tick_counts(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    line = item.lines.get()
    save(client, admin_user, load, **{f"kg_{line.pk}": "140", "confirm": str(item.pk)})
    assert SaleItem.objects.get().weight == Decimal("140")


def test_a_stop_without_all_its_kg_is_not_confirmed(client, admin_user, seller_user, load, paket,
                                                    plyonka):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5, 120), (plyonka, 2))
    save(client, admin_user, load, confirm=str(item.pk))
    assert not Sale.objects.exists()
    item.refresh_from_db()
    assert item.sale is None


def test_confirming_twice_makes_one_sale(client, admin_user, load, weighed):
    save(client, admin_user, load, confirm=str(weighed.pk))
    save(client, admin_user, load, confirm=str(weighed.pk))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "items", "confirm": str(weighed.pk)})
    assert Sale.objects.count() == 1


def test_the_sale_spends_the_clients_advance(client, admin_user, seller_user, load, weighed):
    Payment.objects.create(client=weighed.client, amount=Decimal("1000000"),
                           amount_original=Decimal("1000000"), method=Payment.Method.CASH,
                           kind=Payment.Kind.ADVANCE_IN, date=TODAY(), created_by=seller_user)
    save(client, admin_user, load, confirm=str(weighed.pk))
    assert Payment.objects.filter(kind=Payment.Kind.ADVANCE_USED, sale=Sale.objects.get()).exists()


def test_a_confirmed_stop_is_locked(client, admin_user, seller_user, load, weighed):
    save(client, admin_user, load, confirm=str(weighed.pk))
    line = weighed.lines.first()
    body = detail(client, admin_user, load).content.decode()
    assert f'name="kg_{line.pk}"' not in body and "Sotuv #" in body
    save(client, admin_user, load, **{f"kg_{line.pk}": "999", f"purpose_{weighed.pk}": COLLECT})
    line.refresh_from_db()
    assert line.kg != Decimal("999")
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/mijoz/{weighed.pk}/delete/")
    assert LoadItem.objects.filter(pk=weighed.pk).exists()
    client.post(f"/yetkazish/{load.pk}/delete/")
    assert Load.objects.filter(pk=load.pk).exists()  # a load with sales stays


def test_deleting_the_sale_unlocks_the_stop(client, admin_user, load, weighed):
    save(client, admin_user, load, confirm=str(weighed.pk))
    Sale.objects.get().delete()
    weighed.refresh_from_db()
    assert weighed.sale is None
    assert f'name="purpose_{weighed.pk}"' in detail(client, admin_user, load).content.decode()


def test_a_client_without_a_seller_is_not_booked_to_an_admin(client, admin_user, load, paket):
    item = stop(load, make_client(admin_user, "Admin mijozi", 0.01), (paket, 5, 100))
    save(client, admin_user, load, confirm=str(item.pk))
    assert not Sale.objects.exists()


def test_a_seller_confirms_their_own_stop(client, seller_user, load, weighed):
    save(client, seller_user, load, confirm=str(weighed.pk))
    assert Sale.objects.get().sales_rep == seller_user


# --- the route ----------------------------------------------------------------------

@pytest.fixture
def three(load, seller_user, warehouse, paket):
    return [stop(load, make_client(seller_user, n, d), (paket, 10))
            for n, d in (("A", 0.03), ("B", 0.01), ("C", 0.02))]


def test_route_is_suggested_from_the_warehouse_outward(client, admin_user, load, three):
    resp = route(client, admin_user, load)
    assert names(resp) == ["B", "C", "A"]
    assert resp.context["km"] > 0


def test_saving_and_going_back_to_the_suggested_order(client, admin_user, load, three):
    a, b, c = three
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk, a.pk, b.pk]})
    assert names(route(client, admin_user, load)) == ["C", "A", "B"]
    resp = client.post(f"/yetkazish/{load.pk}/", {"action": "reset"})
    assert resp.url == f"/yetkazish/{load.pk}/?marshrut=1"
    assert names(route(client, admin_user, load)) == ["B", "C", "A"]


def test_an_unsaved_order_has_a_way_back(client, admin_user, load, three):
    assert "data-route-suggest" in route(client, admin_user, load).content.decode()


def test_trucks_wait_for_an_estimate_when_a_product_has_no_history(client, admin_user, load, three):
    Truck.objects.create(name="Labo", capacity_kg=1000)
    resp = route(client, admin_user, load)
    assert resp.context["routes"][0]["truck"] is None and resp.context["unknown"]
    assert "kg noma" in resp.content.decode()  # not a misleading "0 kg"


def test_past_sales_give_a_preliminary_truck(client, admin_user, seller_user, load, three, paket):
    Truck.objects.create(name="Labo", capacity_kg=1000)
    past = Sale.objects.create(client=three[0].client, sales_rep=seller_user,
                               date=TODAY() - timedelta(days=9))
    SaleItem.objects.create(sale=past, product=paket, weight=Decimal("200"), rolls=10,
                            price=Decimal("1"), cost_price=Decimal("1"))  # 20 kg per rulon
    resp = route(client, admin_user, load)
    assert resp.context["estimated"] and resp.context["routes"][0]["truck"].name == "Labo"
    assert resp.context["routes"][0]["kg"] == Decimal("600")  # 3 stops × 10 rulon × 20 kg
    assert "taxminiy" in resp.content.decode()


def test_weighed_kg_replace_the_estimate(client, admin_user, load, three):
    Truck.objects.create(name="Labo", capacity_kg=1000)
    for item in three:
        LoadLine.objects.filter(item=item).update(kg=Decimal("100"))
    resp = route(client, admin_user, load)
    assert not resp.context["estimated"] and resp.context["routes"][0]["kg"] == Decimal("300")


def test_finish_faster_uses_two_trucks_even_when_one_fits(client, admin_user, seller_user,
                                                          load, warehouse, paket):
    Truck.objects.create(name="A", capacity_kg=5000)
    Truck.objects.create(name="B", capacity_kg=5000)
    for name, dlng in (("G1", -0.05), ("G2", -0.04), ("S1", 0.04), ("S2", 0.05)):
        stop(load, make_client(seller_user, name, dlng), (paket, 5, 100))
    assert len(route(client, admin_user, load).context["routes"]) == 1  # fill one first
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "strategy", "strategy": "fast"})
    load.refresh_from_db()
    assert load.strategy == "fast"
    routes = route(client, admin_user, load).context["routes"]
    assert len(routes) == 2
    assert sorted(sorted(s["client"].name[0] for s in r["stops"]) for r in routes) == [
        ["G", "G"], ["S", "S"]]


def test_a_money_only_stop_is_on_the_route_with_the_debt(client, admin_user, seller_user,
                                                         load, warehouse, paket):
    c = make_client(seller_user, "Qarzdor", 0.01)
    owed = Sale.objects.create(client=c, sales_rep=seller_user, date=TODAY() - timedelta(days=20))
    SaleItem.objects.create(sale=owed, product=paket, weight=Decimal("10"), price=Decimal("120000"),
                            cost_price=Decimal("1"))
    stop(load, c, purpose=COLLECT)
    resp = route(client, admin_user, load)
    s = resp.context["routes"][0]["stops"][0]
    assert s["kg"] == 0 and "pul olish: 1 200 000 so'm" in s["amount"]
    assert "pul olish: 1 200 000 so'm" in resp.context["routes"][0]["share"]["text"]


def test_drivers_message_has_rolls_and_kg_when_weighed(client, admin_user, load, three):
    LoadLine.objects.filter(item=three[1]).update(kg=Decimal("250"))
    text = route(client, admin_user, load).context["routes"][0]["share"]["text"]
    assert text.startswith(f"Yuk #{load.pk} — {TODAY():%d.%m.%Y}")
    assert "1. B — 10 rulon · 250 kg" in text and "2. C — 10 rulon\n" in text


def test_missing_locations_hold_the_route_back(client, admin_user, seller_user, load, three, paket):
    nowhere = make_client(seller_user, "Joysiz")
    stop(load, nowhere, (paket, 1))
    resp = route(client, admin_user, load)
    assert resp.context["routes"] == [] and resp.context["missing"] == [nowhere]
    assert f"/clients/{nowhere.pk}/edit/" in detail(client, admin_user, load).content.decode()


def test_no_warehouse_location_is_explained(client, admin_user, seller_user, load, paket):
    stop(load, make_client(seller_user, "A", 0.01), (paket, 1))
    resp = route(client, admin_user, load)
    assert resp.context["routes"] == [] and "Ombor joylashuvi" in resp.context["error"]


def test_ids_are_written_without_a_thousands_separator(client, admin_user, seller_user, load,
                                                       warehouse, paket):
    item = LoadItem.objects.create(pk=1500, load=load, client=make_client(seller_user, "K", 0.01))
    LoadLine.objects.create(pk=1600, item=item, product=paket, rolls=1, price=paket.price)
    assert 'name="stop" value="1500"' in route(client, admin_user, load).content.decode()
    body = detail(client, admin_user, load).content.decode()
    assert 'name="purpose_1500"' in body and 'name="kg_1600"' in body


def test_client_names_cannot_break_out_of_the_map_data(client, admin_user, seller_user, load,
                                                       warehouse, paket):
    stop(load, make_client(seller_user, "</script><b>x", 0.01), (paket, 1))
    assert "</script><b>x" not in route(client, admin_user, load).content.decode()


# --- by road ------------------------------------------------------------------------

@pytest.fixture
def road_key(settings):
    cache.clear()
    settings.OPEN_ROUTE_API_KEY = "test-key"
    yield
    cache.clear()


def test_by_road_the_order_follows_road_distance(client, admin_user, seller_user, load, warehouse,
                                                 paket, road_key, monkeypatch):
    stop(load, make_client(seller_user, "Yaqin", 0.01), (paket, 1))
    stop(load, make_client(seller_user, "Uzoq", 0.03), (paket, 1))
    road_km = {("W", "Yaqin"): 9.0, ("W", "Uzoq"): 2.0, ("Yaqin", "Uzoq"): 3.0}

    def fake_matrix(points):
        at = {(41.3, 69.24): "W", (41.3, 69.25): "Yaqin", (41.3, 69.27): "Uzoq"}
        labels = [at[(round(p[0], 4), round(p[1], 4))] for p in points]
        return [[0 if a == b else road_km.get((a, b), road_km.get((b, a))) for b in labels]
                for a in labels]

    monkeypatch.setattr(road, "matrix_km", fake_matrix)
    monkeypatch.setattr(road, "path", lambda points: [[41.3, 69.24], [41.31, 69.26], [41.3, 69.24]])
    resp = route(client, admin_user, load)
    assert resp.context["by_road"] and names(resp) == ["Uzoq", "Yaqin"]
    assert resp.context["km"] == pytest.approx(2 + 3 + 9)


def test_telegram_sends_yandex_links_only(client, admin_user, seller_user, load, warehouse, paket):
    from urllib.parse import parse_qs, urlparse
    stop(load, make_client(seller_user, "A", 0.01), (paket, 4))
    share = route(client, admin_user, load).context["routes"][0]["share"]
    tg = parse_qs(urlparse(share["telegram_url"]).query)
    assert tg["url"] == [share["yandex_link"]]
    assert "yandex.uz/maps" in tg["text"][0] and "google" not in tg["text"][0]
