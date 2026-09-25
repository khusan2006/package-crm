"""A mijoz's joylashuv: saved from a map, GPS or pasted link on the client form,
and passed on from the joylashuv modal (Ulashish, Telegram, WhatsApp, SMS).

The link formats themselves are covered in test_geo.py; this is the form, the
views, and who may see what.
"""

from decimal import Decimal

import pytest

from crm import geo
from crm.models import AuditLog, Client

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def _post(client, pk=None, **fields):
    data = {"name": "Joyli mijoz", "company": "", "phone": "", "address": "Chilonzor",
            "location": "", "notes": "", **fields}
    url = f"/clients/{pk}/edit/" if pk else "/clients/new/"
    return client.post(url, data)


@pytest.fixture
def located(db, seller_user):
    return Client.objects.create(
        name="Joyli mijoz", owner=seller_user, phone="+998 90 111 22 33",
        address="Chilonzor", latitude=Decimal("41.311081"), longitude=Decimal("69.240562"),
    )


def test_create_with_pasted_link_saves_the_point(client, seller_user):
    client.force_login(seller_user)
    resp = _post(client, location="https://yandex.uz/maps/?pt=69.240562,41.311081&z=17")
    assert resp.status_code == 302
    c = Client.objects.get(name="Joyli mijoz")
    assert (c.latitude, c.longitude) == (Decimal("41.311081"), Decimal("69.240562"))
    assert c.owner == seller_user


def test_create_without_location_leaves_it_empty(client, seller_user):
    client.force_login(seller_user)
    _post(client)
    assert not Client.objects.get(name="Joyli mijoz").has_location


def test_unreadable_location_is_a_field_error(client, seller_user):
    client.force_login(seller_user)
    resp = _post(client, location="Chilonzor bozori yonida")
    assert resp.status_code == 200
    assert "Joylashuv tanilmadi" in resp.content.decode()
    assert not Client.objects.filter(name="Joyli mijoz").exists()


def test_short_link_resolved_on_save(client, seller_user, monkeypatch):
    client.force_login(seller_user)
    monkeypatch.setattr(geo, "_next_hop",
                        lambda url: "https://www.google.com/maps/@41.311081,69.240562,17z")
    _post(client, location="https://maps.app.goo.gl/xyz")
    assert Client.objects.get(name="Joyli mijoz").latitude == Decimal("41.311081")


def test_edit_shows_point_audits_the_change_and_clearing_removes_it(client, seller_user, located):
    client.force_login(seller_user)
    html = client.get(f"/clients/{located.pk}/edit/").content.decode()
    assert 'value="41.311081, 69.240562"' in html and "data-geo-picker" in html

    _post(client, pk=located.pk, name=located.name, phone=located.phone,
          location="41.3, 69.2")
    located.refresh_from_db()
    assert (located.latitude, located.longitude) == (Decimal("41.3"), Decimal("69.2"))
    # The trail reads as coordinates, not as a pair of Decimals.
    line = AuditLog.objects.filter(target_type="Mijoz", target_id=located.pk).latest("pk")
    assert "Joylashuv: 41.311081, 69.240562 → 41.3, 69.2" in line.summary

    _post(client, pk=located.pk, name=located.name, phone=located.phone, location="")
    located.refresh_from_db()
    assert not located.has_location


def test_location_modal_offers_every_way_to_share(client, seller_user, located):
    client.force_login(seller_user)
    resp = client.get(f"/clients/{located.pk}/location/", **XHR)
    html = resp.content.decode()
    assert resp.status_code == 200 and "<html" not in html
    assert "data-share-native" in html
    assert ("https://t.me/share/url?url=https%3A%2F%2Fwww.google.com%2Fmaps"
            "%3Fq%3D41.311081%2C69.240562") in html
    assert "https://wa.me/?text=Joyli%20mijoz%0AChilonzor" in html
    assert "sms:?&amp;body=" in html
    assert "yandex.uz/maps/?pt=69.240562,41.311081" in html


def test_location_page_without_modal(client, seller_user, located):
    client.force_login(seller_user)
    html = client.get(f"/clients/{located.pk}/location/").content.decode()
    assert "<html" in html and "data-geo-share" in html


def test_location_modal_404_without_point(client, seller_user):
    client.force_login(seller_user)
    c = Client.objects.create(name="Joysiz", owner=seller_user)
    assert client.get(f"/clients/{c.pk}/location/").status_code == 404


def test_seller_cannot_open_another_sellers_client_location(client, admin_user, located):
    from accounts.models import User
    other = User.objects.create_user(username="other_seller", password="x",
                                     role=User.Role.SALES)
    client.force_login(other)
    assert client.get(f"/clients/{located.pk}/location/").status_code == 404
    client.force_login(admin_user)
    assert client.get(f"/clients/{located.pk}/location/").status_code == 200


def test_list_and_client_pages_link_the_location(client, seller_user, located):
    client.force_login(seller_user)
    Client.objects.create(name="Joysiz", owner=seller_user, address="Yunusobod")
    html = client.get("/clients/").content.decode()
    assert html.count("cell-loc--map") == 1
    assert f"/clients/{located.pk}/location/" in html
    history = client.get(f"/clients/{located.pk}/tarix/").content.decode()
    assert f"/clients/{located.pk}/location/" in history


def test_geo_parse_endpoint(client, seller_user):
    client.force_login(seller_user)
    ok = client.get("/geo/parse/", {"q": "geo:41.311081,69.240562"})
    assert ok.json() == {"lat": 41.311081, "lng": 69.240562, "text": "41.311081, 69.240562"}
    bad = client.get("/geo/parse/", {"q": "salom"})
    assert bad.status_code == 400 and "tanilmadi" in bad.json()["error"]


def test_geo_parse_needs_login(client):
    assert client.get("/geo/parse/", {"q": "41.3,69.2"}).status_code == 302
