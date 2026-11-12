"""Yetkazish as loads that become sales (docs/specs/2026-11-08-loads-to-sales.md).

The day before, sellers enter stops: a client, a purpose (deliver, collect money,
both) and the products agreed on the call — rulon and price, no kg. On delivery day
kg is entered once weighed and ✓ turns a stop into a real sale. Sellers see the whole
load and change only their own clients' stops. The route maths is in
test_routing.py, the road service in test_road.py."""

import io
import re
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


def test_a_seller_creates_a_load(client, seller_user, paket):
    """«Yangi yuk» opens a draft; the load is made on its first «Saqlash», with the
    client in it."""
    c = make_client(seller_user, "Anvar", 0.01)
    client.force_login(seller_user)
    resp = client.post("/yetkazish/yangi/", {
        "action": "items", "sana": TODAY().isoformat(), "new_key": ["1"],
        "new_client": [c.pk], "new_purpose": [DELIVER], "new_product": [paket.pk],
        "new_rolls": ["4"], "new_price": [""], "new_size": [""], "new_micron": [""]})
    load = Load.objects.get()
    assert resp.url == f"/yetkazish/?sana={TODAY().isoformat()}" and load.created_by == seller_user
    assert load.date == TODAY()
    line = load.items.get().lines.get()
    assert (line.product, line.rolls, line.price) == (paket, 4, Decimal("15000"))
    log = AuditLog.objects.get(target_type="Yuk", action=AuditLog.Action.CREATE)
    assert f"Yuk #{load.pk}" in log.summary and "Anvar" in log.summary


def test_opening_a_new_load_and_leaving_leaves_no_load(client, seller_user):
    """The bug this replaces: one click used to save an empty load straight away.
    «Yangi yuk» is now the day's page, which writes nothing until a client is added."""
    resp = get(client, seller_user, f"/yetkazish/yangi/?sana={TODAY().isoformat()}")
    assert resp.status_code == 302 and resp.url == f"/yetkazish/?sana={TODAY().isoformat()}"
    assert client.get(resp.url).status_code == 200
    assert Load.objects.count() == 0 and not AuditLog.objects.filter(target_type="Yuk").exists()


def test_an_old_one_click_post_only_opens_the_draft(client, seller_user):
    client.force_login(seller_user)
    resp = client.post("/yetkazish/yangi/", {"sana": TODAY().isoformat()})
    assert resp.status_code == 302 and resp.url.startswith("/yetkazish/?sana=")
    assert Load.objects.count() == 0


def test_saving_an_empty_draft_makes_no_load(client, seller_user):
    client.force_login(seller_user)
    resp = client.post("/yetkazish/yangi/", {
        "action": "items", "sana": TODAY().isoformat(), "new_key": ["1"], "new_client": [""],
        "new_purpose": [DELIVER], "new_product": [""], "new_rolls": [""], "new_price": [""],
        "new_size": [""], "new_micron": [""]})
    assert resp.status_code == 302 and Load.objects.count() == 0


def test_a_wrong_draft_comes_back_and_makes_no_load(client, seller_user, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    client.force_login(seller_user)
    resp = client.post("/yetkazish/yangi/", {
        "action": "items", "sana": TODAY().isoformat(), "new_key": ["1"],
        "new_client": [c.pk], "new_purpose": [DELIVER], "new_product": [paket.pk],
        "new_rolls": ["abc"], "new_price": [""], "new_size": [""], "new_micron": [""]})
    assert resp.status_code == 200 and resp.context["adding"]   # the form comes back open
    assert resp.context["new_rows"][0]["rolls"] == "abc"
    assert Load.objects.count() == 0


def test_trucks_and_warehouse_stay_admin_only(client, manager, seller_user):
    for user in (manager, seller_user):
        assert get(client, user, "/mashinalar/").status_code == 403


def test_a_seller_sees_only_their_own_clients(client, seller_user, other_seller, admin_user,
                                              load, paket):
    """Yetkazish shows a seller their own clients only; admins/managers see all."""
    mine = stop(load, make_client(seller_user, "Meniki", 0.01), (paket, 5))
    theirs = stop(load, make_client(other_seller, "Begona", 0.02), (paket, 7))
    body = detail(client, seller_user, load).content.decode()
    assert "Meniki" in body and "Begona" not in body
    assert f'name="purpose_{mine.pk}"' in body
    assert "Begona" in detail(client, admin_user, load).content.decode()
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


def test_only_admins_and_managers_change_the_days_yuk(client, admin_user, manager, seller_user,
                                                     other_seller, paket):
    """The day's Yuk is made by whoever adds the first client and holds every seller's
    clients — being its "creator" gives a seller no right over the others' clients."""
    own = Load.objects.create(date=TODAY(), created_by=seller_user)
    theirs = stop(own, make_client(other_seller, "Begona"), (paket, 1))
    client.force_login(seller_user)
    assert client.get(f"/yetkazish/{own.pk}/edit/").status_code == 403
    assert client.post(f"/yetkazish/{own.pk}/delete/").status_code == 403
    assert client.post(f"/yetkazish/{own.pk}/", {"action": "start"}).status_code == 403
    assert LoadItem.objects.filter(pk=theirs.pk).exists()
    client.force_login(manager)
    assert client.get(f"/yetkazish/{own.pk}/edit/").status_code == 200


# --- the day's loads ----------------------------------------------------------------

def day_page(client, user, day=None):
    return get(client, user, f"/yetkazish/?sana={(day or TODAY()).isoformat()}")


def lane(resp, name):
    return [i.client.name for i in resp.context["lanes"][name]]


def test_the_day_page_shows_every_client_of_the_day_in_lanes(client, admin_user, seller_user,
                                                             paket):
    a = Load.objects.create(date=TODAY())
    b = Load.objects.create(date=TODAY())
    stop(a, make_client(seller_user, "Mashina"), (paket, 4, 100))
    item = stop(b, make_client(seller_user, "Olib"), (paket, 2))
    item.method = "pickup"
    item.save()
    item = stop(a, make_client(seller_user, "Yangi"), (paket, 1))
    item.method = ""
    item.save()
    item = stop(a, make_client(seller_user, "Kutadi"), (paket, 1))
    item.status = "order"
    item.save()
    finished = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
                    make_client(seller_user, "Kecha"), (paket, 1))
    finished.status = "done"  # delivered and sold yesterday: stays there
    finished.sale = Sale.objects.create(client=finished.client, sales_rep=seller_user)
    finished.save()
    resp = day_page(client, admin_user)
    # «Buyurtmalar»: not decided how it leaves (first), then waiting for stock.
    assert (lane(resp, "truck"), lane(resp, "pickup"), lane(resp, "order")) == (
        ["Mashina"], ["Olib"], ["Yangi", "Kutadi"])
    assert resp.context["truck_kg"] == Decimal("100")
    # The Yuk's old address shows the same day.
    assert lane(detail(client, admin_user, a), "pickup") == ["Olib"]


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



@pytest.mark.parametrize("typed, kg", [("50+50+50", "150"), ("48,5 + 51.25+", "99.75")])
def test_kg_typed_as_a_sum_is_saved_as_the_sum(client, admin_user, seller_user, load, paket,
                                               typed, kg):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    line = item.lines.get()
    save(client, admin_user, load, **{f"kg_{line.pk}": typed})
    line.refresh_from_db()
    assert line.kg == Decimal(kg)


@pytest.mark.parametrize("typed", ["+", "50+abc", "0+0", "50+1.2345", "50-10"])
def test_a_broken_kg_sum_is_refused(client, admin_user, seller_user, load, paket, typed):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    line = item.lines.get()
    resp = save(client, admin_user, load, **{f"kg_{line.pk}": typed})
    line.refresh_from_db()
    assert resp.status_code == 200 and line.kg is None

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
    assert re.search(rf'<option value="{b.pk}"[^>]* selected>', body)
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
    assert Sale.objects.get().entered_by == admin_user  # who pressed ✓


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


# --- review follow-ups --------------------------------------------------------------

def test_enter_in_a_box_saves_rather_than_ticking_the_first_stop(client, admin_user, load, weighed):
    # Browsers press the FIRST submit button on Enter; it must be Saqlash, not a ✓.
    import re
    body = detail(client, admin_user, load).content.decode()
    form = body[body.index("data-load-items"):]
    first_submit = re.search(r'<button[^>]*type="submit"[^>]*>', form).group(0)
    assert 'name="confirm"' not in first_submit


def test_a_seller_gets_no_links_into_another_sellers_client(client, seller_user, other_seller,
                                                            admin_user, load, paket):
    theirs_client = make_client(other_seller, "Begona", 0.02)
    theirs = stop(load, theirs_client, (paket, 7, 100))
    nowhere = make_client(other_seller, "Joysiz begona")
    stop(load, nowhere, (paket, 1))
    save(client, admin_user, load, confirm=str(theirs.pk))
    sale = Sale.objects.get()
    body = detail(client, seller_user, load).content.decode()
    assert f'id="stop-{theirs.pk}"' not in body       # not on this seller's list at all
    assert f"/sales/{sale.pk}/" not in body and f"/clients/{nowhere.pk}/edit/" not in body
    # An admin, who can open them, still gets the links.
    admin_body = detail(client, admin_user, load).content.decode()
    assert f"/sales/{sale.pk}/" in admin_body and f"/clients/{nowhere.pk}/edit/" in admin_body


def test_over_capacity_on_estimates_is_a_warning_not_an_alarm(client, admin_user, seller_user,
                                                              load, three, paket):
    Truck.objects.create(name="Kichik", capacity_kg=100)
    past = Sale.objects.create(client=three[0].client, sales_rep=seller_user,
                               date=TODAY() - timedelta(days=9))
    SaleItem.objects.create(sale=past, product=paket, weight=Decimal("200"), rolls=10,
                            price=Decimal("1"), cost_price=Decimal("1"))
    body = route(client, admin_user, load).content.decode()
    assert "taxminiy kg bo" in body and "is-bad-text" not in body


# --- price on new rows --------------------------------------------------------------

def test_a_price_typed_on_a_new_stop_is_kept(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER], new_product=[paket.pk],
         new_rolls=["12"], new_price=["16 500"])
    assert load.items.get().lines.get().price == Decimal("16500")


def test_an_empty_price_takes_the_catalogue_price(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER], new_product=[paket.pk],
         new_rolls=["12"], new_price=[""])
    assert load.items.get().lines.get().price == Decimal("15000")


@pytest.mark.parametrize("raw", ["abc", "-5", "1e4", "15000.123"])
def test_a_wrong_new_price_is_a_red_box(client, admin_user, seller_user, load, paket, raw):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER],
                new_product=[paket.pk], new_rolls=["12"], new_price=[raw])
    assert resp.status_code == 200 and not load.items.exists()
    row = resp.context["new_rows"][0]
    assert row["price_bad"] and row["price"] == raw


def test_a_product_added_to_a_stop_takes_its_typed_price(client, admin_user, seller_user, load,
                                                         paket, plyonka):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    save(client, admin_user, load, **{f"addline_{item.pk}_product": [plyonka.pk],
                                      f"addline_{item.pk}_rolls": ["2"],
                                      f"addline_{item.pk}_price": ["25000"]})
    assert item.lines.get(product=plyonka).price == Decimal("25000")


def test_product_options_carry_their_catalogue_price(client, admin_user, load, paket):
    body = detail(client, admin_user, load).content.decode()
    assert f'value="{paket.pk}" data-price="15000"' in body


# --- metr / mikron, and several products on a new client ----------------------------

@pytest.fixture
def oq(db):
    """A film that comes in metr and mikron, like ОҚ / ҚОРА on the real catalogue."""
    return Product.objects.create(name="ОҚ", sku="oq", cost_price=Decimal("18000"),
                                  price=Decimal("22000"), has_size=True, has_micron=True)


def test_a_new_client_gets_several_products_at_once(client, admin_user, seller_user, load,
                                                    paket, oq):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER], new_key=["1"],
                new_product=[oq.pk], new_size=["1.5"], new_micron=["05"], new_rolls=["4"],
                new_price=[""],
                newline_1_product=[oq.pk, paket.pk], newline_1_size=["2м", "9"],
                newline_1_micron=["08", "06"], newline_1_rolls=["2", "6"],
                newline_1_price=["21500", ""])
    assert resp.status_code == 302
    item = load.items.get()
    got = sorted(item.lines.values_list("product__name", "size", "micron", "rolls", "price"))
    # A bare "1.5" is written as receipts write it; a product without metr/mikron
    # keeps none, whatever was left in its greyed boxes.
    assert got == [("Paket", "", "", 6, Decimal("15000")),
                   ("ОҚ", "1,5м", "05", 4, Decimal("22000")),
                   ("ОҚ", "2м", "08", 2, Decimal("21500"))]


def test_extra_products_stay_with_their_own_client(client, admin_user, seller_user, load, paket, oq):
    a, b = make_client(seller_user, "Anvar", 0.01), make_client(seller_user, "Botir", 0.02)
    save(client, admin_user, load, new_client=[a.pk, b.pk], new_purpose=[DELIVER, DELIVER],
         new_key=["1", "2"], new_product=[paket.pk, paket.pk], new_rolls=["1", "2"],
         newline_2_product=[oq.pk], newline_2_rolls=["7"])
    lines = {i.client.name: sorted(i.lines.values_list("product__name", "rolls"))
             for i in load.items.all()}
    assert lines == {"Anvar": [("Paket", 1)], "Botir": [("Paket", 2), ("ОҚ", 7)]}


def test_a_failed_save_brings_back_the_extra_products(client, admin_user, seller_user, load, paket, oq):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER], new_key=["3"],
                new_product=[paket.pk], new_rolls=["1"],
                newline_3_product=[oq.pk], newline_3_size=["1,5м"], newline_3_micron=["05"],
                newline_3_rolls=["x"])
    assert resp.status_code == 200 and not load.items.exists()
    row = resp.context["new_rows"][0]
    assert row["key"] == "3"
    (extra,) = row["extra"]
    assert (extra["size"], extra["micron"], extra["rolls"], extra["rolls_bad"]) == ("1,5м", "05", "x", True)
    body = resp.content.decode()
    assert 'name="newline_3_rolls"' in body and 'value="1,5м"' in body


def test_a_money_only_client_refuses_extra_products(client, admin_user, seller_user, load, paket):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[COLLECT], new_key=["1"],
                new_product=[""], new_rolls=[""],
                newline_1_product=[paket.pk], newline_1_rolls=["2"])
    assert resp.status_code == 200 and not load.items.exists()
    assert resp.context["new_rows"][0]["extra"][0]["product_bad"]


def test_a_too_long_mikron_is_a_red_box(client, admin_user, seller_user, load, oq):
    c = make_client(seller_user, "Anvar", 0.01)
    resp = save(client, admin_user, load, new_client=[c.pk], new_purpose=[DELIVER], new_key=["1"],
                new_product=[oq.pk], new_micron=["0" * 21], new_rolls=["2"])
    assert resp.status_code == 200 and resp.context["new_rows"][0]["micron_bad"]


def test_metr_and_mikron_are_edited_on_a_saved_line(client, admin_user, seller_user, load, paket, oq):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (oq, 5), (paket, 1))
    film, bag = item.lines.get(product=oq), item.lines.get(product=paket)
    body = detail(client, admin_user, load).content.decode()
    # Both lines have the boxes (the product can be swapped); the bag's stays greyed
    # out on the page and is ignored when saved.
    assert f'name="size_{film.pk}"' in body and f'name="size_{bag.pk}"' in body
    save(client, admin_user, load, **{f"size_{film.pk}": "6", f"micron_{film.pk}": "015",
                                      f"size_{bag.pk}": "2м"})
    film.refresh_from_db(), bag.refresh_from_db()
    assert (film.size, film.micron, bag.size) == ("6м", "015", "")


def test_products_added_to_a_stop_carry_metr_and_mikron(client, admin_user, seller_user, load, paket, oq):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    save(client, admin_user, load, **{f"addline_{item.pk}_product": [oq.pk],
                                      f"addline_{item.pk}_size": ["2м"],
                                      f"addline_{item.pk}_micron": ["06"],
                                      f"addline_{item.pk}_rolls": ["3"]})
    assert item.lines.filter(product=oq, size="2м", micron="06", rolls=3).exists()


def test_the_sale_line_gets_metr_and_mikron(client, admin_user, seller_user, load, oq):
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (oq, 5, 120))
    item.lines.update(size="1,5м", micron="05")
    save(client, admin_user, load, confirm=str(item.pk))
    line = SaleItem.objects.get()
    assert (line.size, line.micron, line.variant_label) == ("1,5м", "05", "1,5м · 05")


def test_product_options_say_which_variants_they_come_in(client, admin_user, load, paket, oq):
    body = detail(client, admin_user, load).content.decode()
    assert f'value="{oq.pk}" data-price="22000" data-size data-micron' in body
    assert f'value="{paket.pk}" data-price="15000">' in body
    assert 'id="load-sizes"' in body and '<option value="1,5м">' in body


def test_rulon_card_counts_each_product_and_variant(client, admin_user, seller_user, load,
                                                    paket, oq):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (oq, 2), (paket, 1))
    a.lines.filter(product=oq).update(size="2м", micron="1")
    b = stop(load, make_client(seller_user, "Bobur", 0.02), (oq, 3), (oq, 4))
    first, second = b.lines.order_by("pk")
    first.size, first.micron = "2м", "1"
    first.save()
    second.size, second.micron = "1,5м", "05"
    second.save()
    resp = detail(client, admin_user, load)
    assert resp.context["roll_breakdown"] == [
        {"rolls": 5, "product": "ОҚ", "size": "2м", "micron": "1", "share": 50},
        {"rolls": 4, "product": "ОҚ", "size": "1,5м", "micron": "05", "share": 40},
        {"rolls": 1, "product": paket.name, "size": "", "micron": "", "share": 10},
    ]
    body = resp.content.decode()
    assert '<span class="kb-count">5<small>ta</small></span>' in body
    assert '<span class="kb-tag" title="Mikron">05<small>mk</small></span>' in body


def test_the_new_stop_client_picker_finds_a_client_by_phone(client, admin_user, seller_user, load):
    c = make_client(seller_user, "Anvar", 0.01)
    c.phone, c.address = "+998 90 123 45 67", "Chilonzor"
    c.save()
    body = detail(client, admin_user, load).content.decode()
    assert ('data-search="anvar +998 90 123 45 67 chilonzor 998901234567" '
            'data-subtitle="+998 90 123 45 67 · Chilonzor"') in body


# --- moving a stop to the other truck by hand ---------------------------------------

@pytest.fixture
def two_trucks(load, seller_user, warehouse, paket):
    """«Tezroq tugatish» with two trucks: the system sends the G's one way, the S's
    the other."""
    Truck.objects.create(name="A", capacity_kg=5000)
    Truck.objects.create(name="B", capacity_kg=5000)
    load.strategy = "fast"
    load.save(update_fields=["strategy"])
    return {name: stop(load, make_client(seller_user, name, dlng), (paket, 5, 100))
            for name, dlng in (("G1", -0.05), ("G2", -0.04), ("S1", 0.04), ("S2", 0.05))}


def by_truck(resp):
    return [sorted(s["client"].name for s in r["stops"]) for r in resp.context["routes"]]


def save_split(client, user, load, resp, move=()):
    """Post the route as drawn, with the stops named in `move` sent to the other truck."""
    stops, groups = [], []
    for n, r in enumerate(resp.context["routes"]):
        for s in r["stops"]:
            stops.append(s["item"].pk)
            groups.append(str(1 - n if s["client"].name in move else n))
    client.force_login(user)
    return client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": stops,
                                                  "group": groups, "routes": "2"})


def test_a_stop_is_moved_to_the_other_truck_and_stays_there(client, admin_user, load, two_trucks):
    first = route(client, admin_user, load)
    assert sorted(by_truck(first)) == [["G1", "G2"], ["S1", "S2"]]
    assert "data-move-truck" in first.content.decode()
    save_split(client, admin_user, load, first, move=("G2",))
    again = route(client, admin_user, load)
    assert sorted(by_truck(again)) == [["G1"], ["G2", "S1", "S2"]]
    # The same truck keeps its name: the one G1 was on.
    trucks = {tuple(sorted(s["client"].name for s in r["stops"])): r["truck"].name
              for r in again.context["routes"]}
    assert trucks[("G1",)] == {tuple(sorted(s["client"].name for s in r["stops"])): r["truck"].name
                               for r in first.context["routes"]}[("G1", "G2")]


def test_going_back_to_the_suggestion_forgets_the_hand_split(client, admin_user, load, two_trucks):
    save_split(client, admin_user, load, route(client, admin_user, load), move=("G2",))
    client.post(f"/yetkazish/{load.pk}/", {"action": "reset"})
    load.refresh_from_db()
    assert load.split_routes is None
    assert not load.items.filter(route_group__isnull=False).exists()
    assert sorted(by_truck(route(client, admin_user, load))) == [["G1", "G2"], ["S1", "S2"]]


def test_one_truck_again_drops_the_hand_split(client, admin_user, load, two_trucks):
    save_split(client, admin_user, load, route(client, admin_user, load), move=("G2",))
    client.post(f"/yetkazish/{load.pk}/", {"action": "strategy", "strategy": "fill"})
    resp = route(client, admin_user, load)
    assert len(by_truck(resp)) == 1 and "data-move-truck" not in resp.content.decode()


def test_a_stop_added_after_the_split_goes_where_the_system_puts_it(client, admin_user, seller_user,
                                                                    load, two_trucks, paket):
    save_split(client, admin_user, load, route(client, admin_user, load), move=("G2",))
    stop(load, make_client(seller_user, "G3", -0.045), (paket, 5, 100))
    assert sorted(by_truck(route(client, admin_user, load))) == [["G1", "G3"], ["G2", "S1", "S2"]]


def test_a_crafted_split_is_refused(client, admin_user, load, two_trucks):
    resp = route(client, admin_user, load)
    stops = [s["item"].pk for r in resp.context["routes"] for s in r["stops"]]
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": stops,
                                           "group": ["0", "5", "1", "1"], "routes": "2"})
    load.refresh_from_db()
    assert load.ordered_at is None and load.split_routes is None


def test_one_truck_has_no_swap_button(client, admin_user, load, three):
    assert "data-move-truck" not in route(client, admin_user, load).content.decode()


def test_both_clients_go_with_one_driver(client, admin_user, seller_user, load, warehouse, paket):
    """Two clients, «Tezroq tugatish» puts one on each truck — and both can still be
    sent with one driver. The empty truck stays on the page, so one can come back."""
    Truck.objects.create(name="A", capacity_kg=5000)
    Truck.objects.create(name="B", capacity_kg=5000)
    load.strategy = "fast"
    load.save(update_fields=["strategy"])
    stop(load, make_client(seller_user, "West", -0.05), (paket, 2, 100))
    stop(load, make_client(seller_user, "East", 0.05), (paket, 2, 100))
    first = route(client, admin_user, load)
    assert sorted(by_truck(first)) == [["East"], ["West"]]
    save_split(client, admin_user, load, first, move=("East",))
    resp = route(client, admin_user, load)
    assert sorted(len(r["stops"]) for r in resp.context["routes"]) == [0, 2]
    assert [len(r["stops"]) for r in resp.context["going"]] == [2]
    body = resp.content.decode()
    assert "bu mashina chiqmaydi" in body and body.count("data-route-share>") == 1
    assert len(resp.context["map_data"]["routes"]) == 1
    # …and back again.
    save_split(client, admin_user, load, resp, move=("East",))
    assert sorted(by_truck(route(client, admin_user, load))) == [["East"], ["West"]]


# --- moving clients to another day ---------------------------------------------------

def move(client, user, load, items, **extra):
    client.force_login(user)
    data = {"move": [i.pk for i in items]}
    data.update(extra)
    return client.post(f"/yetkazish/{load.pk}/boshqa-kunga/", data)


def test_the_dialog_opens_with_the_client_ticked(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    b = stop(load, make_client(seller_user, "Botir", 0.02), (paket, 3))
    page = detail(client, admin_user, load).content.decode()
    assert f"/yetkazish/{load.pk}/boshqa-kunga/?mijoz={a.pk}" in page
    resp = client.get(f"/yetkazish/{load.pk}/boshqa-kunga/?mijoz={a.pk}",
                      HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    body = resp.content.decode()
    assert resp.status_code == 200
    # Opened from Anvar's row: Anvar's order only — nobody else is listed.
    assert f'value="{a.pk}" checked' in body and f'name="move" value="{b.pk}"' not in body
    assert 'value="tomorrow" checked' in body and "Ertaga" in body and "Yangi yuk" not in body


def test_the_dialog_shows_each_day_once(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 5))
    tomorrow = TODAY() + timedelta(days=1)
    Load.objects.create(date=tomorrow)
    Load.objects.create(date=tomorrow)
    client.force_login(admin_user)
    body = client.get(f"/yetkazish/{load.pk}/boshqa-kunga/?mijoz={a.pk}",
                      HTTP_X_REQUESTED_WITH="XMLHttpRequest").content.decode()
    assert body.count('name="target"') == 2          # tomorrow (once) and «Boshqa kun»
    assert 'value="tomorrow"' not in body             # tomorrow already planned: no second card


def test_clients_move_to_a_new_load_tomorrow(client, admin_user, seller_user, load, paket, plyonka):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5, 120), (plyonka, 2))
    b = stop(load, make_client(seller_user, "Botir", 0.02), (paket, 3))
    stays = stop(load, make_client(seller_user, "Qoldi", 0.03), (paket, 1))
    resp = move(client, admin_user, load, [a, b], target="tomorrow")
    assert resp.status_code == 302
    new = Load.objects.exclude(pk=load.pk).get()
    assert new.date == load.date + timedelta(days=1) and f"Yuk #{load.pk}" in new.note
    assert sorted(new.items.values_list("client__name", flat=True)) == ["Anvar", "Botir"]
    assert list(load.items.all()) == [stays]
    # Products, prices and kg go with the client.
    a.refresh_from_db()
    assert sorted(a.lines.values_list("product__name", "rolls", "kg")) == [
        ("Paket", 5, Decimal("120.000")), ("Plyonka", 2, None)]
    assert AuditLog.objects.filter(target_type="Yuk", target_id=load.pk,
                                   summary__contains=f"Yuk #{new.pk}").exists()


def test_clients_move_to_another_date(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5))
    day = load.date + timedelta(days=5)
    move(client, admin_user, load, [a], target="other", sana=day.isoformat())
    a.refresh_from_db()
    assert a.load.date == day


def test_clients_join_a_planned_load_at_the_end_of_its_order(client, admin_user, seller_user,
                                                             load, paket):
    later = Load.objects.create(date=load.date + timedelta(days=2), created_by=admin_user,
                                ordered_at=timezone.now())
    first = stop(later, make_client(seller_user, "Bor", 0.01), (paket, 1))
    LoadItem.objects.filter(pk=first.pk).update(position=1)
    a = stop(load, make_client(seller_user, "Anvar", 0.02), (paket, 5))
    LoadItem.objects.filter(pk=a.pk).update(route_group=1)
    client.force_login(admin_user)
    dialog = client.get(f"/yetkazish/{load.pk}/boshqa-kunga/").content.decode()
    assert f'value="{later.pk}"' in dialog and "1 mijoz" in dialog
    move(client, admin_user, load, [a], target=str(later.pk))
    a.refresh_from_db()
    assert (a.load, a.position, a.route_group) == (later, 2, None)


def test_a_sold_or_others_client_does_not_move(client, seller_user, other_seller, load, paket):
    mine = stop(load, make_client(seller_user, "Mine", 0.01), (paket, 1))
    theirs = stop(load, make_client(other_seller, "Theirs", 0.02), (paket, 1))
    client.force_login(seller_user)
    dialog = client.get(f"/yetkazish/{load.pk}/boshqa-kunga/").content.decode()
    assert f'value="{mine.pk}"' in dialog and f'value="{theirs.pk}"' not in dialog
    resp = move(client, seller_user, load, [theirs], target="tomorrow")
    assert resp.status_code == 200 and "belgilang" in resp.content.decode()
    assert load.items.count() == 2 and Load.objects.count() == 1


def test_only_others_clients_means_no_move_at_all(client, seller_user, other_seller, load, paket):
    stop(load, make_client(other_seller, "Theirs", 0.02), (paket, 1))
    assert "boshqa-kunga" not in detail(client, seller_user, load).content.decode()
    assert client.get(f"/yetkazish/{load.pk}/boshqa-kunga/").status_code == 403


def test_a_refused_move_keeps_the_choices(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 1))
    client.force_login(admin_user)
    url = f"/yetkazish/{load.pk}/boshqa-kunga/"
    for data in ({"target": "tomorrow"}, {"move": [a.pk], "target": "other", "sana": "3026-01-01"},
                 {"move": [a.pk], "target": str(load.pk)}, {"move": [a.pk], "target": "nonsense"}):
        resp = client.post(url, data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        assert resp.status_code == 422
    body = resp.content.decode()
    assert f'value="{a.pk}" checked' in body and "flash-error" in body
    assert Load.objects.count() == 1 and list(load.items.all()) == [a]


def test_a_load_planned_for_tomorrow_is_the_default(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 1))
    planned = Load.objects.create(date=max(load.date, timezone.localdate()) + timedelta(days=1),
                                  created_by=admin_user)
    client.force_login(admin_user)
    body = client.get(f"/yetkazish/{load.pk}/boshqa-kunga/?mijoz={a.pk}").content.decode()
    assert f'value="{planned.pk}" checked' in body and 'value="tomorrow" checked' not in body


# --- dividing a client's order between days ------------------------------------------

def test_some_products_go_tomorrow_the_rest_stay(client, admin_user, seller_user, load, paket, plyonka):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5), (plyonka, 2))
    film = a.lines.get(product=plyonka)
    client.force_login(admin_user)
    dialog = client.get(f"/yetkazish/{load.pk}/boshqa-kunga/?mijoz={a.pk}").content.decode()
    assert f'name="line" value="{film.pk}" checked' in dialog  # ticked with its client
    resp = client.post(f"/yetkazish/{load.pk}/boshqa-kunga/", {"line": [film.pk], "target": "tomorrow"})
    assert resp.status_code == 302
    new = Load.objects.exclude(pk=load.pk).get()
    there = new.items.get()
    assert (there.client, there.purpose) == (a.client, DELIVER)
    assert list(there.lines.values_list("product__name", "rolls")) == [("Plyonka", 2)]
    assert list(a.lines.values_list("product__name", flat=True)) == ["Paket"]
    assert AuditLog.objects.filter(target_id=load.pk, summary__contains="Anvar (Plyonka)").exists()


def test_products_join_the_clients_stop_already_there(client, admin_user, seller_user, load,
                                                      paket, plyonka):
    anvar = make_client(seller_user, "Anvar", 0.01)
    later = Load.objects.create(date=load.date + timedelta(days=1), created_by=admin_user)
    waiting = stop(later, anvar, purpose=COLLECT)
    a = stop(load, anvar, (paket, 5), (plyonka, 2))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/boshqa-kunga/",
                {"line": [a.lines.get(product=paket).pk], "target": str(later.pk)})
    waiting.refresh_from_db()
    assert waiting.purpose == BOTH  # it collected; now it delivers too
    assert list(waiting.lines.values_list("product__name", flat=True)) == ["Paket"]
    assert list(a.lines.values_list("product__name", flat=True)) == ["Plyonka"]


def test_a_whole_client_joins_its_stop_already_there(client, admin_user, seller_user, load, paket):
    anvar = make_client(seller_user, "Anvar", 0.01)
    later = Load.objects.create(date=load.date + timedelta(days=1), created_by=admin_user)
    there = stop(later, anvar, (paket, 1))
    a = stop(load, anvar, (paket, 5), purpose=BOTH)
    move(client, admin_user, load, [a], target=str(later.pk))
    assert not LoadItem.objects.filter(pk=a.pk).exists()
    there.refresh_from_db()
    assert there.purpose == BOTH and sorted(there.lines.values_list("rolls", flat=True)) == [1, 5]


def test_moving_every_product_of_a_both_stop_leaves_the_money(client, admin_user, seller_user,
                                                              load, paket, plyonka):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5), (plyonka, 2), purpose=BOTH)
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/boshqa-kunga/",
                {"line": list(a.lines.values_list("pk", flat=True)), "target": "tomorrow"})
    a.refresh_from_db()
    assert a.load == load and a.purpose == COLLECT and not a.lines.exists()


def test_every_product_ticked_moves_the_whole_delivery(client, admin_user, seller_user, load,
                                                       paket, plyonka):
    a = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5), (plyonka, 2))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/boshqa-kunga/",
                {"line": list(a.lines.values_list("pk", flat=True)), "target": "tomorrow"})
    a.refresh_from_db()
    assert a.load != load and a.lines.count() == 2


def test_a_sold_stop_there_takes_nothing(client, admin_user, seller_user, load, paket, weighed):
    later = Load.objects.create(date=load.date + timedelta(days=1), created_by=admin_user)
    save(client, admin_user, load, confirm=str(weighed.pk))  # Anvar becomes a sale here…
    LoadItem.objects.filter(pk=weighed.pk).update(load=later)  # …and sits in tomorrow's load
    again = stop(load, weighed.client, (paket, 2))
    move(client, admin_user, load, [again], target=str(later.pk))
    assert again.load == load and LoadItem.objects.get(pk=again.pk).load == load


def test_a_line_from_someone_elses_stop_is_ignored(client, seller_user, other_seller, load, paket, plyonka):
    theirs = stop(load, make_client(other_seller, "Theirs", 0.02), (paket, 1), (plyonka, 1))
    stop(load, make_client(seller_user, "Mine", 0.01), (paket, 1))
    client.force_login(seller_user)
    resp = client.post(f"/yetkazish/{load.pk}/boshqa-kunga/",
                       {"line": [theirs.lines.first().pk], "target": "tomorrow"})
    assert resp.status_code == 200 and theirs.lines.count() == 2 and Load.objects.count() == 1


# --- «Jo'natish» and arrival times ---------------------------------------------------

def test_arrival_times_add_driving_and_a_stop_at_each_client():
    from datetime import datetime
    from crm import routing
    start = datetime(2026, 11, 10, 9, 0)
    legs = {((0, 0), (0, 1)): 20, ((0, 1), (0, 2)): 40, ((0, 2), (0, 0)): 60,
            ((0, 1), (0, 0)): 20}

    def dist(a, b):
        return legs[(a, b)]

    times, back = routing.arrival_times((0, 0), [(0, 1), (0, 2)], start, dist)
    # 20 km at 40 km/h = 30 min; 15 min there; 40 km = 60 min; 15 min; 60 km = 90 min.
    assert [t.strftime("%H:%M") for t in times] == ["09:30", "10:45"]
    assert back.strftime("%H:%M") == "12:30"
    times, _ = routing.arrival_times((0, 0), [(0, 1)], start, dist, detour=2)
    assert times[0].strftime("%H:%M") == "10:00"


def start(client, user, load, **data):
    client.force_login(user)
    return client.post(f"/yetkazish/{load.pk}/", {"action": "start", **data})


def test_starting_a_load_records_the_time_typed(client, admin_user, load, three):
    when = timezone.localtime() - timedelta(minutes=40)
    resp = start(client, admin_user, load, started_at=when.strftime("%Y-%m-%dT%H:%M"))
    assert resp.url == f"/yetkazish/{load.pk}/?marshrut=1"
    load.refresh_from_db()
    assert load.started_at == when.replace(second=0, microsecond=0)
    assert AuditLog.objects.filter(target_type="Yuk", target_id=load.pk,
                                   summary__contains="jo'nadi").exists()


def test_starting_without_a_time_means_now(client, admin_user, load, three):
    start(client, admin_user, load, started_at="")
    load.refresh_from_db()
    assert abs(load.started_at - timezone.now()) < timedelta(minutes=1)


@pytest.mark.parametrize("raw", ["kecha", "2026-13-40T25:00"])
def test_a_bad_start_time_is_refused(client, admin_user, load, three, raw):
    start(client, admin_user, load, started_at=raw)
    load.refresh_from_db()
    assert load.started_at is None


def test_a_start_in_the_future_is_refused(client, admin_user, load, three):
    later = timezone.localtime() + timedelta(hours=2)
    start(client, admin_user, load, started_at=later.strftime("%Y-%m-%dT%H:%M"))
    load.refresh_from_db()
    assert load.started_at is None


def test_a_start_can_be_undone(client, admin_user, load, three):
    start(client, admin_user, load, started_at="")
    start(client, admin_user, load, clear="1")
    load.refresh_from_db()
    assert load.started_at is None


def test_only_who_may_change_the_load_starts_it(client, other_seller, load, three):
    assert start(client, other_seller, load, started_at="").status_code == 403
    load.refresh_from_db()
    assert load.started_at is None


def test_the_route_shows_arrival_times_from_the_start(client, admin_user, load, three):
    load.started_at = timezone.make_aware(
        timezone.datetime.combine(TODAY(), timezone.datetime.min.time())) + timedelta(hours=9)
    load.save()
    resp = route(client, admin_user, load)
    r = resp.context["routes"][0]
    etas = [s["eta"] for s in r["stops"]]
    assert load.started_at < etas[0] < etas[1] < etas[2] < r["back"]
    # Each stop is at least the 15 minutes at the one before it later.
    assert etas[1] - etas[0] >= timedelta(minutes=15)
    body = resp.content.decode()
    assert "09:00 da jo'nagan" in body
    assert f"≈ {timezone.localtime(etas[0]):%H:%M}" in body


def test_before_the_start_times_count_from_now(client, admin_user, load, three):
    resp = route(client, admin_user, load)
    first = resp.context["routes"][0]["stops"][0]["eta"]
    assert timezone.now() < first < timezone.now() + timedelta(hours=1)
    assert "Hozir jo'nasa" in resp.content.decode()


def test_the_load_page_has_the_start_box(client, admin_user, load, three):
    body = detail(client, admin_user, load).content.decode()
    assert 'name="started_at"' in body and "Jo'natish" in body
    load.started_at = timezone.now()
    load.save()
    body = detail(client, admin_user, load).content.decode()
    assert "Yo'lda" in body and "Bekor qilish" in body


# --- metr / mikron carried onto the sale ---------------------------------------------

def test_a_confirmed_stops_metr_and_mikron_can_be_cleared_on_the_sale(client, admin_user,
                                                                      seller_user, load, paket):
    """✓ copies the stop's metr/mikron to the sale and locks the stop, so the sale edit
    form must offer them — or a wrong "Oq" typed there could never be removed."""
    from crm.forms import SaleItemForm

    paket.has_micron = True
    paket.save()
    item = stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5, 120))
    item.lines.update(micron="Oq")
    save(client, admin_user, load, confirm=str(item.pk))
    line = SaleItem.objects.get()
    assert line.micron == "Oq"

    client.force_login(admin_user)
    body = client.get(f"/sales/{line.sale_id}/edit/").content.decode()
    assert 'name="items-0-micron"' in body and 'value="Oq"' in body

    data = {"product": paket.pk, "dimension": "kg", "weight": "120", "rolls": "5",
            "price": "15000", "cost_price": "10000", "size": "", "micron": ""}
    form = SaleItemForm(data, instance=line)
    assert form.is_valid(), form.errors
    form.save()
    line.refresh_from_db()
    assert line.micron == ""


def test_a_line_without_metr_or_mikron_does_not_offer_them(db, seller_user, paket):
    from crm.forms import SaleItemForm

    sale = Sale.objects.create(client=make_client(seller_user, "Bobur"), sales_rep=seller_user)
    line = SaleItem.objects.create(sale=sale, product=paket, dimension="kg", weight=Decimal("10"),
                                   price=Decimal("15000"), cost_price=Decimal("10000"))
    form = SaleItemForm(instance=line)
    assert "micron" not in form.fields and "size" not in form.fields
    assert "micron" not in SaleItemForm().fields  # a new line never asks either


# --- the day board: how each stop goes and where it stands ----------------------------

TRUCK, PICKUP, YANDEX = "truck", "pickup", "yandex"


def set_status(client, user, *changes, day=None):
    """Press a row's button on the day page for each change: (item, method, status)."""
    for item, method, status in changes:
        resp = day_save(client, user, day=day, quick=f"{item.pk}:{method}:{status}")
    return resp


def test_new_stops_go_by_truck_and_wait(load, seller_user, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    assert (item.method, item.status, item.on_route) == (TRUCK, "new", True)


def test_the_buttons_set_how_it_leaves_and_its_status(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    b = stop(load, make_client(seller_user, "Bobur"), (paket, 3))
    set_status(client, admin_user, (a, PICKUP, "done"), (b, YANDEX, "sent"))
    a.refresh_from_db(), b.refresh_from_db()
    assert (a.method, a.status, a.status_label) == (PICKUP, "done", "Olib ketdi")
    assert (b.status_label, b.on_route) == ("Yandexdan jo'natildi", False)
    assert AuditLog.objects.filter(target_type="Yuk", summary__contains="Olib ketdi").exists()


def day_save(client, user, day=None, **extra):
    """Post the day page's form as it stands, overridden by `extra`."""
    day = day or TODAY()
    client.force_login(user)
    data = {"action": "items", "sana": day.isoformat()}
    for load in Load.objects.filter(date=day):
        data.update({k: v for k, v in form_of(load).items() if k != "action"})
    data.update(extra)
    return client.post("/yetkazish/kun/", data)


def test_a_seller_moves_only_their_own_clients(client, seller_user, other_seller, load, paket):
    mine = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    theirs = stop(load, make_client(other_seller, "Begona"), (paket, 3))
    body = day_page(client, seller_user).content.decode()
    assert f'data-quick="{mine.pk}"' in body and f'data-quick="{theirs.pk}"' not in body
    day_save(client, seller_user, quick=f"{theirs.pk}:pickup:done")   # a forged one: not theirs to see
    day_save(client, seller_user, quick=f"{mine.pk}:pickup:done")
    mine.refresh_from_db(), theirs.refresh_from_db()
    assert (mine.method, mine.status, theirs.status) == ("pickup", "done", "new")


def test_a_step_button_saves_what_was_typed_too(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    line = item.lines.get()
    resp = day_save(client, admin_user, **{f"kg_{line.pk}": "55", "quick": f"{item.pk}:truck:loading"})
    assert resp.url == f"/yetkazish/?sana={TODAY().isoformat()}#stop-{item.pk}"
    line.refresh_from_db(), item.refresh_from_db()
    assert (line.kg, item.status) == (Decimal("55"), "loading")


def test_a_wrong_step_changes_nothing(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    day_save(client, admin_user, quick=f"{item.pk}:truck:flying")
    item.refresh_from_db()
    assert item.status == "new"


def test_the_first_client_of_an_empty_day_makes_its_yuk(client, admin_user, seller_user, paket):
    day = TODAY() + timedelta(days=1)
    c = make_client(seller_user, "Anvar")
    resp = day_save(client, admin_user, day=day, new_client=[c.pk], new_purpose=[DELIVER],
                    new_product=[paket.pk], new_rolls=["5"])
    assert resp.url == f"/yetkazish/?sana={day.isoformat()}"
    item = LoadItem.objects.get()
    assert (item.load.date, item.client, item.method) == (day, c, TRUCK)
    # Nothing typed on an empty day makes no Yuk... but opening it doesn't either.
    day_page(client, admin_user, day + timedelta(days=1))
    assert Load.objects.count() == 1


def test_any_client_of_the_day_is_confirmed_from_the_day_page(client, admin_user, seller_user,
                                                              load, paket):
    second = Load.objects.create(date=TODAY(), created_by=admin_user)
    item = stop(second, make_client(seller_user, "Anvar"), (paket, 3, 70))
    resp = day_save(client, admin_user, confirm=str(item.pk))
    sale = Sale.objects.get()
    assert (sale.date, sale.note) == (TODAY(), f"Yuk #{second.pk}")
    assert resp.url == f"/yetkazish/?sana={TODAY().isoformat()}&ochiq={item.pk}#stop-{item.pk}"


def test_the_status_still_moves_after_the_sale(client, admin_user, load, weighed):
    save(client, admin_user, load, confirm=str(weighed.pk))
    set_status(client, admin_user, (weighed, TRUCK, "done"))
    weighed.refresh_from_db()
    assert weighed.sale and weighed.status == "done"


def test_only_truck_stops_with_goods_in_hand_are_routed(client, admin_user, load, three):
    a, b, c = three
    set_status(client, admin_user, (a, PICKUP, "new"), (b, TRUCK, "order"))
    assert names(route(client, admin_user, load)) == ["C"]
    # Saving the order takes the routed stops only — not «Tartib eskirgan».
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk]})
    load.refresh_from_db()
    assert load.ordered_at is not None


def test_a_pickup_without_a_location_does_not_block_the_route(client, admin_user, seller_user,
                                                               load, three, paket):
    nowhere = stop(load, make_client(seller_user, "Joysiz"), (paket, 1))
    set_status(client, admin_user, (nowhere, PICKUP, "new"))
    resp = route(client, admin_user, load)
    assert resp.context["missing"] == [] and len(names(resp)) == 3


def test_sending_the_truck_marks_its_stops_sent(client, admin_user, load, three):
    a, b, c = three
    set_status(client, admin_user, (a, PICKUP, "new"), (b, TRUCK, "loading"))
    start(client, admin_user, load)
    statuses = dict(load.items.values_list("pk", "status"))
    assert (statuses[a.pk], statuses[b.pk], statuses[c.pk]) == ("new", "sent", "sent")
    start(client, admin_user, load, clear="1")
    assert set(load.items.filter(method=TRUCK).values_list("status", flat=True)) == {"loading"}


# --- a sale typed in Sotuvlar lands on the board ---------------------------------------

def sell(client, user, buyer, product, day=None, weight="120"):
    from crm.tests import one_item, sale_post
    client.force_login(user)
    data = sale_post(buyer.pk, [one_item(product, weight=weight, price="15000")])
    if day:
        data["date"] = day.isoformat()
    return client.post("/sales/new/", data)


def test_a_sale_from_sotuvlar_lands_on_the_board_without_a_method(client, seller_user, paket):
    anvar = make_client(seller_user, "Anvar")
    sell(client, seller_user, anvar, paket)
    sale = Sale.objects.get()
    item = sale.load_item
    assert (item.load.date, item.load.note, item.method, item.status) == (
        TODAY(), "Sotuvlardan", "", "new")
    line = item.lines.get()
    assert (line.product, line.kg, line.price) == (paket, Decimal("120"), Decimal("15000"))
    assert not item.on_route
    assert lane(day_page(client, seller_user), "order") == ["Anvar"]
    assert "Kutilmoqda" in client.get("/sales/").content.decode()


def test_it_joins_the_days_existing_load(client, admin_user, seller_user, load, paket):
    sell(client, seller_user, make_client(seller_user, "Anvar"), paket)
    assert Sale.objects.get().load_item.load == load
    assert Load.objects.count() == 1


def test_a_planned_stop_takes_the_sale_and_its_lines(client, seller_user, load, paket, plyonka):
    anvar = make_client(seller_user, "Anvar")
    planned = stop(load, anvar, (plyonka, 4))
    sell(client, seller_user, anvar, paket, weight="80")
    planned.refresh_from_db()
    assert planned.sale == Sale.objects.get() and planned.method == TRUCK
    assert [(l.product, l.kg) for l in planned.lines.all()] == [(paket, Decimal("80"))]
    assert LoadItem.objects.count() == 1


def test_a_second_sale_the_same_day_gets_its_own_stop(client, seller_user, paket):
    anvar = make_client(seller_user, "Anvar")
    sell(client, seller_user, anvar, paket)
    sell(client, seller_user, anvar, paket)
    assert LoadItem.objects.filter(client=anvar, load__date=TODAY()).count() == 2
    assert Load.objects.filter(date=TODAY()).count() == 2


def test_a_backdated_sale_shows_on_its_own_day(client, admin_user, seller_user, paket):
    """A sale typed in late for an earlier day is on that day's board too, as it is in
    Sotuvlar — and stays there: not carried to today."""
    day = TODAY() - timedelta(days=3)
    sell(client, seller_user, make_client(seller_user, "Anvar"), paket, day=day)
    item = LoadItem.objects.get()
    assert (item.load.date, item.method, item.via_sotuvlar) == (day, "", True)
    assert lane(day_page(client, admin_user, day), "order") == ["Anvar"]
    day_page(client, admin_user)
    item.refresh_from_db()
    assert item.load.date == day


# --- An order its day passed without: today's page asks what happened -------------------

def decide(client, user, item, qaror, **extra):
    """Answer «O'z kunida jo'natilmagan» for one order."""
    client.force_login(user)
    return client.post(f"/yetkazish/qaror/{item.pk}/", {"qaror": qaror, **extra})


def test_yesterdays_unfinished_clients_wait_for_a_decision(client, admin_user, seller_user, paket):
    """Not moved by themselves: they stay on their day and today's page asks; «Bugunga»
    brings one over as «Kechagidan»."""
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1))
    open_one = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3))
    done_one = stop(yesterday, make_client(seller_user, "Bobur"), (paket, 2))
    done_one.status = "done"
    done_one.sale = Sale.objects.create(client=done_one.client, sales_rep=seller_user)
    done_one.save()
    resp = day_page(client, admin_user)
    open_one.refresh_from_db()
    assert open_one.load == yesterday
    assert [w.client.name for w in resp.context["waiting"]] == ["Anvar"]
    body = resp.content.decode()
    # A bell with the count beside the day; the list itself in its window.
    assert 'class="day-bell-count">1<' in body and 'id="waiting-dialog"' in body
    decide(client, admin_user, open_one, "bugun")
    open_one.refresh_from_db(), done_one.refresh_from_db()
    assert open_one.load.date == TODAY() and open_one.carried_from == yesterday.date
    assert done_one.load == yesterday
    resp = day_page(client, admin_user)
    assert lane(resp, "truck") == ["Anvar"] and "Kechagidan" in resp.content.decode()
    assert resp.context["waiting"] == []
    assert LoadItem.objects.filter(load__date=TODAY()).count() == 1


def test_older_days_say_which_day(client, admin_user, seller_user, paket):
    old = Load.objects.create(date=TODAY() - timedelta(days=3))
    item = stop(old, make_client(seller_user, "Anvar"), (paket, 3))
    decide(client, admin_user, item, "bugun")
    item.refresh_from_db()
    assert item.carried_label == f"{old.date:%d.%m} dan"


def test_only_todays_page_asks_and_nothing_moves_by_itself(client, admin_user, seller_user,
                                                           paket):
    yesterday = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
                     make_client(seller_user, "Anvar"), (paket, 3))
    ancient = stop(Load.objects.create(date=TODAY() - timedelta(days=40)),
                   make_client(seller_user, "Qadimiy"), (paket, 3))
    assert day_page(client, admin_user, TODAY() + timedelta(days=1)).context["waiting"] == []
    assert day_page(client, admin_user, TODAY() - timedelta(days=1)).context["waiting"] == []
    waiting = day_page(client, admin_user).context["waiting"]
    assert [w.client.name for w in waiting] == ["Qadimiy", "Anvar"]
    yesterday.refresh_from_db(), ancient.refresh_from_db()
    assert yesterday.load.date == TODAY() - timedelta(days=1)
    assert ancient.load.date == TODAY() - timedelta(days=40)


def test_a_client_already_on_today_takes_the_old_products(client, admin_user, seller_user,
                                                          load, paket, plyonka):
    anvar = make_client(seller_user, "Anvar")
    old = stop(Load.objects.create(date=TODAY() - timedelta(days=1)), anvar, (plyonka, 2))
    today = stop(load, anvar, (paket, 3))
    decide(client, admin_user, old, "bugun")
    assert not LoadItem.objects.filter(pk=old.pk).exists()
    assert sorted(l.product.name for l in today.lines.all()) == ["Paket", "Plyonka"]


def test_a_carried_stop_is_sold_on_the_day_it_is_delivered(client, admin_user, seller_user, paket):
    item = stop(Load.objects.create(date=TODAY() - timedelta(days=2)),
                make_client(seller_user, "Anvar"), (paket, 3, 50))
    decide(client, admin_user, item, "bugun")
    day_save(client, admin_user, confirm=str(item.pk))
    assert Sale.objects.get().date == TODAY()


def test_an_order_is_moved_to_another_day_and_starts_over(client, admin_user, seller_user, paket):
    item = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
                make_client(seller_user, "Anvar"), (paket, 3))
    LoadItem.objects.filter(pk=item.pk).update(status="sent")
    later = TODAY() + timedelta(days=2)
    resp = decide(client, admin_user, item, "kun", sana=later.isoformat())
    assert resp.url == f"/yetkazish/?sana={TODAY().isoformat()}#kutilayotgan"
    item.refresh_from_db()
    assert (item.load.date, item.status) == (later, "new")  # on its way then: planned afresh
    assert item.carried_from == TODAY() - timedelta(days=1)
    # It now shows under today's «Buyurtmalar», for that day.
    groups = day_page(client, admin_user).context["ahead_groups"]
    assert [(g["day"], [i.pk for i in g["rows"]]) for g in groups] == [(later, [item.pk])]
    # A past day is no answer.
    other = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
                 make_client(seller_user, "Bobur"), (paket, 1))
    decide(client, admin_user, other, "kun", sana=(TODAY() - timedelta(days=1)).isoformat())
    other.refresh_from_db()
    assert other.load.date == TODAY() - timedelta(days=1)


def test_an_order_waiting_for_stock_stays_one_when_moved(client, admin_user, seller_user, paket):
    item = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
                make_client(seller_user, "Anvar"), (paket, 3))
    LoadItem.objects.filter(pk=item.pk).update(status="order")
    decide(client, admin_user, item, "bugun")
    item.refresh_from_db()
    assert (item.load.date, item.status) == (TODAY(), "order")


def test_a_cancelled_order_stays_on_its_day(client, admin_user, seller_user, paket):
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1))
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3))
    decide(client, admin_user, item, "bekor")
    item.refresh_from_db()
    assert (item.load, item.status) == (yesterday, "cancel")
    assert day_page(client, admin_user).context["waiting"] == []
    assert AuditLog.objects.filter(target_type="Yuk", target_id=yesterday.pk,
                                   summary__contains="bekor qilindi").exists()


def sold_on_its_day(item, seller):
    """The stop as ✓ left it on its own (earlier) day. ✓ itself is for today's
    clients only, so an earlier day's sold stop is set up directly."""
    sale = Sale.objects.create(client=item.client, sales_rep=seller, date=item.load.date,
                               debt_deadline=item.load.date + timedelta(days=7))
    for line in item.lines.all():
        SaleItem.objects.create(sale=sale, product=line.product, dimension="kg", weight=line.kg,
                                rolls=line.rolls, price=line.price, cost_price=line.product.cost_price)
    item.lines.update(sale=sale)
    LoadItem.objects.filter(pk=item.pk).update(sale=sale)
    return sale


def test_tick_sells_today_and_only_todays_clients(client, admin_user, seller_user, load, paket):
    """The firm's rule: written the day before, weighed and ✓-ed on the day it goes —
    ✓ makes THAT day's sale. So ✓ is only on today's list; an earlier day's client
    comes over first («Bugunga»), a later day's is sold on its day."""
    past = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
                make_client(seller_user, "Kecha"), (paket, 2, 40))
    ahead = stop(Load.objects.create(date=TODAY() + timedelta(days=1)),
                 make_client(seller_user, "Ertaga"), (paket, 2, 40))
    body = day_page(client, admin_user, past.load.date).content.decode()
    assert f'name="confirm" value="{past.pk}"' not in body and "✓ bugungi ro'yxatda" in body
    body = day_page(client, admin_user, ahead.load.date).content.decode()
    assert f'name="confirm" value="{ahead.pk}"' not in body
    resp = day_save(client, admin_user, day=past.load.date, confirm=str(past.pk))
    day_save(client, admin_user, day=ahead.load.date, confirm=str(ahead.pk))
    assert not Sale.objects.exists()
    assert any("Bugunga" in str(m) for m in resp.wsgi_request._messages)
    decide(client, admin_user, past, "bugun")
    day_save(client, admin_user, confirm=str(past.pk))
    assert Sale.objects.get().date == TODAY()


def test_a_sold_order_is_not_cancelled_from_the_question(client, admin_user, seller_user, paket):
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1), created_by=admin_user)
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3, 50))
    sold_on_its_day(item, seller_user)
    body = day_page(client, admin_user).content.decode()
    assert f"/yetkazish/qaror/{item.pk}/" in body and 'value="bekor"' not in body
    decide(client, admin_user, item, "bekor")
    item.refresh_from_db()
    assert item.status == "new"
    # «Yetkazilgan»: it went; sold already, so it is simply done.
    decide(client, admin_user, item, "yetkazildi")
    item.refresh_from_db()
    assert (item.load, item.status) == (yesterday, "done")


def test_delivered_but_not_sold_comes_to_today_to_be_sold(client, admin_user, seller_user, paket):
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1))
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3, 50))
    decide(client, admin_user, item, "yetkazildi")
    item.refresh_from_db()
    assert item.status == "done"
    body = day_page(client, admin_user).content.decode()
    item.refresh_from_db()
    assert item.load.date == TODAY() and "Sotuv yozilmagan" in body


def test_only_the_clients_seller_or_a_manager_decides(client, seller_user, other_seller, manager,
                                                      paket):
    item = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
                make_client(seller_user, "Anvar"), (paket, 3))
    assert day_page(client, other_seller).context["waiting"] == []
    assert decide(client, other_seller, item, "bekor").status_code == 403
    assert [w.pk for w in day_page(client, seller_user).context["waiting"]] == [item.pk]
    decide(client, manager, item, "bugun")
    item.refresh_from_db()
    assert item.load.date == TODAY()
    # Answered once: a second answer (another tab) changes nothing.
    decide(client, seller_user, item, "bekor")
    item.refresh_from_db()
    assert item.status == "new"


def test_a_sotuvlar_sale_lands_on_the_carried_stop(client, seller_user, paket):
    anvar = make_client(seller_user, "Anvar")
    old = stop(Load.objects.create(date=TODAY() - timedelta(days=1)), anvar, (paket, 3))
    sell(client, seller_user, anvar, paket)
    old.refresh_from_db()
    assert old.sale == Sale.objects.get() and old.load.date == TODAY()


# --- saving by itself, and changing a line's product ------------------------------------

def background_save(client, user, **extra):
    client.force_login(user)
    data = {"action": "items", "sana": TODAY().isoformat()}
    for load in Load.objects.filter(date=TODAY()):
        data.update({k: v for k, v in form_of(load).items() if k != "action"})
    data.update(extra)
    return client.post("/yetkazish/kun/", data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")


def test_a_box_saves_by_itself_without_a_page(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    line = item.lines.get()
    resp = background_save(client, admin_user, **{f"kg_{line.pk}": "50+48,5"})
    assert resp.status_code == 200 and resp.json() == {"ok": True, "saved": True}
    line.refresh_from_db()
    assert line.kg == Decimal("98.5")
    assert background_save(client, admin_user).json() == {"ok": True, "saved": False}


def test_a_wrong_box_says_which_without_saving(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    line = item.lines.get()
    resp = background_save(client, admin_user, **{f"kg_{line.pk}": "abc", f"rolls_{line.pk}": "7"})
    assert resp.status_code == 400
    assert resp.json()["bad"] == [f"kg_{line.pk}"]
    line.refresh_from_db()
    assert line.rolls == 3


def test_a_lines_product_can_be_changed(client, admin_user, seller_user, load, paket, plyonka):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    line = item.lines.get()
    plyonka.has_micron = True
    plyonka.save()
    background_save(client, admin_user, **{f"product_{line.pk}": str(plyonka.pk),
                                           f"price_{line.pk}": "26000", f"micron_{line.pk}": "05"})
    line.refresh_from_db()
    assert (line.product, line.price, line.micron) == (plyonka, Decimal("26000"), "05")
    assert AuditLog.objects.filter(summary__contains="Plyonka: 3 rulon").exists()


def test_the_day_page_has_no_save_button_but_adds_clients(client, admin_user, seller_user, load,
                                                         paket):
    stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    body = day_page(client, admin_user).content.decode()
    assert "data-autosave" in body and ">Saqlash<" not in body and "Qo&#x27;shish" not in body
    assert "Qo'shish</button>" in body


def test_a_rows_dropdown_sets_its_status(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    body = day_page(client, admin_user).content.decode()
    assert '<option value="truck:loading">Ortilmoqda</option>' in body
    assert 'value="truck:order">Omborda yo' in body
    day_save(client, admin_user, quick_pick=f"{item.pk}:truck:sent")
    item.refresh_from_db()
    assert item.status == "sent"


def test_kg_and_the_sale_button_sit_on_the_row(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    line = item.lines.get()
    body = day_page(client, admin_user).content.decode()
    summary = body[body.index(f'id="stop-{item.pk}"'):body.index('class="lane-editor"')]
    assert f'name="kg_{line.pk}"' in summary and f'name="confirm" value="{item.pk}"' in summary
    assert body.count(f'name="kg_{line.pk}"') == 1


def test_a_new_client_is_added_picked_up_or_by_yandex(client, admin_user, seller_user, load, paket):
    a, b = make_client(seller_user, "Anvar"), make_client(seller_user, "Bobur")
    day_save(client, admin_user, new_client=[a.pk, b.pk], new_purpose=[DELIVER, DELIVER],
             new_key=["1", "2"], new_method=["pickup", "yandex"],
             new_product=[paket.pk, paket.pk], new_rolls=["2", "3"])
    assert dict(LoadItem.objects.values_list("client__name", "method")) == {
        "Anvar": "pickup", "Bobur": "yandex"}
    assert lane(day_page(client, admin_user), "pickup") == ["Anvar", "Bobur"]


# --- data safety: only what was sent is saved ---------------------------------------------

def test_an_old_page_cannot_put_its_values_over_newer_ones(client, admin_user, seller_user, load,
                                                           paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    b = stop(load, make_client(seller_user, "Bobur"), (paket, 3))
    a_line, b_line = a.lines.get(), b.lines.get()
    LoadLine.objects.filter(pk=b_line.pk).update(kg=Decimal("77"))  # typed elsewhere since
    client.force_login(admin_user)
    resp = client.post("/yetkazish/kun/", {"action": "items", "sana": TODAY().isoformat(),
                                           f"kg_{a_line.pk}": "40"},
                       HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert resp.json() == {"ok": True, "saved": True}
    a_line.refresh_from_db(), b_line.refresh_from_db()
    assert (a_line.kg, b_line.kg) == (Decimal("40"), Decimal("77"))


def test_a_box_not_sent_keeps_its_value_even_within_the_client(client, admin_user, seller_user,
                                                               load, paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    line = a.lines.get()
    LoadLine.objects.filter(pk=line.pk).update(price=Decimal("19000"))
    client.force_login(admin_user)
    client.post("/yetkazish/kun/", {"action": "items", "sana": TODAY().isoformat(),
                                    f"kg_{line.pk}": "12"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    line.refresh_from_db()
    assert (line.kg, line.price, line.rolls) == (Decimal("12"), Decimal("19000"), 3)


def test_one_clients_bad_data_does_not_block_another(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    b = stop(load, make_client(seller_user, "Bobur"), (paket, 3))
    LoadLine.objects.filter(item=b).update(rolls=0)  # e.g. a sale with no rulon count
    a_line = a.lines.get()
    client.force_login(admin_user)
    resp = client.post("/yetkazish/kun/", {"action": "items", "sana": TODAY().isoformat(),
                                           f"kg_{a_line.pk}": "40"},
                       HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert resp.status_code == 200
    resp = client.post("/yetkazish/kun/", {"action": "items", "sana": TODAY().isoformat(),
                                           "quick_pick": f"{a.pk}:truck:loading"})
    a.refresh_from_db()
    assert a.status == "loading"


# --- Sotuvlar and Yetkazish stay in step -------------------------------------------------------

def edit_sale(client, user, sale, **line):
    from crm.tests import one_item, sale_post
    item = sale.items.get()
    row = {**one_item(item.product, weight=str(item.weight), price=str(int(item.price))),
           "id": item.pk, **line}
    data = sale_post(sale.client_id, [row], date=sale.date.isoformat())
    data["items-INITIAL_FORMS"] = "1"
    client.force_login(user)
    return client.post(f"/sales/{sale.pk}/edit/", data)


def test_editing_a_sale_updates_its_stop(client, seller_user, paket):
    sell(client, seller_user, make_client(seller_user, "Anvar"), paket, weight="120")
    sale = Sale.objects.get()
    resp = edit_sale(client, seller_user, sale, weight="95")
    assert resp.status_code in (200, 302)
    assert sale.load_item.lines.get().kg == Decimal("95")


def test_deleting_a_sotuvlar_sale_takes_its_stop(client, seller_user, admin_user, load, paket):
    sell(client, seller_user, make_client(seller_user, "Anvar"), paket)
    sale = Sale.objects.get()
    assert sale.load_item.via_sotuvlar
    client.force_login(admin_user)
    client.post(f"/sales/{sale.pk}/delete/")
    assert not LoadItem.objects.exists()


def test_deleting_a_ticked_sale_unlocks_the_planned_stop(client, admin_user, load, weighed):
    save(client, admin_user, load, confirm=str(weighed.pk))
    client.post(f"/sales/{Sale.objects.get().pk}/delete/")
    weighed.refresh_from_db()
    assert weighed.sale is None and weighed.lines.count() == 2


def test_a_second_sale_the_same_day_is_warned_about(client, seller_user, paket):
    anvar = make_client(seller_user, "Anvar")
    sell(client, seller_user, anvar, paket)
    resp = sell(client, seller_user, anvar, paket)
    msgs = [str(m) for m in resp.wsgi_request._messages]
    assert any("yana bir sotuv bor" in m for m in msgs)



def test_delivered_but_not_sold_comes_to_today_marked(client, admin_user, seller_user, paket):
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1))
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3, 50))
    item.method, item.status = "pickup", "done"
    item.save()
    body = day_page(client, admin_user).content.decode()
    item.refresh_from_db()
    assert item.load.date == TODAY() and "Sotuv yozilmagan" in body


def test_a_sotuvlar_sale_nobody_routed_stays_on_its_day(client, admin_user, seller_user, paket):
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1))
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3, 50))
    item.method, item.via_sotuvlar = "", True
    item.save()
    day_page(client, admin_user)
    item.refresh_from_db()
    assert item.load == yesterday


def test_merging_a_money_plan_into_a_delivery_makes_both(client, admin_user, seller_user, load,
                                                         paket):
    anvar = make_client(seller_user, "Anvar")
    old = stop(Load.objects.create(date=TODAY() - timedelta(days=1)), anvar, purpose=COLLECT)
    today = stop(load, anvar, (paket, 3))
    decide(client, admin_user, old, "bugun")
    today.refresh_from_db()
    assert today.purpose == BOTH


def test_a_sold_stop_from_yesterday_gets_its_own_row_beside_todays(client, admin_user, seller_user,
                                                                   load, paket):
    anvar = make_client(seller_user, "Anvar")
    old = stop(Load.objects.create(date=TODAY() - timedelta(days=1)), anvar, (paket, 3, 40))
    old.sale = Sale.objects.create(client=anvar, sales_rep=seller_user)
    old.save()
    stop(load, anvar, (paket, 2))
    decide(client, admin_user, old, "bugun")
    resp = day_page(client, admin_user)
    old.refresh_from_db()
    assert old.load.date == TODAY() and old.load != load
    assert lane(resp, "truck") == ["Anvar", "Anvar"]


def test_the_route_and_jonatish_cover_every_yuk_of_the_day(client, admin_user, seller_user, load,
                                                           warehouse, paket):
    a = stop(load, make_client(seller_user, "A", 0.01), (paket, 3))
    second = Load.objects.create(date=TODAY())
    b = stop(second, make_client(seller_user, "B", 0.02), (paket, 3))
    assert sorted(names(route(client, admin_user, load))) == ["A", "B"]
    start(client, admin_user, load)
    b.refresh_from_db()
    assert b.status == "sent"


def test_delivered_stops_leave_the_route(client, admin_user, load, three):
    a, b, c = three
    set_status(client, admin_user, (a, TRUCK, "done"))
    assert sorted(names(route(client, admin_user, load))) == ["B", "C"]


def test_a_refused_tick_opens_the_client_and_marks_its_kg(client, admin_user, seller_user, load,
                                                          paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 3))
    resp = day_save(client, admin_user, confirm=str(item.pk))
    assert resp.url == f"/yetkazish/?sana={TODAY().isoformat()}&kgsiz={item.pk}#stop-{item.pk}"
    body = client.get(resp.url).content.decode()
    assert 'class="pick-kg is-bad"' in body


def test_an_empty_add_makes_no_yuk(client, admin_user):
    resp = day_save(client, admin_user, day=TODAY() + timedelta(days=3), add="1")
    assert resp.status_code == 302 and not Load.objects.exists()


def test_cancelled_stops_are_neither_carried_nor_routed(client, admin_user, seller_user, load,
                                                        three, paket):
    a, b, c = three
    set_status(client, admin_user, (a, TRUCK, "cancel"))
    assert sorted(names(route(client, admin_user, load))) == ["B", "C"]
    old = stop(Load.objects.create(date=TODAY() - timedelta(days=1)),
               make_client(seller_user, "Eski"), (paket, 1))
    old.status = "cancel"
    old.save()
    day_page(client, admin_user)
    old.refresh_from_db()
    assert old.load.date == TODAY() - timedelta(days=1)


# --- selling one product at a time --------------------------------------------------------

@pytest.fixture
def two_products(load, seller_user, paket, plyonka):
    return stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 5, 120), (plyonka, 2, 30))


def test_one_product_is_sold_alone_then_the_rest(client, admin_user, two_products, paket, plyonka):
    item = two_products
    bag, film = item.lines.get(product=paket), item.lines.get(product=plyonka)
    resp = day_save(client, admin_user, confirm_line=str(bag.pk))
    first = Sale.objects.get()
    assert [i.product for i in first.items.all()] == [paket]
    bag.refresh_from_db(), film.refresh_from_db(), item.refresh_from_db()
    assert (bag.sale, film.sale, item.sale) == (first, None, None)
    body = client.get(resp.url).content.decode()
    assert f'name="kg_{film.pk}"' in body and f'name="kg_{bag.pk}"' not in body
    assert "✓ Qolganini" in body
    day_save(client, admin_user, confirm=str(item.pk))
    second = Sale.objects.exclude(pk=first.pk).get()
    item.refresh_from_db(), film.refresh_from_db()
    assert [i.product for i in second.items.all()] == [plyonka]
    assert item.sale == second and film.sale == second   # nothing left: the stop is sold


def test_a_sold_product_is_locked(client, admin_user, two_products, paket):
    bag = two_products.lines.get(product=paket)
    day_save(client, admin_user, confirm_line=str(bag.pk))
    day_save(client, admin_user, **{f"kg_{bag.pk}": "999", f"drop_{bag.pk}": "1"})
    bag.refresh_from_db()
    assert bag.kg == Decimal("120") and LoadLine.objects.filter(pk=bag.pk).exists()


def test_selling_one_product_needs_only_its_kg(client, admin_user, seller_user, load, paket,
                                               plyonka):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 5, 120), (plyonka, 2))
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    assert Sale.objects.count() == 1
    resp = day_save(client, admin_user, confirm_line=str(item.lines.get(product=plyonka).pk))
    assert Sale.objects.count() == 1 and "kgsiz" in resp.url


def test_a_partly_sold_client_moves_only_its_unsold_products(client, admin_user, two_products,
                                                             paket, plyonka):
    item = two_products
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    client.post(f"/yetkazish/{item.load_id}/boshqa-kunga/", {"move": [item.pk], "target": "tomorrow"})
    item.refresh_from_db()
    assert list(item.lines.values_list("product__name", flat=True)) == ["Paket"]
    moved = LoadLine.objects.get(product=plyonka)
    assert moved.item != item and moved.item.load.date == TODAY() + timedelta(days=1)


def test_a_partly_sold_client_cannot_be_removed(client, admin_user, two_products, paket):
    item = two_products
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    client.post(f"/yetkazish/{item.load_id}/mijoz/{item.pk}/delete/")
    assert LoadItem.objects.filter(pk=item.pk).exists()


def test_deleting_a_products_sale_frees_it(client, admin_user, two_products, paket):
    bag = two_products.lines.get(product=paket)
    day_save(client, admin_user, confirm_line=str(bag.pk))
    client.post(f"/sales/{Sale.objects.get().pk}/delete/")
    bag.refresh_from_db()
    assert bag.sale is None
    assert f'name="kg_{bag.pk}"' in day_page(client, admin_user).content.decode()


def test_sotuvlar_shows_the_delivery_of_a_product_sale(client, admin_user, two_products, paket):
    day_save(client, admin_user, confirm_line=str(two_products.lines.get(product=paket).pk))
    body = client.get(f"/sales/?dan={TODAY().isoformat()}&gacha={TODAY().isoformat()}").content.decode()
    assert "Kutilmoqda" in body


# --- tabs ----------------------------------------------------------------------------------

def test_tabs_count_and_show_one_lane(client, admin_user, seller_user, load, paket):
    truck = stop(load, make_client(seller_user, "Mashina"), (paket, 1))
    pick = stop(load, make_client(seller_user, "Olib"), (paket, 1))
    yan = stop(load, make_client(seller_user, "Yan"), (paket, 1))
    set_status(client, admin_user, (pick, PICKUP, "new"), (yan, YANDEX, "sent"))
    resp = day_page(client, admin_user)
    # Pickup and Yandex are one tab, «O'zi olib ketadi».
    assert {t["label"]: t["count"] for t in resp.context["tabs"]} == {
        "Hammasi": 3, "Buyurtmalar": 0, "O'zi olib ketadi": 2, "Mashinada": 1}
    day = TODAY().isoformat()
    resp = client.get(f"/yetkazish/?sana={day}&tur=pickup")
    assert (lane(resp, "pickup"), lane(resp, "truck"), resp.context["show_route"]) == (
        ["Olib", "Yan"], [], False)
    # An old «Yandex» link lands on the same tab.
    assert lane(client.get(f"/yetkazish/?sana={day}&tur=yandex"), "pickup") == ["Olib", "Yan"]
    resp = client.get(f"/yetkazish/?sana={day}&tur=truck")
    assert lane(resp, "truck") == ["Mashina"] and resp.context["show_route"]


def test_status_tabs_filter_hammasi(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 1))
    stop(load, make_client(seller_user, "Bobur"), (paket, 1))
    set_status(client, admin_user, (a, TRUCK, "loading"))
    resp = client.get(f"/yetkazish/?sana={TODAY().isoformat()}&holat=loading")
    assert lane(resp, "truck") == ["Anvar"]
    assert {t["label"]: t["count"] for t in resp.context["status_tabs"]}["Kutilmoqda"] == 1


def test_an_action_comes_back_to_its_tab(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 1))
    resp = day_save(client, admin_user, tur="truck", quick_pick=f"{a.pk}:truck:loading")
    assert resp.url == f"/yetkazish/?sana={TODAY().isoformat()}&tur=truck#stop-{a.pk}"
    resp = day_save(client, admin_user, holat="new", confirm=str(a.pk))
    assert "&holat=new" in resp.url and "kgsiz" in resp.url


# --- partly sold: the rest goes to tomorrow ----------------------------------------------

def test_the_rest_goes_to_tomorrow_in_one_click(client, admin_user, two_products, paket, plyonka):
    item = two_products
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    body = day_page(client, admin_user).content.decode()
    assert f'name="ertaga" value="{item.pk}"' in body
    tomorrow = Load.objects.create(date=TODAY() + timedelta(days=1))
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{item.load_id}/boshqa-kunga/",
                       {"ertaga": item.pk, "sana": TODAY().isoformat()})
    assert resp.url.endswith(f"#stop-{item.pk}")
    item.refresh_from_db()
    assert list(item.lines.values_list("product__name", flat=True)) == ["Paket"]
    assert item.sale is not None                           # only the sold one left: sold
    moved = LoadLine.objects.get(product=plyonka).item
    assert moved.load == tomorrow                          # joined tomorrow's Yuk, no new one
    assert Load.objects.filter(date=tomorrow.date).count() == 1


def test_moving_the_rest_keeps_a_money_and_goods_purpose(client, admin_user, two_products, paket):
    item = two_products
    item.purpose = BOTH
    item.save()
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{item.load_id}/boshqa-kunga/", {"ertaga": item.pk})
    item.refresh_from_db()
    assert item.purpose == BOTH


def test_carry_over_leaves_sold_products_on_their_day(client, admin_user, seller_user, paket,
                                                     plyonka):
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1))
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 5, 120), (plyonka, 2))
    sale = Sale.objects.create(client=item.client, sales_rep=seller_user)
    item.lines.filter(product=paket).update(sale=sale)
    decide(client, admin_user, item, "bugun")
    resp = day_page(client, admin_user)
    item.refresh_from_db()
    assert item.load == yesterday and item.sale == sale
    assert list(item.lines.values_list("product__name", flat=True)) == ["Paket"]
    today = LoadLine.objects.get(product=plyonka).item
    assert today.load.date == TODAY() and today.carried_from == yesterday.date
    assert lane(resp, "truck") == ["Anvar"]


# --- audit 2: a sale never wipes another sale's products ----------------------------------

def test_a_sotuvlar_sale_does_not_land_on_a_partly_sold_stop(client, admin_user, seller_user,
                                                            two_products, paket, plyonka):
    item = two_products
    bag = item.lines.get(product=paket)
    day_save(client, admin_user, confirm_line=str(bag.pk))
    first = Sale.objects.get()
    sell(client, seller_user, item.client, plyonka, weight="30")
    bag.refresh_from_db(), item.refresh_from_db()
    assert bag.sale == first and first.delivery_stop == item     # untouched
    assert item.lines.count() == 2 and item.sale is None
    second = Sale.objects.exclude(pk=first.pk).get()
    assert second.delivery_stop != item                         # its own row


def test_editing_the_second_sale_keeps_the_first_ones_product(client, admin_user, seller_user,
                                                             two_products, paket, plyonka):
    item = two_products
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    day_save(client, admin_user, confirm=str(item.pk))
    first, second = Sale.objects.order_by("pk")
    edit_sale(client, admin_user, second, weight="33")
    assert first.load_lines.count() == 1
    assert item.lines.get(sale=second).kg == Decimal("33")


def test_deleting_the_first_sale_opens_the_stop_again(client, admin_user, two_products, paket):
    item = two_products
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    day_save(client, admin_user, confirm=str(item.pk))
    first = Sale.objects.order_by("pk").first()
    client.post(f"/sales/{first.pk}/delete/")
    item.refresh_from_db()
    bag = item.lines.get(product=paket)
    assert bag.sale is None and item.sale is None
    assert f'name="kg_{bag.pk}"' in day_page(client, admin_user).content.decode()


def test_moving_a_sale_to_another_client_frees_its_products(client, admin_user, seller_user, load,
                                                            weighed):
    save(client, admin_user, load, confirm=str(weighed.pk))
    sale = Sale.objects.get()
    other = make_client(seller_user, "Boshqa")
    from crm.tests import one_item, sale_post
    items = list(sale.items.all())
    rows = [{**one_item(i.product, weight=str(i.weight), price=str(int(i.price))), "id": i.pk}
            for i in items]
    data = sale_post(other.pk, rows, date=sale.date.isoformat())
    data["items-INITIAL_FORMS"] = str(len(rows))
    client.post(f"/sales/{sale.pk}/edit/", data)
    weighed.refresh_from_db()
    assert weighed.sale is None and not weighed.lines.filter(sale__isnull=False).exists()


def test_a_carried_sold_stop_stays_sold_when_its_sale_is_edited(client, admin_user, seller_user,
                                                                paket):
    """✓ yesterday before the truck left, carried to today («Kechagidan»): editing the
    sale in Sotuvlar updates the stop — it doesn't open it to be sold a second time."""
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1), created_by=admin_user)
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3, 50))
    sold_on_its_day(item, seller_user)
    sale = Sale.objects.get()
    decide(client, admin_user, item, "bugun")
    item.refresh_from_db()
    assert item.load.date == TODAY() and item.sale == sale
    edit_sale(client, admin_user, sale, weight="55")
    item.refresh_from_db()
    assert item.sale == sale and item.lines.get().sale == sale
    assert item.lines.get().kg == Decimal("55")
    save(client, admin_user, item.load, confirm=str(item.pk))
    assert Sale.objects.count() == 1


def test_putting_a_sales_date_right_keeps_its_stop_sold(client, admin_user, load, weighed):
    save(client, admin_user, load, confirm=str(weighed.pk))
    sale = Sale.objects.get()
    from crm.tests import one_item, sale_post
    rows = [{**one_item(i.product, weight=str(i.weight), price=str(int(i.price))), "id": i.pk}
            for i in sale.items.all()]
    data = sale_post(sale.client_id, rows, date=(TODAY() - timedelta(days=1)).isoformat())
    data["items-INITIAL_FORMS"] = str(len(rows))
    client.post(f"/sales/{sale.pk}/edit/", data)
    sale.refresh_from_db()
    weighed.refresh_from_db()
    assert sale.date == TODAY() - timedelta(days=1)
    assert weighed.sale == sale and not weighed.lines.filter(sale__isnull=True).exists()
    save(client, admin_user, load, confirm=str(weighed.pk))
    assert Sale.objects.count() == 1


def test_a_cancelled_stop_is_not_sold(client, admin_user, load, weighed):
    weighed.status = "cancel"
    weighed.save()
    body = day_page(client, admin_user).content.decode()
    assert f'name="confirm" value="{weighed.pk}"' not in body
    assert 'name="confirm_line"' not in body
    resp = save(client, admin_user, load, confirm=str(weighed.pk))
    assert not Sale.objects.exists()
    assert any("bekor qilingan" in str(m) for m in resp.wsgi_request._messages)


def test_a_sotuvlar_sale_skips_a_cancelled_stop(client, seller_user, load, paket):
    anvar = make_client(seller_user, "Anvar")
    cancelled = stop(load, anvar, (paket, 1))
    cancelled.status = "cancel"
    cancelled.save()
    sell(client, seller_user, anvar, paket)
    cancelled.refresh_from_db()
    assert cancelled.sale is None and Sale.objects.get().delivery_stop != cancelled


# --- audit 2: what is done stays done -------------------------------------------------------

def test_the_rest_goes_tomorrow_and_the_sold_part_stays_done(client, admin_user, two_products,
                                                              paket, plyonka):
    item = two_products
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{item.load_id}/boshqa-kunga/", {"ertaga": item.pk})
    item.refresh_from_db()
    assert item.status == "done"
    # Tomorrow, opening the page doesn't bring the finished part along.
    from unittest import mock
    with mock.patch("django.utils.timezone.localdate", return_value=TODAY() + timedelta(days=1)):
        day_page(client, admin_user, TODAY() + timedelta(days=1))
        day_page(client, admin_user, TODAY() + timedelta(days=1))
    item.refresh_from_db()
    assert item.load.date == TODAY()


def test_a_split_keeps_how_the_rest_leaves(client, admin_user, two_products, paket, plyonka):
    item = two_products
    item.method = "pickup"
    item.save()
    day_save(client, admin_user, confirm_line=str(item.lines.get(product=paket).pk))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{item.load_id}/boshqa-kunga/", {"ertaga": item.pk})
    assert LoadLine.objects.get(product=plyonka).item.method == "pickup"


def test_a_delivered_partly_sold_stop_does_not_send_the_truck_again(client, admin_user,
                                                                    seller_user, paket, plyonka):
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1))
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 5, 120), (plyonka, 2, 30))
    item.status = "done"
    item.save()
    item.lines.filter(product=paket).update(sale=Sale.objects.create(client=item.client,
                                                                     sales_rep=seller_user))
    body = day_page(client, admin_user).content.decode()
    rest = LoadLine.objects.get(product=plyonka).item
    assert rest.status == "done" and not rest.on_route and "Sotuv yozilmagan" in body


def test_lane_numbers_follow_the_days_saved_order(client, admin_user, seller_user, load,
                                                  warehouse, paket):
    a = stop(load, make_client(seller_user, "A", 0.01), (paket, 1))
    second = Load.objects.create(date=TODAY())
    b = stop(second, make_client(seller_user, "B", 0.02), (paket, 1))
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [b.pk, a.pk]})
    assert lane(day_page(client, admin_user), "truck") == ["B", "A"]


# --- audit 2: smaller fixes -----------------------------------------------------------------

def test_a_refused_save_stays_on_its_tab(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 1))
    line = item.lines.get()
    resp = day_save(client, admin_user, tur="truck", **{f"kg_{line.pk}": "abc"})
    assert resp.status_code == 200 and resp.context["tab"] == "truck"
    assert 'name="tur" value="truck"' in resp.content.decode()


def test_a_refused_product_tick_marks_only_its_box(client, admin_user, seller_user, load, paket,
                                                   plyonka):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 1), (plyonka, 1))
    bag, film = item.lines.get(product=paket), item.lines.get(product=plyonka)
    resp = day_save(client, admin_user, confirm_line=str(bag.pk))
    assert f"&kgline={bag.pk}" in resp.url
    body = client.get(resp.url).content.decode()
    assert body.count('class="pick-kg is-bad"') == 1
    assert f'class="pick-kg is-bad" name="kg_{bag.pk}"' in body


def test_nothing_in_the_warehouse_nothing_to_sell(client, admin_user, seller_user, load, paket):
    item = stop(load, make_client(seller_user, "Anvar"), (paket, 1, 50))
    item.status = "order"
    item.save()
    body = day_page(client, admin_user).content.decode()
    assert f'name="confirm" value="{item.pk}"' not in body
    day_save(client, admin_user, confirm=str(item.pk))
    assert not Sale.objects.exists()

# --- «Oxirgi narx» on the load page -------------------------------------------------

def _sold(client_obj, seller, product, price, days_ago=1):
    sale = Sale.objects.create(client=client_obj, sales_rep=seller,
                               date=TODAY() - timedelta(days=days_ago),
                               debt_deadline=TODAY() + timedelta(days=7))
    SaleItem.objects.create(sale=sale, product=product, dimension="kg", weight=Decimal("10"),
                            price=Decimal(price), cost_price=product.cost_price)
    return sale


def test_the_load_page_carries_each_clients_last_price(client, admin_user, seller_user, load, paket):
    """A product picked for a client opens its Narx at what they paid last time
    (load.js reads the map); the added rows under a saved stop know their client."""
    buyer = make_client(seller_user, "Doimiy")
    _sold(buyer, seller_user, paket, "16500", days_ago=5)
    _sold(buyer, seller_user, paket, "17500", days_ago=2)
    item = stop(load, buyer, (paket, 3))
    resp = detail(client, admin_user, load)
    assert resp.status_code == 200
    prices = resp.context["client_last_price_json"]
    assert prices[str(buyer.pk)][str(paket.pk)]["price"] == "17500"
    html = resp.content.decode()
    assert 'id="client-last-price-map"' in html
    assert f'data-item="{item.pk}" data-client="{buyer.pk}"' in html


def test_a_seller_reads_only_their_own_last_prices(client, seller_user, other_seller, load, paket):
    mine = make_client(seller_user, "Meniki")
    theirs = make_client(other_seller, "Boshqaniki")
    _sold(mine, seller_user, paket, "17000")
    _sold(theirs, other_seller, paket, "18000")
    prices = detail(client, seller_user, load).context["client_last_price_json"]
    assert str(mine.pk) in prices
    assert str(theirs.pk) not in prices


# --- 11.11 tuzatishlari: bo'limlar, keyingi kunlar, Sotuvlar bilan bog'liqlik --------------

def test_pickup_and_yandex_are_one_lane(client, admin_user, seller_user, load, paket):
    pick = stop(load, make_client(seller_user, "Olib"), (paket, 1))
    yan = stop(load, make_client(seller_user, "Yan"), (paket, 1))
    set_status(client, admin_user, (pick, PICKUP, "done"), (yan, YANDEX, "sent"))
    resp = day_page(client, admin_user)
    assert lane(resp, "pickup") == ["Olib", "Yan"]
    body = resp.content.decode()
    assert "O'zi olib ketadi" in body and "O'zi olib ketadi va Yandex" not in body


def test_later_days_orders_show_under_buyurtmalar(client, admin_user, seller_user, load, paket):
    """An order taken for another day is seen on today's page, under its day."""
    stop(load, make_client(seller_user, "Bugungi"), (paket, 1))
    tomorrow = Load.objects.create(date=TODAY() + timedelta(days=1))
    later = Load.objects.create(date=TODAY() + timedelta(days=5))
    stop(tomorrow, make_client(seller_user, "Ertangi"), (paket, 2))
    stop(later, make_client(seller_user, "Keyingi"), (paket, 3))
    gone = stop(later, make_client(seller_user, "Bekor"), (paket, 1))
    gone.status = "cancel"
    gone.save()
    resp = day_page(client, admin_user)
    groups = [(g["day"], [i.client.name for i in g["rows"]]) for g in resp.context["ahead_groups"]]
    assert groups == [(tomorrow.date, ["Ertangi"]), (later.date, ["Keyingi"])]
    tabs = {t["label"]: t["count"] for t in resp.context["tabs"]}
    assert tabs["Buyurtmalar"] == 2 and tabs["Hammasi"] == 3
    # The day's own progress and route know nothing of them.
    assert resp.context["total"] == 1 and lane(resp, "truck") == ["Bugungi"]
    # The «Buyurtmalar» tab shows them too; «Mashinada» doesn't.
    day = TODAY().isoformat()
    assert len(client.get(f"/yetkazish/?sana={day}&tur=order").context["ahead_groups"]) == 2
    assert client.get(f"/yetkazish/?sana={day}&tur=truck").context["ahead_groups"] == []
    # A past day's page doesn't list what is coming.
    assert day_page(client, admin_user, TODAY() - timedelta(days=1)).context["ahead_groups"] == []


def test_a_later_days_order_is_edited_from_today(client, admin_user, seller_user, paket):
    """Its boxes and its status save from today's page — even a day with no Yuk yet,
    which is not made by it."""
    tomorrow = Load.objects.create(date=TODAY() + timedelta(days=1))
    item = stop(tomorrow, make_client(seller_user, "Ertangi"), (paket, 2))
    line = item.lines.get()
    body = day_page(client, admin_user).content.decode()
    assert f'name="rolls_{line.pk}"' in body
    assert f'name="confirm" value="{item.pk}"' not in body  # sold on its own day
    resp = client.post("/yetkazish/kun/", {"action": "items", "sana": TODAY().isoformat(),
                                           f"rolls_{line.pk}": "7"},
                       HTTP_X_REQUESTED_WITH="XMLHttpRequest")
    assert resp.json() == {"ok": True, "saved": True}
    line.refresh_from_db()
    assert line.rolls == 7
    assert AuditLog.objects.filter(target_type="Yuk", target_id=tomorrow.pk).exists()
    day_save(client, admin_user, quick_pick=f"{item.pk}:truck:order")
    item.refresh_from_db()
    assert item.status == "order"
    assert not Load.objects.filter(date=TODAY()).exists()


def test_a_client_is_added_to_another_day_from_this_one(client, admin_user, seller_user, load,
                                                        paket):
    anvar = make_client(seller_user, "Anvar")
    stop(load, anvar, (paket, 1))  # on today already: another day is another order
    later = TODAY() + timedelta(days=3)
    new = {"add": "1", "new_sana": later.isoformat(), "new_key": ["1"], "new_client": [anvar.pk],
           "new_purpose": [DELIVER], "new_method": ["truck"], "new_product": [paket.pk],
           "new_rolls": ["4"], "new_price": [""], "new_size": [""], "new_micron": [""]}
    resp = day_save(client, admin_user, **new)
    assert resp.url.startswith(f"/yetkazish/?sana={TODAY().isoformat()}")
    item = LoadItem.objects.get(load__date=later)
    assert (item.client, item.lines.get().rolls) == (anvar, 4)
    assert AuditLog.objects.filter(target_type="Yuk", target_id=item.load_id,
                                   action=AuditLog.Action.CREATE).exists()
    groups = day_page(client, admin_user).context["ahead_groups"]
    assert [i.client.name for g in groups for i in g["rows"]] == ["Anvar"]
    # Twice on that day: refused, nothing added.
    day_save(client, admin_user, **new)
    assert LoadItem.objects.filter(load__date=later).count() == 1


def test_cancelled_and_delivered_stops_are_off_the_trucks_count(client, admin_user, seller_user,
                                                               load, paket, plyonka):
    stop(load, make_client(seller_user, "Boradi"), (paket, 2, 40))
    done = stop(load, make_client(seller_user, "Yetkazildi"), (plyonka, 3, 60))
    gone = stop(load, make_client(seller_user, "Bekor"), (plyonka, 5, 90))
    LoadItem.objects.filter(pk=done.pk).update(status="done")
    LoadItem.objects.filter(pk=gone.pk).update(status="cancel")
    resp = day_page(client, admin_user)
    assert lane(resp, "truck") == ["Boradi", "Yetkazildi"] and lane(resp, "cancel") == ["Bekor"]
    numbers = {i.client.name: i.n for i in resp.context["lanes"]["truck"]}
    assert numbers == {"Boradi": 1, "Yetkazildi": None}
    assert resp.context["truck_kg"] == Decimal("40")
    assert [r["product"] for r in resp.context["roll_breakdown"]] == ["Paket"]
    assert (resp.context["total"], resp.context["done"]) == (2, 1)


def test_buyurtmalar_adds_up_its_products(client, admin_user, seller_user, load, paket):
    waits = stop(load, make_client(seller_user, "Kutadi"), (paket, 4))
    LoadItem.objects.filter(pk=waits.pk).update(status="order")
    stop(Load.objects.create(date=TODAY() + timedelta(days=2)), make_client(seller_user, "Keyin"),
         (paket, 6))
    rows = day_page(client, admin_user).context["order_breakdown"]
    assert [(r["product"], r["rolls"]) for r in rows] == [("Paket", 10)]


def test_a_saved_line_knows_its_client_for_the_last_price(client, admin_user, seller_user, load,
                                                         paket):
    item = stop(load, make_client(seller_user, "Doimiy"), (paket, 3))
    body = day_page(client, admin_user).content.decode()
    assert f'class="stop" data-client="{item.client_id}"' in body


def test_moving_a_stop_on_its_way_starts_it_over(client, admin_user, seller_user, load, paket):
    a = stop(load, make_client(seller_user, "Anvar"), (paket, 2))
    LoadItem.objects.filter(pk=a.pk).update(status="sent")
    move(client, admin_user, load, [a], target="tomorrow")
    a.refresh_from_db()
    assert a.load.date == TODAY() + timedelta(days=1) and a.status == "new"


def test_a_carried_client_still_shows_on_its_own_day(client, admin_user, seller_user, paket):
    """✓ yesterday, not delivered: the client goes on to today, and yesterday's page
    still lists it — with where it is now — as its sale in Sotuvlar is yesterday's."""
    yesterday = Load.objects.create(date=TODAY() - timedelta(days=1), created_by=admin_user)
    item = stop(yesterday, make_client(seller_user, "Anvar"), (paket, 3, 50))
    sold_on_its_day(item, seller_user)
    decide(client, admin_user, item, "bugun")  # today's page: «Bugunga»
    item.refresh_from_db()
    assert item.load.date == TODAY()
    resp = day_page(client, admin_user, yesterday.date)
    assert [m.client.name for m in resp.context["moved_on"]] == ["Anvar"]
    body = resp.content.decode()
    assert f"?sana={TODAY().isoformat()}&amp;ochiq={item.pk}#stop-{item.pk}" in body
    assert "Sotildi" in body and "uchun hali mijoz yo'q" not in body
    # Today's page doesn't list it twice.
    assert day_page(client, admin_user).context["moved_on"] == []


def test_old_stops_go_to_their_sales_day(seller_user, paket, plyonka):
    """Migration 0066: before this release a Yuk had the day its orders were WRITTEN,
    and the goods went — and were sold — the next day. Each old stop goes where its
    sale is: a ✓-ed one to its sale's date; one sold in Sotuvlar that day or up to 2
    days later takes that sale (its lines what was sold) and moves to its date. Both
    are delivered and show where they came from; one with no sale stays cancelled."""
    from importlib import import_module

    from django.apps import apps
    migration = import_module("crm.migrations.0066_link_old_stops_to_their_sales")
    written, went = TODAY() - timedelta(days=2), TODAY() - timedelta(days=1)
    load = Load.objects.create(date=written)
    anvar, bobur, olim, salox = (make_client(seller_user, n) for n in ("Anvar", "Bobur", "Olim", "Salox"))

    def sold(client_obj, day, product, kg, rolls):
        sale = Sale.objects.create(client=client_obj, sales_rep=seller_user, date=day)
        SaleItem.objects.create(sale=sale, product=product, dimension="kg", weight=Decimal(kg),
                                rolls=rolls, price=Decimal("21000"), cost_price=product.cost_price)
        return sale

    next_day = stop(load, anvar, (paket, 1))                    # sold in Sotuvlar the next day
    sale_a = sold(anvar, went, plyonka, "240.9", 5)
    ticked = stop(load, bobur, (paket, 2, 80))                  # ✓-ed the next day
    sale_b = sold(bobur, went, paket, "80", 2)
    LoadItem.objects.filter(pk=ticked.pk).update(sale=sale_b, status="done")
    ticked.lines.update(sale=sale_b)
    same_day = stop(load, olim, (paket, 1))                     # sold the same day
    sale_o = sold(olim, written, paket, "40", 1)
    nothing = stop(load, salox, (paket, 1))                     # never sold
    later = sold(salox, written + timedelta(days=4), paket, "30", 1)  # too late to be its sale
    for s in (next_day, same_day, nothing):
        LoadItem.objects.filter(pk=s.pk).update(status="cancel")  # as 0062 leaves them
    migration.put_stops_on_their_sales_day(apps, None)
    for s in (next_day, ticked, same_day, nothing):
        s.refresh_from_db()
    assert (next_day.sale, next_day.load.date, next_day.carried_from, next_day.status) == (
        sale_a, went, written, "done")
    line = next_day.lines.get()
    assert (line.product, line.kg, line.rolls, line.sale) == (plyonka, Decimal("240.9"), 5, sale_a)
    assert (ticked.load.date, ticked.carried_from, ticked.status) == (went, written, "done")
    assert next_day.load == ticked.load  # the day's one Yuk takes both
    assert (same_day.sale, same_day.load, same_day.status) == (sale_o, load, "done")
    assert (nothing.sale, nothing.load, nothing.status) == (None, load, "cancel")
    assert later.delivery_stop is None


def test_adding_on_todays_page_goes_to_tomorrow(client, admin_user, load):
    """Orders are written the day before they go: on today's page «Mijoz qo'shish»
    opens at tomorrow; on another day's page, at that day."""
    tomorrow = (TODAY() + timedelta(days=1)).isoformat()
    resp = day_page(client, admin_user)
    assert resp.context["new_day"] == tomorrow
    assert f'name="new_sana" value="{tomorrow}" data-default="{tomorrow}"' in resp.content.decode()
    later = TODAY() + timedelta(days=3)
    assert day_page(client, admin_user, later).context["new_day"] == later.isoformat()


def test_older_sales_are_put_on_the_board_by_command(seller_user, paket):
    """track_sales_delivery: the sales typed before the board tracked them get their
    stop (today and later unless --dan says otherwise); opening balances and sales
    already on the board are left alone, so it can run again."""
    from django.core.management import call_command

    def untracked(name, day, **extra):
        sale = Sale.objects.create(client=make_client(seller_user, name), sales_rep=seller_user,
                                   date=day, **extra)
        SaleItem.objects.create(sale=sale, product=paket, dimension="kg", weight=Decimal("30"),
                                price=Decimal("15000"), cost_price=paket.cost_price)
        return sale

    today_sale = untracked("Bugun", TODAY())
    old_sale = untracked("Kecha", TODAY() - timedelta(days=1))
    opening = untracked("Qoldiq", TODAY(), is_opening=True)
    call_command("track_sales_delivery", stdout=io.StringIO())
    assert today_sale.delivery_stop.load.date == TODAY()
    assert Sale.objects.get(pk=old_sale.pk).delivery_stop is None
    call_command("track_sales_delivery", dan=(TODAY() - timedelta(days=1)).isoformat(),
                 stdout=io.StringIO())
    stop_ = Sale.objects.get(pk=old_sale.pk).delivery_stop
    assert (stop_.load.date, stop_.method, stop_.via_sotuvlar) == (old_sale.date, "", True)
    assert Sale.objects.get(pk=opening.pk).delivery_stop is None
    count = LoadItem.objects.count()
    call_command("track_sales_delivery", dan=(TODAY() - timedelta(days=1)).isoformat(),
                 stdout=io.StringIO())
    assert LoadItem.objects.count() == count


def test_moving_a_sotuvlar_sale_to_an_earlier_day_moves_its_stop(client, admin_user, seller_user,
                                                                 paket):
    sell(client, seller_user, make_client(seller_user, "Anvar"), paket)
    sale = Sale.objects.get()
    from crm.tests import one_item, sale_post
    it = sale.items.get()
    row = {**one_item(it.product, weight=str(it.weight), price=str(int(it.price))), "id": it.pk}
    earlier = TODAY() - timedelta(days=2)
    data = sale_post(sale.client_id, [row], date=earlier.isoformat())
    data["items-INITIAL_FORMS"] = "1"
    client.force_login(admin_user)
    client.post(f"/sales/{sale.pk}/edit/", data)
    assert Sale.objects.get().delivery_stop.load.date == earlier


def test_each_line_says_whether_the_location_is_saved(client, admin_user, seller_user, load,
                                                      paket):
    stop(load, make_client(seller_user, "Joyli", 0.01), (paket, 1))
    stop(load, make_client(seller_user, "Joysiz"), (paket, 1))
    body = day_page(client, admin_user).content.decode()
    assert body.count('class="loc-flag"') == 1 and body.count('class="loc-flag is-missing"') == 1
    assert "joylashuv yo&#x27;q" in body or "joylashuv yo'q" in body


def test_adding_a_missing_location_opens_the_clients_form(client, admin_user, seller_user, load,
                                                          paket):
    """«Joylashuv qo'shish» opened the map page, which is a 404 for a client without one."""
    joysiz = make_client(seller_user, "Joysiz")
    stop(load, joysiz, (paket, 1))
    body = day_page(client, admin_user).content.decode()
    assert f'href="/clients/{joysiz.pk}/edit/" data-modal>Joylashuv qo' in body
    assert client.get(f"/clients/{joysiz.pk}/edit/").status_code == 200


def test_each_line_says_how_much_of_it_is_sold(client, admin_user, seller_user, load, paket,
                                                plyonka):
    """The client buttons are painted from this: sold, partly sold, still an order."""
    sold = stop(load, make_client(seller_user, "Sotilgan"), (paket, 1, 10))
    part = stop(load, make_client(seller_user, "Qisman"), (paket, 1, 10), (plyonka, 1, 5))
    stop(load, make_client(seller_user, "Buyurtma"), (paket, 1))
    day_save(client, admin_user, confirm=str(sold.pk))
    day_save(client, admin_user, confirm_line=str(part.lines.get(product=paket).pk))
    body = day_page(client, admin_user).content.decode()
    assert body.count('data-sold="all"') == 1 and body.count('data-sold="part"') == 1
    assert body.count('data-sold="none"') == 1



def test_the_route_and_jonatish_are_for_admins_and_managers(client, seller_user, manager, load,
                                                            warehouse, paket):
    stop(load, make_client(seller_user, "Anvar", 0.01), (paket, 1))
    client.force_login(seller_user)
    assert client.get(f"/yetkazish/{load.pk}/marshrut/").status_code == 403
    assert client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": []}).status_code == 403
    assert client.post(f"/yetkazish/{load.pk}/", {"action": "reset"}).status_code == 403
    assert client.post(f"/yetkazish/{load.pk}/", {"action": "strategy", "strategy": "fast"}).status_code == 403
    assert day_page(client, seller_user).context["show_route"] is False
    assert day_page(client, manager).context["show_route"] is True


def test_a_sellers_counts_and_buttons_are_their_own(client, seller_user, other_seller, load, paket):
    stop(load, make_client(seller_user, "Meniki"), (paket, 1))
    stop(load, make_client(other_seller, "Begona"), (paket, 1))
    resp = day_page(client, seller_user)
    assert {t["label"]: t["count"] for t in resp.context["tabs"]}["Hammasi"] == 1
