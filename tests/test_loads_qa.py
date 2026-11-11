"""Regression tests for the Yetkazish QA audit (2026-11-08): each one is a finding
that was reproduced, then fixed — now on the stop-and-product-line form. Server side;
the browser findings are in test_loads_e2e.py and the route maths in test_routing.py."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import User
from crm import views
from crm.models import Client, Load, LoadItem, LoadLine, Product, Warehouse

TODAY = timezone.localdate


@pytest.fixture
def load(db, admin_user):
    return Load.objects.create(date=TODAY(), created_by=admin_user)


@pytest.fixture
def paket(db):
    return Product.objects.create(name="Paket", sku="P-1", cost_price=Decimal("1"), price=Decimal("15000"))


def client_at(owner, name, dlng=0.01):
    return Client.objects.create(name=name, owner=owner, latitude=Decimal("41.3"),
                                 longitude=Decimal("69.24") + Decimal(str(dlng)))


@pytest.fixture
def line(load, seller_user, paket):
    item = LoadItem.objects.create(load=load, client=client_at(seller_user, "A"))
    return LoadLine.objects.create(item=item, product=paket, rolls=5, price=paket.price)


def post_line(client, user, line, **boxes):
    """Post the form with this line's boxes (rolls, price, kg) as given."""
    client.force_login(user)
    data = {"action": "items", f"purpose_{line.item_id}": line.item.purpose,
            f"rolls_{line.pk}": "5", f"price_{line.pk}": "15000", f"kg_{line.pk}": ""}
    data.update({f"{k}_{line.pk}": v for k, v in boxes.items()})
    return client.post(f"/yetkazish/{line.item.load_id}/", data)


def post_new(client, user, load, **lists):
    client.force_login(user)
    return client.post(f"/yetkazish/{load.pk}/", {"action": "items", **lists})


def bad_line(resp):
    return next(l for i in resp.context["items"] for l in i.line_list)


# --- rulon, kg and price: only sane numbers, never a 500 ----------------------------

@pytest.mark.parametrize("raw", ["100001", "99999999999", "99999999999999999999", "3000000000",
                                 "+5", "1_000", "١٢", "²", "1e3", "0", "-1", "3.5", ""])
def test_a_wrong_rulon_is_a_red_box_not_an_error(client, admin_user, line, raw):
    resp = post_line(client, admin_user, line, rolls=raw)
    assert resp.status_code == 200 and bad_line(resp).rolls_bad
    line.refresh_from_db()
    assert line.rolls == 5


@pytest.mark.parametrize("raw", ["1e3", "1e10", "1000000000", "9999999999999", "0", "-0", "0.0001",
                                 "12.3456", "NaN", "Infinity", "abc", "-5"])
def test_a_wrong_kg_is_a_red_box_not_an_error(client, admin_user, line, raw):
    resp = post_line(client, admin_user, line, kg=raw)
    assert resp.status_code == 200 and bad_line(resp).kg_bad
    line.refresh_from_db()
    assert line.kg is None


@pytest.mark.parametrize("raw", ["", "abc", "-1", "1e5", "15000.123", "9999999999999999"])
def test_a_wrong_price_is_a_red_box_not_an_error(client, admin_user, line, raw):
    resp = post_line(client, admin_user, line, price=raw)
    assert resp.status_code == 200 and bad_line(resp).price_bad
    line.refresh_from_db()
    assert line.price == Decimal("15000")


@pytest.mark.parametrize("box,raw,field,saved", [
    ("rolls", "100000", "rolls", 100000), ("rolls", "1 000", "rolls", 1000),
    ("kg", "999999999.999", "kg", Decimal("999999999.999")), ("kg", "12,5", "kg", Decimal("12.5")),
    ("price", "15 000,50", "price", Decimal("15000.50")), ("price", "0", "price", Decimal("0")),
])
def test_good_values_are_saved(client, admin_user, line, box, raw, field, saved):
    post_line(client, admin_user, line, **{box: raw})
    line.refresh_from_db()
    assert getattr(line, field) == saved


def test_a_new_stop_rulon_too_big_is_a_red_box(client, admin_user, seller_user, load, paket):
    a = client_at(seller_user, "A")
    resp = post_new(client, admin_user, load, new_client=[a.pk], new_purpose=["deliver"],
                    new_product=[paket.pk], new_rolls=["3000000000"])
    assert resp.status_code == 200 and resp.context["new_rows"][0]["rolls_bad"]
    assert not LoadItem.objects.exists()


def test_a_strange_client_id_is_a_red_box_not_an_error(client, admin_user, load, paket):
    resp = post_new(client, admin_user, load, new_client=["²"], new_purpose=["deliver"],
                    new_product=[paket.pk], new_rolls=["3"])
    assert resp.status_code == 200 and resp.context["new_rows"][0]["client_bad"]


def test_a_strange_purpose_is_refused(client, admin_user, line):
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{line.item.load_id}/", {
        "action": "items", f"purpose_{line.item_id}": "fly", f"rolls_{line.pk}": "5",
        f"price_{line.pk}": "15000", f"kg_{line.pk}": ""})
    assert resp.status_code == 200
    line.item.refresh_from_db()
    assert line.item.purpose == "deliver"


def test_the_same_client_in_two_new_rows_says_so(client, admin_user, seller_user, load, paket):
    a = client_at(seller_user, "Anvar")
    resp = post_new(client, admin_user, load, new_client=[a.pk, a.pk], new_purpose=["deliver"] * 2,
                    new_product=[paket.pk] * 2, new_rolls=["3", "4"])
    assert resp.status_code == 200 and not LoadItem.objects.exists()
    assert "ikki qatorda tanlangan" in resp.content.decode()


# --- limits on a load ---------------------------------------------------------------

def test_a_load_holds_at_most_58_stops(client, admin_user, seller_user, load, paket):
    for n in range(views.LOAD_MAX_ITEMS):
        LoadItem.objects.create(load=load, client=client_at(seller_user, f"C{n}", n / 1000),
                                purpose="collect")
    extra = client_at(seller_user, "Ortiqcha", 0.5)
    resp = post_new(client, admin_user, load, new_client=[extra.pk], new_purpose=["collect"],
                    new_product=[""], new_rolls=[""])
    assert resp.status_code == 200 and not load.items.filter(client=extra).exists()
    assert "bilan 58 ta mijoz" in resp.content.decode()


def test_too_many_posted_rows_are_refused_without_echoing_them(client, admin_user, load):
    resp = post_new(client, admin_user, load, new_client=[""] * 200, new_purpose=["deliver"] * 200,
                    new_product=[""] * 200, new_rolls=["1"] * 200)
    assert resp.status_code == 302 and not LoadItem.objects.exists()


# --- dates --------------------------------------------------------------------------

@pytest.mark.parametrize("sana", ["0001-01-01", "9999-12-31", "1999-12-31", "2100-01-01"])
def test_out_of_range_days_fall_back_to_today(client, admin_user, sana):
    client.force_login(admin_user)
    resp = client.get("/yetkazish/", {"sana": sana})
    assert resp.status_code == 200 and resp.context["day"] == TODAY()


def test_a_load_is_never_created_on_an_out_of_range_day(client, admin_user):
    owner = User.objects.create_user(username="qa_owner", password="x", role=User.Role.SALES)
    c = Client.objects.create(name="Sana mijoz", owner=owner)
    collect = LoadItem.Purpose.COLLECT
    client.force_login(admin_user)
    client.post("/yetkazish/yangi/", {"action": "items", "sana": "9999-12-31", "new_key": ["1"],
                                      "new_client": [c.pk], "new_purpose": [collect]})
    assert Load.objects.get().date == TODAY()


def test_editing_a_load_to_an_out_of_range_day_is_refused(client, admin_user, load):
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/edit/", {"date": "9999-12-31", "note": ""})
    assert resp.status_code == 200
    load.refresh_from_db()
    assert load.date == TODAY()


# --- saving the order ---------------------------------------------------------------

def test_a_partial_order_is_refused(client, admin_user, seller_user, load):
    a = LoadItem.objects.create(load=load, client=client_at(seller_user, "A", 0.01), purpose="collect")
    LoadItem.objects.create(load=load, client=client_at(seller_user, "B", 0.02), purpose="collect")
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [a.pk]})
    load.refresh_from_db()
    assert load.ordered_at is None and not load.items.exclude(position=None).exists()


# --- deleting a client who is in a load ---------------------------------------------

def test_deleting_a_client_in_a_load_takes_them_out_of_it(client, admin_user, line):
    a = line.item.client
    client.force_login(admin_user)
    confirm = client.get(f"/clients/{a.pk}/delete/").content.decode()
    assert f"Yuk #{line.item.load_id}" in confirm  # the confirm says so first
    resp = client.post(f"/clients/{a.pk}/delete/")
    assert resp.status_code == 302
    assert not Client.objects.filter(pk=a.pk).exists()
    assert not LoadItem.objects.exists() and not LoadLine.objects.exists()


# --- messages ------------------------------------------------------------------------

def test_a_manager_is_told_an_admin_sets_the_warehouse(client, line):
    manager = User.objects.create_user(username="mgr", password="x", role=User.Role.MANAGER)
    Warehouse.load()  # exists, no location
    client.force_login(manager)
    error = client.get(f"/yetkazish/{line.item.load_id}/marshrut/").context["error"]
    assert "administrator" in error


