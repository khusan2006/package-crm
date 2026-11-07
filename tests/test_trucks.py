"""Delivery setup: trucks with their capacity, and the one shared warehouse every
route starts from. Both are admin-only — see
docs/specs/2026-11-07-delivery-route-design.md."""

from decimal import Decimal

import pytest

from crm.models import AuditLog, Truck, Warehouse


@pytest.fixture
def truck(db):
    return Truck.objects.create(name="Isuzu", capacity_kg=3000)


def test_admin_sees_trucks_and_warehouse(client, admin_user, truck):
    client.force_login(admin_user)
    resp = client.get("/mashinalar/")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Isuzu" in body
    assert "Ombor joylashuvi hali belgilanmagan" in body


@pytest.mark.parametrize("url", [
    "/mashinalar/", "/mashinalar/yangi/", "/mashinalar/ombor/",
])
def test_seller_is_refused(client, seller_user, url):
    client.force_login(seller_user)
    assert client.get(url).status_code == 403


def test_seller_cannot_edit_or_delete_a_truck(client, seller_user, truck):
    client.force_login(seller_user)
    assert client.post(f"/mashinalar/{truck.pk}/edit/", {"name": "X", "capacity_kg": 1}).status_code == 403
    assert client.post(f"/mashinalar/{truck.pk}/delete/").status_code == 403
    assert Truck.objects.filter(pk=truck.pk, name="Isuzu").exists()


def test_create_truck(client, admin_user):
    client.force_login(admin_user)
    resp = client.post("/mashinalar/yangi/", {"name": "Labo", "capacity_kg": 800, "is_active": "on"})
    assert resp.status_code == 302
    t = Truck.objects.get(name="Labo")
    assert t.capacity_kg == 800 and t.is_active
    assert AuditLog.objects.filter(target_type="Mashina", target_id=t.pk,
                                   action=AuditLog.Action.CREATE).exists()


def test_capacity_must_be_positive(client, admin_user):
    client.force_login(admin_user)
    resp = client.post("/mashinalar/yangi/", {"name": "Bo'sh", "capacity_kg": 0})
    assert resp.status_code == 200
    assert not Truck.objects.filter(name="Bo'sh").exists()


def test_edit_truck_logs_the_change(client, admin_user, truck):
    client.force_login(admin_user)
    resp = client.post(f"/mashinalar/{truck.pk}/edit/", {"name": "Isuzu", "capacity_kg": 3500})
    assert resp.status_code == 302
    truck.refresh_from_db()
    assert truck.capacity_kg == 3500
    assert not truck.is_active  # unchecked box
    log = AuditLog.objects.get(target_type="Mashina", action=AuditLog.Action.UPDATE)
    assert "3000" in log.summary and "3500" in log.summary


def test_delete_truck(client, admin_user, truck):
    client.force_login(admin_user)
    assert client.post(f"/mashinalar/{truck.pk}/delete/").status_code == 302
    assert not Truck.objects.exists()


def test_warehouse_location_from_pasted_link(client, admin_user):
    client.force_login(admin_user)
    resp = client.post("/mashinalar/ombor/", {
        "name": "Asosiy ombor",
        "location": "https://yandex.uz/maps/?pt=69.240562,41.311081&z=17",
    })
    assert resp.status_code == 302
    w = Warehouse.load()
    assert w.name == "Asosiy ombor"
    assert (w.latitude, w.longitude) == (Decimal("41.311081"), Decimal("69.240562"))
    assert w.coordinates == "41.311081, 69.240562"
    log = AuditLog.objects.get(target_type="Ombor")
    assert "41.311081, 69.240562" in log.summary


def test_warehouse_unreadable_location_is_rejected(client, admin_user):
    client.force_login(admin_user)
    resp = client.post("/mashinalar/ombor/", {"name": "Ombor", "location": "bozor yonida"})
    assert resp.status_code == 200
    assert "Joylashuv tanilmadi" in resp.content.decode()
    assert not Warehouse.load().has_location


def test_warehouse_is_a_single_row(db):
    first = Warehouse.load()
    assert Warehouse.load().pk == first.pk
    assert Warehouse.objects.count() == 1


def test_sidebar_link_only_for_admin(client, admin_user, seller_user):
    client.force_login(admin_user)
    assert "/mashinalar/" in client.get("/").content.decode()
    client.force_login(seller_user)
    assert "/mashinalar/" not in client.get("/").content.decode()
