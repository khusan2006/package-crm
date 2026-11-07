"""The Yetkazish page: a day's sales from every seller, turned into one route per
truck. The route maths itself is covered in test_routing.py; this is which sales
count, the gates before planning, and who may see it."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.models import User
from crm.models import Client, Product, Sale, SaleItem, Truck, Warehouse

TODAY = timezone.localdate


@pytest.fixture
def product(db):
    return Product.objects.create(name="Paket", sku="P-1", cost_price=Decimal("1"), price=Decimal("2"))


@pytest.fixture
def warehouse(db):
    w = Warehouse.load()
    w.latitude, w.longitude = Decimal("41.300000"), Decimal("69.240000")
    w.save()
    return w


@pytest.fixture
def other_seller(db):
    return User.objects.create_user(username="seller2", password="x", role=User.Role.SALES,
                                    first_name="Ikkinchi")


@pytest.fixture
def manager(db):
    return User.objects.create_user(username="mgr", password="x", role=User.Role.MANAGER)


def make_client(owner, name, dlng=None):
    loc = {} if dlng is None else {"latitude": Decimal("41.300000"),
                                   "longitude": Decimal("69.240000") + Decimal(str(dlng))}
    return Client.objects.create(name=name, owner=owner, address=f"{name} ko'chasi", **loc)


def sell(client, product, kg, fulfilled=None, day=None, dimension="kg", **extra):
    sale = Sale.objects.create(client=client, sales_rep=client.owner, date=day or TODAY(), **extra)
    weight = Decimal(str(kg))
    in_kg = weight / 1000 if dimension == "g" else weight
    SaleItem.objects.create(
        sale=sale, product=product, dimension=dimension, weight=weight,
        price=Decimal("2"), cost_price=Decimal("1"),
        fulfilled_kg=in_kg if fulfilled is None else Decimal(str(fulfilled)),
        fulfilled_at=TODAY() if fulfilled is None else None,
    )
    return sale


def page(client, user, day=None):
    client.force_login(user)
    url = "/yetkazish/" + (f"?sana={day.isoformat()}" if day else "")
    return client.get(url)


def test_sellers_cannot_open_it(client, seller_user):
    assert page(client, seller_user).status_code == 403


def test_manager_can_open_it(client, manager, warehouse):
    assert page(client, manager).status_code == 200


def test_one_truck_when_the_load_fits(client, admin_user, seller_user, other_seller,
                                      product, warehouse):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    sell(make_client(seller_user, "Anvar", 0.01), product, 500)
    sell(make_client(other_seller, "Botir", 0.02), product, 700)
    resp = page(client, admin_user)
    routes = resp.context["routes"]
    assert len(routes) == 1
    assert routes[0]["kg"] == Decimal(1200)
    assert [s["client"].name for s in routes[0]["stops"]] == ["Anvar", "Botir"]
    assert resp.context["map_data"]["depot"] == [41.3, 69.24]
    body = resp.content.decode()
    assert "Isuzu" in body and 'id="route-data"' in body


def test_two_trucks_when_it_does_not(client, admin_user, seller_user, product, warehouse):
    Truck.objects.create(name="A", capacity_kg=1000)
    Truck.objects.create(name="B", capacity_kg=1000)
    for i, dlng in enumerate((-0.05, -0.04, 0.04, 0.05)):
        sell(make_client(seller_user, f"M{i}", dlng), product, 450)
    routes = page(client, admin_user).context["routes"]
    assert len(routes) == 2
    assert all(r["kg"] <= r["truck"].capacity_kg for r in routes)
    assert sorted(s["client"].name for r in routes for s in r["stops"]) == ["M0", "M1", "M2", "M3"]


def test_two_sales_to_one_client_are_one_stop(client, admin_user, seller_user, product, warehouse):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    c = make_client(seller_user, "Anvar", 0.01)
    sell(c, product, 100)
    sell(c, product, 50)
    resp = page(client, admin_user)
    stops = resp.context["routes"][0]["stops"]
    assert len(stops) == 1 and stops[0]["kg"] == Decimal(150)
    assert resp.context["sale_count"] == 2


def test_only_in_stock_goods_are_loaded(client, admin_user, seller_user, product, warehouse):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    sell(make_client(seller_user, "Qisman", 0.01), product, 100, fulfilled=40)
    zakaz = sell(make_client(seller_user, "Zakaz", 0.02), product, 80, fulfilled=0)
    resp = page(client, admin_user)
    stop = resp.context["routes"][0]["stops"][0]
    assert (stop["client"].name, stop["kg"], stop["pending_kg"]) == ("Qisman", Decimal(40), Decimal(60))
    assert resp.context["waiting"] == [zakaz]
    assert "Omborda yo'q" in resp.content.decode()


def test_grams_count_as_kg(client, admin_user, seller_user, product, warehouse):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    sell(make_client(seller_user, "Gramm", 0.01), product, 500, dimension="g")
    assert page(client, admin_user).context["total_kg"] == Decimal("0.5")


def test_other_days_and_opening_balances_are_left_out(client, admin_user, seller_user,
                                                      product, warehouse):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    c = make_client(seller_user, "Anvar", 0.01)
    sell(c, product, 100, day=TODAY() - timedelta(days=1))
    Sale.objects.create(client=c, sales_rep=seller_user, is_opening=True,
                        opening_amount=Decimal("1000"))
    resp = page(client, admin_user)
    assert resp.context["visit_count"] == 0 and resp.context["routes"] == []
    assert "yetkaziladigan sotuv yo'q" in resp.content.decode()
    # ...and the day before shows it.
    assert page(client, admin_user, TODAY() - timedelta(days=1)).context["visit_count"] == 1


def test_missing_locations_block_the_plan(client, admin_user, seller_user, product, warehouse):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    sell(make_client(seller_user, "Joyli", 0.01), product, 100)
    nowhere = make_client(seller_user, "Joysiz")
    sell(nowhere, product, 100)
    resp = page(client, admin_user)
    assert resp.context["routes"] == []
    assert resp.context["missing"] == [nowhere]
    assert f"/clients/{nowhere.pk}/edit/" in resp.content.decode()


def test_no_warehouse_location_is_explained(client, admin_user, seller_user, product):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    sell(make_client(seller_user, "Anvar", 0.01), product, 100)
    resp = page(client, admin_user)
    assert resp.context["routes"] == []
    assert "Ombor joylashuvi belgilanmagan" in resp.context["error"]


def test_over_capacity_is_explained(client, admin_user, seller_user, product, warehouse):
    Truck.objects.create(name="Kichik", capacity_kg=100)
    sell(make_client(seller_user, "A", 0.01), product, 90)
    sell(make_client(seller_user, "B", 0.02), product, 90)
    resp = page(client, admin_user)
    assert resp.context["routes"] == []
    assert "80 kg ortiqcha" in resp.context["error"]


def test_inactive_trucks_are_not_used(client, admin_user, seller_user, product, warehouse):
    Truck.objects.create(name="Eski", capacity_kg=5000, is_active=False)
    sell(make_client(seller_user, "A", 0.01), product, 90)
    resp = page(client, admin_user)
    assert "Faol mashina yo'q" in resp.context["error"]


def test_client_names_are_escaped_in_the_map_data(client, admin_user, seller_user, product,
                                                  warehouse):
    Truck.objects.create(name="Isuzu", capacity_kg=3000)
    sell(make_client(seller_user, "</script><b>x", 0.01), product, 10)
    body = page(client, admin_user).content.decode()
    assert "</script><b>x" not in body


def test_sotuvlar_links_to_it_for_admins_only(client, admin_user, seller_user):
    client.force_login(admin_user)
    assert "/yetkazish/" in client.get("/sales/").content.decode()
    client.force_login(seller_user)
    assert "/yetkazish/" not in client.get("/sales/").content.decode()
