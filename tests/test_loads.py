"""Yetkazish as loads (yuk): an admin or manager puts clients and rolls into a load
by hand — kg once weighed — and gets a route through them. Not tied to sales.
The route maths is covered in test_routing.py, the road service in test_road.py."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.models import User
from crm import road
from crm.models import AuditLog, Client, Load, LoadItem, Warehouse

TODAY = timezone.localdate


@pytest.fixture
def warehouse(db):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.300000"), Decimal("69.240000")
    w.save()
    return w


@pytest.fixture
def manager(db):
    return User.objects.create_user(username="mgr", password="x", role=User.Role.MANAGER)


def make_client(owner, name, dlng=None):
    loc = {} if dlng is None else {"latitude": Decimal("41.300000"),
                                   "longitude": Decimal("69.240000") + Decimal(str(dlng))}
    return Client.objects.create(name=name, owner=owner, address=f"{name} ko'chasi", **loc)


@pytest.fixture
def load(db, admin_user):
    return Load.objects.create(date=TODAY(), created_by=admin_user)


def put(load, client, rolls=10, kg=None):
    return LoadItem.objects.create(load=load, client=client, rolls=rolls,
                                   kg=None if kg is None else Decimal(str(kg)))


def get(client, user, url):
    client.force_login(user)
    return client.get(url)


def detail(client, user, load):
    return get(client, user, f"/yetkazish/{load.pk}/")


def route(client, user, load):
    """The «Marshrut tuzish» fragment for the load."""
    client.force_login(user)
    return client.get(f"/yetkazish/{load.pk}/marshrut/")


def names(resp, route=0):
    return [s["client"].name for s in resp.context["routes"][route]["stops"]]


# --- who ----------------------------------------------------------------------------

def test_sellers_cannot_reach_loads(client, seller_user):
    assert get(client, seller_user, "/yetkazish/").status_code == 403
    assert client.post("/yetkazish/yangi/", {"sana": TODAY().isoformat()}).status_code == 403
    assert not Load.objects.exists()


def test_sellers_cannot_open_or_change_a_load(client, seller_user, load):
    assert detail(client, seller_user, load).status_code == 403
    client.force_login(seller_user)
    assert client.post(f"/yetkazish/{load.pk}/delete/").status_code == 403
    assert Load.objects.filter(pk=load.pk).exists()


def test_manager_works_with_loads(client, manager, load, warehouse):
    assert get(client, manager, "/yetkazish/").status_code == 200
    assert detail(client, manager, load).status_code == 200


def test_sidebar_shows_yetkazish_to_admins_and_managers_only(client, admin_user, manager, seller_user):
    for user, shown in ((admin_user, True), (manager, True), (seller_user, False)):
        assert ('href="/yetkazish/"' in get(client, user, "/").content.decode()) is shown


# --- the day's loads ----------------------------------------------------------------

def test_one_click_makes_a_load_for_the_day_and_opens_it(client, admin_user):
    client.force_login(admin_user)
    tomorrow = TODAY() + timedelta(days=1)
    resp = client.post("/yetkazish/yangi/", {"sana": tomorrow.isoformat()})
    load = Load.objects.get()
    assert resp.status_code == 302 and resp.url == f"/yetkazish/{load.pk}/"
    assert (load.date, load.note, load.created_by) == (tomorrow, "", admin_user)
    assert AuditLog.objects.filter(target_type="Yuk", target_id=load.pk).exists()


def test_no_pop_up_and_no_truck_to_pick(client, admin_user, load):
    # «Yangi yuk» is a plain form posting the day, not a modal link.
    body = get(client, admin_user, "/yetkazish/").content.decode()
    assert 'action="/yetkazish/yangi/"' in body and 'href="/yetkazish/yangi/' not in body
    # Editing a load later asks for the date and a note — never a truck.
    form = get(client, admin_user, f"/yetkazish/{load.pk}/edit/").context["form"]
    assert list(form.fields) == ["date", "note"]


def test_opening_the_create_address_just_goes_back_to_the_list(client, admin_user):
    resp = get(client, admin_user, "/yetkazish/yangi/")
    assert resp.status_code == 302 and resp.url == "/yetkazish/" and not Load.objects.exists()


def test_a_day_can_have_several_loads(client, admin_user, seller_user):
    a = Load.objects.create(date=TODAY())
    b = Load.objects.create(date=TODAY())
    Load.objects.create(date=TODAY() - timedelta(days=1))
    put(a, make_client(seller_user, "X", 0.01), rolls=4, kg=100)
    put(a, make_client(seller_user, "Y", 0.02), rolls=6)
    loads = list(get(client, admin_user, "/yetkazish/").context["loads"])
    assert [l.pk for l in loads] == [a.pk, b.pk]
    assert (loads[0].client_count, loads[0].roll_total, loads[0].kg_total, loads[0].unweighed) == (
        2, 10, Decimal(100), 1)


def test_an_empty_day_says_so(client, admin_user):
    resp = get(client, admin_user, "/yetkazish/")
    assert list(resp.context["loads"]) == []
    assert "uchun yuk yo" in resp.content.decode()


def test_editing_a_load(client, admin_user, load):
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/edit/", {"date": TODAY().isoformat(), "note": "kech"})
    load.refresh_from_db()
    assert load.note == "kech"


def test_deleting_a_load_takes_its_clients_too(client, admin_user, seller_user, load):
    put(load, make_client(seller_user, "X", 0.01))
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/delete/")
    assert resp.status_code == 302 and resp.url == f"/yetkazish/?sana={TODAY().isoformat()}"
    assert not Load.objects.exists() and not LoadItem.objects.exists()


# --- clients, rolls, kg -------------------------------------------------------------

def add_rows(client, user, load, *rows):
    """Post Yuk tarkibi with new rows only: (client or "", rulon as typed)."""
    client.force_login(user)
    return client.post(f"/yetkazish/{load.pk}/", {
        "action": "items",
        "new_client": [c.pk if c else "" for c, _ in rows],
        "new_rolls": [r for _, r in rows],
    })


def test_the_page_offers_only_clients_not_yet_in_the_load(client, admin_user, seller_user, load):
    a = make_client(seller_user, "Anvar", 0.01)
    b = make_client(seller_user, "Botir", 0.02)
    put(load, a)
    resp = detail(client, admin_user, load)
    assert resp.context["clients"] == [b]
    body = resp.content.decode()
    assert 'name="new_client"' in body and 'name="new_rolls"' in body
    assert "data-new-row-add" in body and "<template data-new-row-template>" in body


def test_several_new_rows_are_added_in_one_save(client, admin_user, seller_user, load):
    a = make_client(seller_user, "Anvar", 0.01)
    c = make_client(seller_user, "Davron", 0.03)
    resp = add_rows(client, admin_user, load, (a, "12"), (None, ""), (c, " 3 "))
    assert resp.status_code == 302 and resp.url == f"/yetkazish/{load.pk}/"
    assert {(i.client.name, i.rolls, i.kg) for i in load.items.all()} == {
        ("Anvar", 12, None), ("Davron", 3, None)}
    assert AuditLog.objects.filter(target_type="Yuk", summary__contains="+ Anvar 12").exists()


@pytest.mark.parametrize("bad", ["", "0", "-2", "3.5", "abc"])
def test_a_new_row_without_a_good_rulon_saves_nothing(client, admin_user, seller_user, load, bad):
    a = make_client(seller_user, "Anvar", 0.01)
    b = make_client(seller_user, "Botir", 0.02)
    add_rows(client, admin_user, load, (a, "12"), (b, bad))
    assert not LoadItem.objects.exists()


def test_a_rulon_without_a_client_saves_nothing(client, admin_user, seller_user, load):
    a = make_client(seller_user, "Anvar", 0.01)
    add_rows(client, admin_user, load, (a, "12"), (None, "5"))
    assert not LoadItem.objects.exists()


def test_the_same_client_twice_saves_nothing(client, admin_user, seller_user, load):
    a = make_client(seller_user, "Anvar", 0.01)
    add_rows(client, admin_user, load, (a, "12"), (a, "3"))
    assert not LoadItem.objects.exists()
    put(load, a, rolls=4)
    add_rows(client, admin_user, load, (a, "9"))  # a stale tab posting it again
    assert [(i.client.name, i.rolls) for i in load.items.all()] == [("Anvar", 4)]


def test_the_same_client_can_be_in_another_load(client, admin_user, seller_user, load):
    c = make_client(seller_user, "Anvar", 0.01)
    put(Load.objects.create(date=TODAY()), c)
    add_rows(client, admin_user, load, (c, "3"))
    assert load.items.filter(client=c, rolls=3).exists()


def test_new_rows_and_edits_are_saved_together(client, admin_user, seller_user, load):
    a = put(load, make_client(seller_user, "Anvar", 0.01), rolls=12)
    b = make_client(seller_user, "Botir", 0.02)
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {
        "action": "items", f"rolls_{a.pk}": "12", f"kg_{a.pk}": "300",
        "new_client": [b.pk], "new_rolls": ["7"],
    })
    a.refresh_from_db()
    assert a.kg == Decimal("300") and load.items.get(client=b).rolls == 7


def save_items(client, user, load, **rows):
    """Post the Yuk tarkibi table: rows are item=(rolls, kg) as typed."""
    client.force_login(user)
    data = {"action": "items"}
    for item, (rolls, kg) in rows.values():
        data[f"rolls_{item.pk}"], data[f"kg_{item.pk}"] = rolls, kg
    return client.post(f"/yetkazish/{load.pk}/", data)


def test_rolls_and_kg_are_edited_in_the_table(client, admin_user, seller_user, load, warehouse):
    a = put(load, make_client(seller_user, "Anvar", 0.01), rolls=12)
    b = put(load, make_client(seller_user, "Botir", 0.02), rolls=5)
    resp = save_items(client, admin_user, load, a=(a, ("12", "340,5")), b=(b, ("6", "")))
    assert resp.status_code == 302
    a.refresh_from_db()
    b.refresh_from_db()
    assert (a.rolls, a.kg, b.rolls, b.kg) == (12, Decimal("340.5"), 6, None)
    resp = detail(client, admin_user, load)
    assert resp.context["kg_total"] == Decimal("340.5") and resp.context["unweighed"] == 1


@pytest.mark.parametrize("rolls,kg", [("0", ""), ("", "10"), ("2", "-5"), ("2", "abc")])
def test_a_wrong_box_in_the_table_saves_nothing(client, admin_user, seller_user, load, rolls, kg):
    a = put(load, make_client(seller_user, "Anvar", 0.01), rolls=12)
    b = put(load, make_client(seller_user, "Botir", 0.02), rolls=5)
    save_items(client, admin_user, load, a=(a, ("20", "100")), b=(b, (rolls, kg)))
    a.refresh_from_db()
    assert (a.rolls, a.kg) == (12, None)  # the good row wasn't half-saved either


def test_kg_can_be_cleared_again(client, admin_user, seller_user, load):
    a = put(load, make_client(seller_user, "Anvar", 0.01), rolls=12, kg=300)
    save_items(client, admin_user, load, a=(a, ("12", "")))
    a.refresh_from_db()
    assert a.kg is None


def test_removing_a_client(client, admin_user, seller_user, load):
    item = put(load, make_client(seller_user, "Anvar", 0.01))
    client.force_login(admin_user)
    assert client.post(f"/yetkazish/{load.pk}/mijoz/{item.pk}/delete/").status_code == 302
    assert not LoadItem.objects.exists()


def test_a_row_of_another_load_cannot_be_touched_through_this_one(client, admin_user, seller_user, load):
    other = put(Load.objects.create(date=TODAY()), make_client(seller_user, "Anvar", 0.01))
    client.force_login(admin_user)
    assert client.post(f"/yetkazish/{load.pk}/mijoz/{other.pk}/delete/").status_code == 404


# --- the route ----------------------------------------------------------------------

@pytest.fixture
def three(load, seller_user, warehouse):
    return [put(load, make_client(seller_user, n, d), rolls=10)
            for n, d in (("A", 0.03), ("B", 0.01), ("C", 0.02))]


def test_route_is_suggested_from_the_warehouse_outward(client, admin_user, load, three):
    resp = route(client, admin_user, load)
    assert names(resp) == ["B", "C", "A"]
    assert resp.context["km"] > 0 and resp.context["map_data"]["depot"] == [41.3, 69.24]
    assert "Taklif" in resp.content.decode()


def test_drivers_message_has_rolls_and_kg_when_known(client, admin_user, load, three):
    three[1].kg = Decimal("250")
    three[1].save()
    share = route(client, admin_user, load).context["routes"][0]["share"]
    assert share["text"].startswith(f"Yuk #{load.pk} — {TODAY():%d.%m.%Y}\n")
    assert "1. B — 10 rulon · 250 kg" in share["text"]
    assert "2. C — 10 rulon\n" in share["text"]


def test_saving_the_order(client, admin_user, load, three):
    a, b, c = three
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk, a.pk, b.pk]})
    assert resp.status_code == 302
    load.refresh_from_db()
    assert load.ordered_at is not None
    assert names(route(client, admin_user, load)) == ["C", "A", "B"]


def test_a_client_added_after_saving_joins_the_end(client, admin_user, seller_user, load, three):
    a, b, c = three
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk, a.pk, b.pk]})
    late = make_client(seller_user, "Kechikkan", 0.005)
    add_rows(client, admin_user, load, (late, "2"))
    assert names(route(client, admin_user, load)) == ["C", "A", "B", "Kechikkan"]


@pytest.mark.parametrize("stops", [["999999"], ["abc"], []])
def test_a_stale_or_broken_order_saves_nothing(client, admin_user, load, three, stops):
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": stops})
    load.refresh_from_db()
    assert load.ordered_at is None


def test_the_same_stop_twice_saves_nothing(client, admin_user, load, three):
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [three[0].pk, three[0].pk]})
    load.refresh_from_db()
    assert load.ordered_at is None


def test_reset_gives_a_fresh_suggestion(client, admin_user, load, three):
    a, b, c = three
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk, a.pk, b.pk]})
    client.post(f"/yetkazish/{load.pk}/", {"action": "reset"})
    load.refresh_from_db()
    assert load.ordered_at is None and not load.items.exclude(position=None).exists()
    assert names(route(client, admin_user, load)) == ["B", "C", "A"]


def test_missing_locations_hold_the_route_back(client, admin_user, seller_user, load, three):
    nowhere = make_client(seller_user, "Joysiz")
    put(load, nowhere)
    resp = route(client, admin_user, load)
    assert resp.context["routes"] == [] and resp.context["missing"] == [nowhere]
    assert f"/clients/{nowhere.pk}/edit/" in detail(client, admin_user, load).content.decode()


def test_no_warehouse_location_is_explained(client, admin_user, seller_user, load):
    put(load, make_client(seller_user, "A", 0.01))
    resp = route(client, admin_user, load)
    assert resp.context["routes"] == [] and "Ombor joylashuvi" in resp.context["error"]


def test_ids_are_written_without_a_thousands_separator(client, admin_user, seller_user, load, warehouse):
    c = make_client(seller_user, "Katta", 0.01)
    LoadItem.objects.create(pk=1500, load=load, client=c, rolls=1)
    assert 'name="stop" value="1500"' in route(client, admin_user, load).content.decode()


def test_client_names_cannot_break_out_of_the_map_data(client, admin_user, seller_user, load, warehouse):
    put(load, make_client(seller_user, "</script><b>x", 0.01))
    assert "</script><b>x" not in route(client, admin_user, load).content.decode()


# --- by road ------------------------------------------------------------------------

@pytest.fixture
def road_key(settings):
    cache.clear()
    settings.OPEN_ROUTE_API_KEY = "test-key"
    yield
    cache.clear()


def test_by_road_the_order_follows_road_distance(client, admin_user, seller_user, load, warehouse,
                                                 road_key, monkeypatch):
    # In a straight line "Yaqin" is nearer the warehouse, but by road "Uzoq" is.
    put(load, make_client(seller_user, "Yaqin", 0.01))
    put(load, make_client(seller_user, "Uzoq", 0.03))
    road_km = {("W", "Yaqin"): 9.0, ("W", "Uzoq"): 2.0, ("Yaqin", "Uzoq"): 3.0}

    def fake_matrix(points):
        names_at = {(41.3, 69.24): "W", (41.3, 69.25): "Yaqin", (41.3, 69.27): "Uzoq"}
        labels = [names_at[(round(p[0], 4), round(p[1], 4))] for p in points]
        return [[0 if a == b else road_km.get((a, b), road_km.get((b, a))) for b in labels]
                for a in labels]

    monkeypatch.setattr(road, "matrix_km", fake_matrix)
    monkeypatch.setattr(road, "path", lambda points: [[41.3, 69.24], [41.31, 69.26], [41.3, 69.24]])
    resp = route(client, admin_user, load)
    assert resp.context["by_road"] and names(resp) == ["Uzoq", "Yaqin"]
    assert resp.context["km"] == pytest.approx(2 + 3 + 9)
    assert resp.context["map_data"]["routes"][0]["path"][1] == [41.31, 69.26]


def test_when_the_road_service_fails_the_route_still_comes(client, admin_user, seller_user, load,
                                                           warehouse, road_key, monkeypatch):
    put(load, make_client(seller_user, "A", 0.01))

    def broken(points):
        raise road.RoadError("down")

    monkeypatch.setattr(road, "matrix_km", broken)
    resp = route(client, admin_user, load)
    assert not resp.context["by_road"] and len(resp.context["routes"][0]["stops"]) == 1
    assert resp.context["road_note"].startswith("Yo'l masofalarini olib bo'lmadi")



# --- trucks, chosen by the system from kg -----------------------------------------

from crm.models import Truck  # noqa: E402


@pytest.fixture
def trucks(db):
    return (Truck.objects.create(name="Labo", capacity_kg=1000),
            Truck.objects.create(name="Isuzu", capacity_kg=3000))


def test_no_truck_until_everyone_is_weighed(client, admin_user, seller_user, load, warehouse, trucks):
    put(load, make_client(seller_user, "A", 0.01), kg=200)
    put(load, make_client(seller_user, "B", 0.02))  # not weighed yet
    resp = route(client, admin_user, load)
    assert len(resp.context["routes"]) == 1 and resp.context["routes"][0]["truck"] is None
    body = resp.content.decode()
    assert "Hali tanlanmagan" in body and "1 ta mijoz tortilmagan" in body


def test_once_weighed_the_smallest_truck_that_fits_is_chosen(client, admin_user, seller_user, load,
                                                             warehouse, trucks):
    labo, isuzu = trucks
    put(load, make_client(seller_user, "A", 0.01), kg=300)
    put(load, make_client(seller_user, "B", 0.02), kg=400)
    routes = route(client, admin_user, load).context["routes"]
    assert [r["truck"] for r in routes] == [labo]
    assert routes[0]["kg"] == Decimal(700) and not routes[0]["over"]
    assert "Labo" in routes[0]["share"]["title"]


def test_too_heavy_for_one_truck_is_split_across_two(client, admin_user, seller_user, load,
                                                     warehouse, db):
    Truck.objects.create(name="A", capacity_kg=1000)
    Truck.objects.create(name="B", capacity_kg=1000)
    for name, dlng in (("G1", -0.05), ("G2", -0.04), ("S1", 0.04), ("S2", 0.05)):
        put(load, make_client(seller_user, name, dlng), kg=450)
    routes = route(client, admin_user, load).context["routes"]
    assert len(routes) == 2
    assert all(r["kg"] <= r["truck"].capacity_kg for r in routes)
    assert sorted(sorted(s["client"].name[0] for s in r["stops"]) for r in routes) == [
        ["G", "G"], ["S", "S"]]  # each truck one side of the city


def test_more_than_the_trucks_can_take_is_explained(client, admin_user, seller_user, load,
                                                    warehouse, trucks):
    put(load, make_client(seller_user, "A", 0.01), kg=2500)
    put(load, make_client(seller_user, "B", 0.02), kg=2000)
    resp = route(client, admin_user, load)
    assert "ortiqcha" in resp.context["error"]
    # The route is still drawn, just without a truck.
    assert len(resp.context["routes"]) == 1 and resp.context["routes"][0]["truck"] is None


def test_the_saved_order_holds_inside_each_truck(client, admin_user, seller_user, load,
                                                 warehouse, trucks):
    a = put(load, make_client(seller_user, "A", 0.01), kg=100)
    b = put(load, make_client(seller_user, "B", 0.02), kg=100)
    c = put(load, make_client(seller_user, "C", 0.03), kg=100)
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk, a.pk, b.pk]})
    resp = route(client, admin_user, load)
    assert resp.context["routes"][0]["truck"] == trucks[0]
    assert names(resp) == ["C", "A", "B"]


@pytest.mark.parametrize("kg,shown", [("160", "160"), ("340.5", "340.5"), ("1000", "1000"), ("0.25", "0.25")])
def test_the_kg_box_shows_plain_numbers(client, admin_user, seller_user, load, kg, shown):
    put(load, make_client(seller_user, "Anvar", 0.01), kg=kg)
    assert f'value="{shown}"' in detail(client, admin_user, load).content.decode()



# --- «Marshrut tuzish» --------------------------------------------------------------

def test_the_page_itself_asks_no_route(client, admin_user, seller_user, load, warehouse, monkeypatch):
    put(load, make_client(seller_user, "A", 0.01))
    called = []
    monkeypatch.setattr("crm.routing.order_stops", lambda *a, **k: called.append(1))
    resp = detail(client, admin_user, load)
    assert called == [] and "data-route-build" in resp.content.decode()
    assert "data-auto" not in resp.content.decode()  # nothing saved yet: wait for the button


def test_a_saved_order_opens_the_route_by_itself(client, admin_user, seller_user, load, warehouse):
    a = put(load, make_client(seller_user, "A", 0.01))
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [a.pk]})
    assert resp.url == f"/yetkazish/{load.pk}/?marshrut=1"
    assert "data-auto" in detail(client, admin_user, load).content.decode()


def test_an_empty_load_has_no_route_button(client, admin_user, load):
    assert "data-route-build" not in detail(client, admin_user, load).content.decode()


def test_sellers_cannot_fetch_a_route(client, seller_user, load):
    assert route(client, seller_user, load).status_code == 403


def test_the_day_list_deletes_a_load(client, admin_user, load):
    body = get(client, admin_user, "/yetkazish/").content.decode()
    assert f'href="/yetkazish/{load.pk}/delete/"' in body


def test_telegram_sends_yandex_links_only(client, admin_user, seller_user, load, warehouse):
    from urllib.parse import parse_qs, urlparse
    put(load, make_client(seller_user, "A", 0.01), rolls=4)
    share = route(client, admin_user, load).context["routes"][0]["share"]
    tg = parse_qs(urlparse(share["telegram_url"]).query)
    assert tg["url"] == [share["yandex_link"]]
    assert "yandex.uz/maps" in tg["text"][0] and "google" not in tg["text"][0]
    # WhatsApp and the copied text still carry both.
    assert "google.com/maps" in share["text"] and "yandex.uz/maps" in share["text"]



# --- a failed save keeps what was typed ---------------------------------------------

def test_a_failed_save_shows_the_page_again_with_everything_typed(client, admin_user, seller_user, load):
    a = put(load, make_client(seller_user, "Anvar", 0.01), rolls=12)
    b = make_client(seller_user, "Botir", 0.02)
    c = make_client(seller_user, "Davron", 0.03)
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/", {
        "action": "items", f"rolls_{a.pk}": "15", f"kg_{a.pk}": "abc",
        "new_client": [b.pk, c.pk, ""], "new_rolls": ["7", "", ""],
    })
    # Not a redirect: the same page, nothing saved, nothing lost.
    assert resp.status_code == 200
    a.refresh_from_db()
    assert (a.rolls, a.kg) == (12, None) and load.items.count() == 1
    body = resp.content.decode()
    assert f'name="rolls_{a.pk}" value="15"' in body
    assert f'name="kg_{a.pk}" value="abc"' in body
    assert f'<option value="{b.pk}" selected>' in body and f'<option value="{c.pk}" selected>' in body
    assert 'value="7"' in body
    # The wrong boxes are marked: the kg here, Davron's missing rulon.
    rows = resp.context["new_rows"]
    assert [(r["client"], r["rolls"], r["rolls_bad"]) for r in rows] == [
        (str(b.pk), "7", False), (str(c.pk), "", True)]
    assert resp.context["items"][0].kg_bad and body.count('aria-invalid="true"') == 2
    assert resp.context["failed"] and "data-unsaved" in body


def test_a_rulon_typed_without_a_client_comes_back_marked(client, admin_user, seller_user, load):
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/", {
        "action": "items", "new_client": [""], "new_rolls": ["5"]})
    assert resp.status_code == 200
    assert resp.context["new_rows"] == [{"client": "", "rolls": "5", "client_bad": True, "rolls_bad": False}]
