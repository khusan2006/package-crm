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


# --- saving and hand corrections ----------------------------------------------------

from crm.models import AuditLog, DeliveryRoute, RouteStop  # noqa: E402


def save(client, user, stops, day=None, action="save"):
    client.force_login(user)
    return client.post("/yetkazish/", {
        "sana": (day or TODAY()).isoformat(), "action": action,
        "stop": [f"{t.pk}:{c.pk}" for t, c in stops],
    })


@pytest.fixture
def three(db, seller_user, product, warehouse):
    truck = Truck.objects.create(name="Isuzu", capacity_kg=3000)
    clients = [make_client(seller_user, n, d) for n, d in (("A", 0.01), ("B", 0.02), ("C", 0.03))]
    for c in clients:
        sell(c, product, 100)
    return truck, clients


def names(resp, route=0):
    return [s["client"].name for s in resp.context["routes"][route]["stops"]]


def test_unsaved_plan_is_a_suggestion(client, admin_user, three):
    resp = page(client, admin_user)
    assert resp.context["saved"] is None
    assert "Taklif" in resp.content.decode()


def test_saving_keeps_the_order_given(client, admin_user, three):
    truck, (a, b, c) = three
    resp = save(client, admin_user, [(truck, c), (truck, a), (truck, b)])
    assert resp.status_code == 302 and resp.url == f"/yetkazish/?sana={TODAY().isoformat()}"
    route = DeliveryRoute.objects.get(date=TODAY())
    assert route.created_by == admin_user
    assert [(s.client.name, s.position) for s in route.stops.all()] == [("C", 1), ("A", 2), ("B", 3)]
    assert AuditLog.objects.filter(target_type="Yetkazish", target_id=route.pk).exists()
    # Reopened, the page shows the saved order, not a fresh suggestion.
    resp = page(client, admin_user)
    assert resp.context["saved"] == route
    assert names(resp) == ["C", "A", "B"]
    assert "Saqlangan" in resp.content.decode()


def test_saving_again_replaces_the_plan(client, admin_user, three):
    truck, (a, b, c) = three
    save(client, admin_user, [(truck, a), (truck, b), (truck, c)])
    save(client, admin_user, [(truck, b), (truck, a), (truck, c)])
    assert DeliveryRoute.objects.count() == 1
    assert names(page(client, admin_user)) == ["B", "A", "C"]


@pytest.fixture
def two_trucks(db, seller_user, product, warehouse):
    big = Truck.objects.create(name="Katta", capacity_kg=1000)
    small = Truck.objects.create(name="Kichik", capacity_kg=1000)
    a, b = make_client(seller_user, "A", 0.01), make_client(seller_user, "B", 0.02)
    sell(a, product, 600)
    sell(b, product, 600)
    return big, small, a, b


def test_a_stop_moved_to_the_other_truck_stays_there(client, admin_user, two_trucks):
    big, small, a, b = two_trucks
    save(client, admin_user, [(small, a), (big, b)])
    routes = {r["truck"].name: [s["client"].name for s in r["stops"]]
              for r in page(client, admin_user).context["routes"]}
    assert routes == {"Katta": ["B"], "Kichik": ["A"]}


def test_overloading_by_hand_is_saved_but_flagged(client, admin_user, two_trucks):
    big, small, a, b = two_trucks
    save(client, admin_user, [(big, a), (big, b)])
    resp = page(client, admin_user)
    routes = resp.context["routes"]
    assert len(routes) == 1 and routes[0]["truck"] == big
    assert routes[0]["kg"] == Decimal(1200) and routes[0]["over"]
    assert "sig'imidan oshib ketdi" in resp.content.decode()


@pytest.mark.parametrize("bad", ["unknown client", "duplicate", "garbage", "nothing"])
def test_a_stale_or_broken_post_saves_nothing(client, admin_user, seller_user, three, bad):
    truck, (a, b, c) = three
    stranger = make_client(seller_user, "Begona", 0.04)  # no sale today
    client.force_login(admin_user)
    stops = {
        "unknown client": [f"{truck.pk}:{a.pk}", f"{truck.pk}:{stranger.pk}"],
        "duplicate": [f"{truck.pk}:{a.pk}", f"{truck.pk}:{a.pk}"],
        "garbage": ["abc"],
        "nothing": [],
    }[bad]
    resp = client.post("/yetkazish/", {"sana": TODAY().isoformat(), "action": "save", "stop": stops})
    assert resp.status_code == 302
    assert not DeliveryRoute.objects.exists()


def test_a_sale_after_saving_is_flagged_not_slipped_in(client, admin_user, seller_user,
                                                       product, three):
    truck, (a, b, c) = three
    save(client, admin_user, [(truck, a), (truck, b), (truck, c)])
    late = make_client(seller_user, "Kechikkan", 0.05)
    sell(late, product, 100)
    resp = page(client, admin_user)
    assert names(resp) == ["A", "B", "C"]
    assert resp.context["unplanned"] == [late]
    assert "Kechikkan" in resp.content.decode()


def test_a_cancelled_sale_drops_out_of_the_saved_plan(client, admin_user, three):
    truck, (a, b, c) = three
    save(client, admin_user, [(truck, a), (truck, b), (truck, c)])
    b.sales.all().delete()
    resp = page(client, admin_user)
    assert names(resp) == ["A", "C"]
    assert resp.context["dropped"] == 1


def test_saved_plan_reads_the_load_from_the_sales_as_they_are(client, admin_user, three, product):
    truck, (a, b, c) = three
    save(client, admin_user, [(truck, a), (truck, b), (truck, c)])
    sell(a, product, 50)  # a second sale to A after saving
    stops = page(client, admin_user).context["routes"][0]["stops"]
    assert stops[0]["kg"] == Decimal(150)


def test_reset_throws_the_saved_plan_away(client, admin_user, three):
    truck, (a, b, c) = three
    save(client, admin_user, [(truck, c), (truck, b), (truck, a)])
    resp = save(client, admin_user, [], action="reset")
    assert resp.status_code == 302
    assert not DeliveryRoute.objects.exists() and not RouteStop.objects.exists()
    assert page(client, admin_user).context["saved"] is None


def test_sellers_cannot_save(client, seller_user, three):
    truck, (a, b, c) = three
    assert save(client, seller_user, [(truck, a)]).status_code == 403
    assert not DeliveryRoute.objects.exists()


def test_a_truck_in_a_saved_plan_is_switched_off_not_deleted(client, admin_user, three):
    truck, (a, b, c) = three
    save(client, admin_user, [(truck, a)])
    client.post(f"/mashinalar/{truck.pk}/delete/")
    truck.refresh_from_db()
    assert not truck.is_active
    assert RouteStop.objects.filter(truck=truck).exists()


def test_ids_and_capacity_are_written_without_a_thousands_separator(client, admin_user,
                                                                    seller_user, product,
                                                                    warehouse):
    # USE_THOUSAND_SEPARATOR would print 1500 as "1 500", which the page's script
    # reads as 1 — and a "truck:client" pair like that can't be saved.
    truck = Truck.objects.create(pk=1200, name="Isuzu", capacity_kg=3000)
    c = Client.objects.create(pk=1500, name="Katta raqam", owner=seller_user,
                              latitude=Decimal("41.31"), longitude=Decimal("69.25"))
    sell(c, product, 100)
    body = page(client, admin_user).content.decode()
    assert 'value="1200:1500"' in body
    assert 'data-capacity="3000"' in body
    assert 'data-route-truck="1200"' in body
