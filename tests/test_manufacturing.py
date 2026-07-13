import pytest
from django.apps import apps
from accounts.models import User


def test_omborchi_role_exists():
    assert User.Role.OMBORCHI == "omborchi"
    assert ("omborchi", "Omborchi") in User.Role.choices


def test_manufacturing_app_installed():
    assert apps.is_installed("manufacturing")


from decimal import Decimal
from django.utils import timezone
from manufacturing.models import RawMaterial, MaterialPurchase
from manufacturing import services


@pytest.fixture
def material(db):
    return RawMaterial.objects.create(name="Karton", sku="MAT-1")


def _buy(material, qty, price, user, day="2026-07-01"):
    p = MaterialPurchase.objects.create(
        material=material, quantity_kg=Decimal(qty), price_per_kg=Decimal(price),
        date=day, created_by=user,
    )
    services.recompute_avg_cost(material)
    material.refresh_from_db()
    return p


def test_first_purchase_sets_avg_to_price(material, admin_user):
    _buy(material, "100", "1000", admin_user)
    assert material.avg_cost == Decimal("1000.00")
    assert material.current_stock == Decimal("100.000")


def test_second_purchase_is_weighted(material, admin_user):
    _buy(material, "100", "1000", admin_user, day="2026-07-01")
    _buy(material, "100", "2000", admin_user, day="2026-07-02")
    # (100*1000 + 100*2000) / 200 = 1500
    assert material.avg_cost == Decimal("1500.00")
    assert material.current_stock == Decimal("200.000")


def test_purchase_total(material, admin_user):
    p = _buy(material, "10", "1500", admin_user)
    assert p.total == Decimal("15000.00")
