"""Role-based access matrix — asserted at the view layer (no browser needed).

The company's rule: an admin/manager sees and manages EVERY seller's work; a
seller sees only their OWN work — EXCEPT the shared company sections (warehouse /
products / kassa), which every role may fully use. User management is the only
strictly admin-only area. These tests pin that contract down.
"""

import pytest

pytestmark = pytest.mark.django_db


# --- Admin-only: user management ----------------------------------------------

def test_seller_blocked_from_user_management(client, seller_user):
    client.force_login(seller_user)
    assert client.get("/users/").status_code == 403


def test_admin_can_reach_user_management(client, admin_user):
    client.force_login(admin_user)
    assert client.get("/users/").status_code == 200


# --- Seller scoping: own work is reachable ------------------------------------

def test_seller_sales_list_loads(client, seller_user, sample_data):
    client.force_login(seller_user)
    assert client.get("/sales/").status_code == 200


# --- Shared sections: a seller may manage the warehouse/products --------------
# By design, the warehouse and products are a shared company section — every
# role, sellers included, can create products and adjust stock.

def test_seller_can_open_product_create(client, seller_user):
    client.force_login(seller_user)
    assert client.get("/products/new/").status_code == 200


def test_seller_can_adjust_stock(client, seller_user, sample_data):
    client.force_login(seller_user)
    pk = sample_data["product"].pk
    assert client.get(f"/products/{pk}/tuzatish/").status_code == 200


def test_seller_can_create_product_via_post(client, seller_user):
    client.force_login(seller_user)
    resp = client.post("/products/new/", {
        "name": "Seller paket", "sku": "SLR-001", "price": "20000",
        "cost_price": "12000", "low_stock_threshold": "0", "is_active": "on",
    })
    assert resp.status_code in (200, 302)
    from crm.models import Product
    assert Product.objects.filter(sku="SLR-001").exists()
