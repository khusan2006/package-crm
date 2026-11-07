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
from crm.models import AuditLog, Client, Load, LoadItem, Truck, Warehouse

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


@pytest.fixture
def truck(db):
    return Truck.objects.create(name="Isuzu", capacity_kg=1000)


def make_client(owner, name, dlng=None):
    loc = {} if dlng is None else {"latitude": Decimal("41.300000"),
                                   "longitude": Decimal("69.240000") + Decimal(str(dlng))}
    return Client.objects.create(name=name, owner=owner, address=f"{name} ko'chasi", **loc)


@pytest.fixture
def load(db, admin_user, truck):
    return Load.objects.create(date=TODAY(), truck=truck, created_by=admin_user)


def put(load, client, rolls=10, kg=None):
    return LoadItem.objects.create(load=load, client=client, rolls=rolls,
                                   kg=None if kg is None else Decimal(str(kg)))


def get(client, user, url):
    client.force_login(user)
    return client.get(url)


def detail(client, user, load):
    return get(client, user, f"/yetkazish/{load.pk}/")


def names(resp):
    return [s["client"].name for s in resp.context["stops"]]


# --- who ----------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["/yetkazish/", "/yetkazish/yangi/"])
def test_sellers_cannot_reach_loads(client, seller_user, url):
    assert get(client, seller_user, url).status_code == 403


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

def test_creating_a_load_opens_it(client, admin_user, truck):
    client.force_login(admin_user)
    tomorrow = TODAY() + timedelta(days=1)
    resp = client.post("/yetkazish/yangi/", {"date": tomorrow.isoformat(), "truck": truck.pk, "note": "1-reys"})
    load = Load.objects.get()
    assert resp.status_code == 302 and resp.url == f"/yetkazish/{load.pk}/"
    assert (load.date, load.truck, load.note, load.created_by) == (tomorrow, truck, "1-reys", admin_user)
    assert AuditLog.objects.filter(target_type="Yuk", target_id=load.pk).exists()


def test_a_load_needs_no_truck_yet(client, admin_user):
    client.force_login(admin_user)
    client.post("/yetkazish/yangi/", {"date": TODAY().isoformat(), "truck": "", "note": ""})
    assert Load.objects.get().truck is None


def test_a_day_can_have_several_loads(client, admin_user, seller_user, truck):
    a = Load.objects.create(date=TODAY(), truck=truck)
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
    client.post(f"/yetkazish/{load.pk}/edit/", {"date": TODAY().isoformat(), "truck": "", "note": "kech"})
    load.refresh_from_db()
    assert load.truck is None and load.note == "kech"


def test_deleting_a_load_takes_its_clients_too(client, admin_user, seller_user, load):
    put(load, make_client(seller_user, "X", 0.01))
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/delete/")
    assert resp.status_code == 302 and resp.url == f"/yetkazish/?sana={TODAY().isoformat()}"
    assert not Load.objects.exists() and not LoadItem.objects.exists()


# --- clients, rolls, kg -------------------------------------------------------------

def test_adding_a_client_with_rolls_and_no_kg(client, admin_user, seller_user, load):
    c = make_client(seller_user, "Anvar", 0.01)
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/mijoz/", {"client": c.pk, "rolls": 12, "kg": ""})
    assert resp.status_code == 302
    item = LoadItem.objects.get()
    assert (item.client, item.rolls, item.kg) == (c, 12, None)


def test_rolls_must_be_at_least_one(client, admin_user, seller_user, load):
    c = make_client(seller_user, "Anvar", 0.01)
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/mijoz/", {"client": c.pk, "rolls": 0})
    assert resp.status_code == 200 and not LoadItem.objects.exists()


def test_the_same_client_twice_is_refused(client, admin_user, seller_user, load):
    c = make_client(seller_user, "Anvar", 0.01)
    put(load, c)
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/mijoz/", {"client": c.pk, "rolls": 3})
    assert resp.status_code == 200 and "allaqachon bor" in resp.content.decode()
    assert LoadItem.objects.count() == 1


def test_the_same_client_can_be_in_another_load(client, admin_user, seller_user, load):
    c = make_client(seller_user, "Anvar", 0.01)
    put(Load.objects.create(date=TODAY()), c)
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/mijoz/", {"client": c.pk, "rolls": 3})
    assert load.items.filter(client=c).exists()


def test_kg_is_entered_later(client, admin_user, seller_user, load, warehouse):
    item = put(load, make_client(seller_user, "Anvar", 0.01), rolls=12)
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/mijoz/{item.pk}/edit/", {"rolls": 12, "kg": "340.5"})
    item.refresh_from_db()
    assert item.kg == Decimal("340.5")
    resp = detail(client, admin_user, load)
    assert resp.context["kg_total"] == Decimal("340.5") and resp.context["unweighed"] == 0


def test_kg_against_the_trucks_capacity(client, admin_user, seller_user, load, warehouse):
    put(load, make_client(seller_user, "A", 0.01), kg=700)
    put(load, make_client(seller_user, "B", 0.02), kg=500)
    resp = detail(client, admin_user, load)
    assert resp.context["over"] and resp.context["load_pct"] == 100
    assert "imidan oshib ketdi" in resp.content.decode()


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
    resp = detail(client, admin_user, load)
    assert names(resp) == ["B", "C", "A"]
    assert resp.context["km"] > 0 and resp.context["map_data"]["depot"] == [41.3, 69.24]
    assert "Taklif" in resp.content.decode()


def test_drivers_message_has_rolls_and_kg_when_known(client, admin_user, load, three):
    three[1].kg = Decimal("250")
    three[1].save()
    share = detail(client, admin_user, load).context["share"]
    assert share["text"].startswith(f"Yuk #{load.pk} — {TODAY():%d.%m.%Y} — Isuzu")
    assert "1. B — 10 rulon · 250 kg" in share["text"]
    assert "2. C — 10 rulon\n" in share["text"]


def test_saving_the_order(client, admin_user, load, three):
    a, b, c = three
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk, a.pk, b.pk]})
    assert resp.status_code == 302
    load.refresh_from_db()
    assert load.ordered_at is not None
    assert names(detail(client, admin_user, load)) == ["C", "A", "B"]


def test_a_client_added_after_saving_joins_the_end(client, admin_user, seller_user, load, three):
    a, b, c = three
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [c.pk, a.pk, b.pk]})
    late = make_client(seller_user, "Kechikkan", 0.005)
    client.post(f"/yetkazish/{load.pk}/mijoz/", {"client": late.pk, "rolls": 2})
    assert names(detail(client, admin_user, load)) == ["C", "A", "B", "Kechikkan"]


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
    assert names(detail(client, admin_user, load)) == ["B", "C", "A"]


def test_missing_locations_hold_the_route_back(client, admin_user, seller_user, load, three):
    nowhere = make_client(seller_user, "Joysiz")
    put(load, nowhere)
    resp = detail(client, admin_user, load)
    assert resp.context["stops"] == [] and resp.context["missing"] == [nowhere]
    assert f"/clients/{nowhere.pk}/edit/" in resp.content.decode()


def test_no_warehouse_location_is_explained(client, admin_user, seller_user, load):
    put(load, make_client(seller_user, "A", 0.01))
    resp = detail(client, admin_user, load)
    assert resp.context["stops"] == [] and "Ombor joylashuvi" in resp.context["error"]


def test_ids_are_written_without_a_thousands_separator(client, admin_user, seller_user, load, warehouse):
    c = make_client(seller_user, "Katta", 0.01)
    LoadItem.objects.create(pk=1500, load=load, client=c, rolls=1)
    assert 'name="stop" value="1500"' in detail(client, admin_user, load).content.decode()


def test_client_names_cannot_break_out_of_the_map_data(client, admin_user, seller_user, load, warehouse):
    put(load, make_client(seller_user, "</script><b>x", 0.01))
    assert "</script><b>x" not in detail(client, admin_user, load).content.decode()


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
    resp = detail(client, admin_user, load)
    assert resp.context["by_road"] and names(resp) == ["Uzoq", "Yaqin"]
    assert resp.context["km"] == pytest.approx(2 + 3 + 9)
    assert resp.context["map_data"]["routes"][0]["path"][1] == [41.31, 69.26]


def test_when_the_road_service_fails_the_route_still_comes(client, admin_user, seller_user, load,
                                                           warehouse, road_key, monkeypatch):
    put(load, make_client(seller_user, "A", 0.01))

    def broken(points):
        raise road.RoadError("down")

    monkeypatch.setattr(road, "matrix_km", broken)
    resp = detail(client, admin_user, load)
    assert not resp.context["by_road"] and len(resp.context["stops"]) == 1
    assert resp.context["road_note"].startswith("Yo'l masofalarini olib bo'lmadi")


# --- trucks -------------------------------------------------------------------------

def test_a_truck_used_by_a_load_is_switched_off_not_deleted(client, admin_user, load, truck):
    client.force_login(admin_user)
    client.post(f"/mashinalar/{truck.pk}/delete/")
    truck.refresh_from_db()
    assert not truck.is_active and Load.objects.filter(truck=truck).exists()


def test_a_switched_off_truck_stays_on_its_load_form(client, admin_user, load, truck):
    Truck.objects.filter(pk=truck.pk).update(is_active=False)
    form = get(client, admin_user, f"/yetkazish/{load.pk}/edit/").context["form"]
    assert truck in form.fields["truck"].queryset
