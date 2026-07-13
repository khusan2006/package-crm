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
