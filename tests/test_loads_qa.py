"""Regression tests for the Yetkazish QA audit (2026-11-08): each one is a finding
that was reproduced, then fixed. Server side; the browser findings are in
test_loads_e2e.py and the route maths in test_routing.py."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import User
from crm import views
from crm.models import Client, Load, LoadItem, Warehouse

TODAY = timezone.localdate


@pytest.fixture
def load(db, admin_user):
    return Load.objects.create(date=TODAY(), created_by=admin_user)


def client_at(owner, name, dlng=0.01):
    return Client.objects.create(name=name, owner=owner, latitude=Decimal("41.3"),
                                 longitude=Decimal("69.24") + Decimal(str(dlng)))


def post_items(client, user, load, **data):
    client.force_login(user)
    return client.post(f"/yetkazish/{load.pk}/", {"action": "items", **data})


# --- rulon and kg: only sane numbers, never a 500 -----------------------------------

@pytest.mark.parametrize("raw", ["100001", "99999999999", "99999999999999999999", "3000000000",
                                 "+5", "1_000", "١٢", "²", "1e3", "0", "-1", "3.5"])
def test_a_wrong_rulon_is_a_red_box_not_an_error(client, admin_user, seller_user, load, raw):
    item = LoadItem.objects.create(load=load, client=client_at(seller_user, "A"), rolls=5)
    resp = post_items(client, admin_user, load, **{f"rolls_{item.pk}": raw, f"kg_{item.pk}": ""})
    assert resp.status_code == 200
    assert resp.context["items"][0].rolls_bad
    item.refresh_from_db()
    assert item.rolls == 5


@pytest.mark.parametrize("raw", ["1e3", "1e10", "1000000000", "9999999999999", "0", "-0", "0.0001",
                                 "12.3456", "NaN", "Infinity", "abc", "-5"])
def test_a_wrong_kg_is_a_red_box_not_an_error(client, admin_user, seller_user, load, raw):
    item = LoadItem.objects.create(load=load, client=client_at(seller_user, "A"), rolls=5)
    resp = post_items(client, admin_user, load, **{f"rolls_{item.pk}": "5", f"kg_{item.pk}": raw})
    assert resp.status_code == 200
    assert resp.context["items"][0].kg_bad
    item.refresh_from_db()
    assert item.kg is None


@pytest.mark.parametrize("raw,saved", [("100000", None), ("1 000", None)])
def test_the_largest_and_grouped_rulon_are_fine(client, admin_user, seller_user, load, raw, saved):
    item = LoadItem.objects.create(load=load, client=client_at(seller_user, "A"), rolls=5)
    post_items(client, admin_user, load, **{f"rolls_{item.pk}": raw, f"kg_{item.pk}": ""})
    item.refresh_from_db()
    assert item.rolls == int(raw.replace(" ", ""))


@pytest.mark.parametrize("raw,saved", [("999999999.999", Decimal("999999999.999")),
                                       ("12,5", Decimal("12.5")), ("1 000", Decimal("1000"))])
def test_good_kg_is_saved(client, admin_user, seller_user, load, raw, saved):
    item = LoadItem.objects.create(load=load, client=client_at(seller_user, "A"), rolls=5)
    post_items(client, admin_user, load, **{f"rolls_{item.pk}": "5", f"kg_{item.pk}": raw})
    item.refresh_from_db()
    assert item.kg == saved


def test_a_new_row_rulon_too_big_is_a_red_box(client, admin_user, seller_user, load):
    a = client_at(seller_user, "A")
    resp = post_items(client, admin_user, load, new_client=[a.pk], new_rolls=["3000000000"])
    assert resp.status_code == 200 and resp.context["new_rows"][0]["rolls_bad"]
    assert not LoadItem.objects.exists()


def test_a_strange_client_id_is_a_red_box_not_an_error(client, admin_user, load):
    resp = post_items(client, admin_user, load, new_client=["²"], new_rolls=["3"])
    assert resp.status_code == 200 and resp.context["new_rows"][0]["client_bad"]


def test_the_same_client_in_two_new_rows_says_so(client, admin_user, seller_user, load):
    a = client_at(seller_user, "Anvar")
    resp = post_items(client, admin_user, load, new_client=[a.pk, a.pk], new_rolls=["3", "4"])
    assert resp.status_code == 200 and not LoadItem.objects.exists()
    assert "ikki qatorda tanlangan" in resp.content.decode()
    assert "yukda allaqachon bor" not in resp.content.decode()


# --- limits on a load ---------------------------------------------------------------

def test_a_load_holds_at_most_58_clients(client, admin_user, seller_user, load):
    for n in range(views.LOAD_MAX_ITEMS):
        LoadItem.objects.create(load=load, client=client_at(seller_user, f"C{n}", n / 1000), rolls=1)
    extra = client_at(seller_user, "Ortiqcha", 0.5)
    resp = post_items(client, admin_user, load, new_client=[extra.pk], new_rolls=["1"])
    assert resp.status_code == 200
    assert not load.items.filter(client=extra).exists()
    assert "bilan 58 ta mijoz" in resp.content.decode()


def test_too_many_posted_rows_are_refused_without_echoing_them(client, admin_user, seller_user, load):
    resp = post_items(client, admin_user, load, new_client=[""] * 200, new_rolls=["1"] * 200)
    assert resp.status_code == 302 and not LoadItem.objects.exists()


# --- dates --------------------------------------------------------------------------

@pytest.mark.parametrize("sana", ["0001-01-01", "9999-12-31", "1999-12-31", "2100-01-01"])
def test_out_of_range_days_fall_back_to_today(client, admin_user, sana):
    client.force_login(admin_user)
    resp = client.get("/yetkazish/", {"sana": sana})
    assert resp.status_code == 200 and resp.context["day"] == TODAY()


def test_a_load_is_never_created_on_an_out_of_range_day(client, admin_user):
    client.force_login(admin_user)
    client.post("/yetkazish/yangi/", {"sana": "9999-12-31"})
    assert Load.objects.get().date == TODAY()


def test_editing_a_load_to_an_out_of_range_day_is_refused(client, admin_user, load):
    client.force_login(admin_user)
    resp = client.post(f"/yetkazish/{load.pk}/edit/", {"date": "9999-12-31", "note": ""})
    assert resp.status_code == 200
    load.refresh_from_db()
    assert load.date == TODAY()


# --- saving the order ---------------------------------------------------------------

def test_a_partial_order_is_refused(client, admin_user, seller_user, load):
    a = LoadItem.objects.create(load=load, client=client_at(seller_user, "A", 0.01), rolls=1)
    LoadItem.objects.create(load=load, client=client_at(seller_user, "B", 0.02), rolls=1)
    client.force_login(admin_user)
    client.post(f"/yetkazish/{load.pk}/", {"action": "save", "stop": [a.pk]})
    load.refresh_from_db()
    assert load.ordered_at is None and not load.items.exclude(position=None).exists()


# --- deleting a client who is in a load ---------------------------------------------

def test_deleting_a_client_in_a_load_takes_them_out_of_it(client, admin_user, seller_user, load):
    a = client_at(seller_user, "Anvar")
    LoadItem.objects.create(load=load, client=a, rolls=4)
    client.force_login(admin_user)
    confirm = client.get(f"/clients/{a.pk}/delete/").content.decode()
    assert f"Yuk #{load.pk}" in confirm  # the confirm says so first
    resp = client.post(f"/clients/{a.pk}/delete/")
    assert resp.status_code == 302
    assert not Client.objects.filter(pk=a.pk).exists() and not LoadItem.objects.exists()


# --- messages ------------------------------------------------------------------------

def test_a_manager_is_told_an_admin_sets_the_warehouse(client, seller_user, load):
    manager = User.objects.create_user(username="mgr", password="x", role=User.Role.MANAGER)
    LoadItem.objects.create(load=load, client=client_at(seller_user, "A"), rolls=1)
    Warehouse.load()  # exists, no location
    client.force_login(manager)
    error = client.get(f"/yetkazish/{load.pk}/marshrut/").context["error"]
    assert "administrator" in error


def test_the_load_list_says_whether_the_order_is_saved(client, admin_user, load):
    client.force_login(admin_user)
    body = client.get("/yetkazish/").content.decode()
    assert "saqlanmagan" in body and "Taklif" not in body
    load.ordered_at = timezone.now() - timedelta(minutes=1)
    load.save()
    assert "Saqlangan" in client.get("/yetkazish/").content.decode()
