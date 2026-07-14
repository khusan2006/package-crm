import pytest
from django.urls import reverse

MFG_ADMIN_URLS = [
    "manufacturing:material_list",
    "manufacturing:purchase_list",
    "manufacturing:sklad_ombor",
    "manufacturing:transfer_list",
]


@pytest.mark.parametrize("name", MFG_ADMIN_URLS)
def test_seller_denied_sklad_pages(client, seller_user, name):
    client.force_login(seller_user)
    resp = client.get(reverse(name))
    assert resp.status_code == 403


@pytest.mark.parametrize("name", MFG_ADMIN_URLS)
def test_admin_sees_sklad_pages(client, admin_user, name):
    client.force_login(admin_user)
    resp = client.get(reverse(name))
    assert resp.status_code == 200


def test_nav_shows_sklad_for_omborchi(client, db):
    from accounts.models import User
    omb = User.objects.create_user(username="omb", password="x", role=User.Role.OMBORCHI)
    client.force_login(omb)
    html = client.get(reverse("dashboard")).content.decode()
    assert "Ishlab chiqarish" in html
    assert reverse("manufacturing:material_list") in html


def test_nav_hides_sklad_for_seller(client, seller_user):
    client.force_login(seller_user)
    html = client.get(reverse("dashboard")).content.decode()
    assert reverse("manufacturing:material_list") not in html
    assert reverse("manufacturing:my_ombor") in html      # seller sees own ombor link


def test_nav_hides_product_catalog_for_seller(client, seller_user, admin_user):
    """A seller should not see the shared product catalog ('Ombor') link — it now
    shows factory/sklad stock and duplicates 'Mening omborim'. Non-sellers keep it."""
    client.force_login(seller_user)
    seller_html = client.get(reverse("dashboard")).content.decode()
    assert reverse("product_list") not in seller_html
    assert reverse("manufacturing:my_ombor") in seller_html

    client.force_login(admin_user)
    admin_html = client.get(reverse("dashboard")).content.decode()
    assert reverse("product_list") in admin_html


def test_omborchi_denied_seller_kassa(client, omborchi_user):
    client.force_login(omborchi_user)
    # Sklad pages allowed:
    assert client.get(reverse("manufacturing:material_list")).status_code == 200
    assert client.get(reverse("manufacturing:sklad_kassa")).status_code == 200


def test_seller_denied_transfer_create(client, seller_user):
    client.force_login(seller_user)
    assert client.get(reverse("manufacturing:transfer_create")).status_code == 403


@pytest.mark.parametrize("name", [
    "manufacturing:material_list",
    "manufacturing:purchase_list",
    "manufacturing:production_list",
    "manufacturing:sklad_ombor",
    "manufacturing:needs_production",
    "manufacturing:transfer_list",
    "manufacturing:sklad_kassa",
    "dashboard",
    "product_list",
    "kassa",
])
def test_exactly_one_nav_item_active(client, admin_user, name):
    """Each page must highlight exactly one sidebar nav item — guards against the
    substring collisions (product⊂production⊂needs_production, kassa⊂sklad_kassa)."""
    client.force_login(admin_user)
    html = client.get(reverse(name)).content.decode()
    assert html.count("nav-item active") == 1, name
