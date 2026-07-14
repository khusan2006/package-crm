import pytest
from django.urls import reverse

MFG_ADMIN_URLS = ["manufacturing:material_list", "manufacturing:purchase_list"]


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
