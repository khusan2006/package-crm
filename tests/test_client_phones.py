"""A client's extra numbers — their worker's, their accountant's: kept on the client
form, shown beside the main phone, and found by every search like the main one."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from crm.models import Client, ClientPhone, Payment, Sale
from crm.views import _client_search


def post_client(client, user, url, **data):
    client.force_login(user)
    base = {"name": "Anvar", "phone": "", "address": "", "location": "", "notes": ""}
    return client.post(url, {**base, **data}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")


@pytest.fixture
def anvar(db, sample_data):
    c = sample_data["client"]
    ClientPhone.objects.create(client=c, phone="+998 91 555 66 77", label="ishchisi Aziz")
    ClientPhone.objects.create(client=c, phone="+998 93 111 22 33")
    return c


def test_a_new_client_is_saved_with_extra_numbers(client, seller_user):
    resp = post_client(client, seller_user, "/clients/new/",
                       extra_phones_label=["ishchisi Aziz", "", ""],
                       extra_phones_phone=["+998 91 555 66 77", "+998 93 111 22 33", ""])
    assert resp.status_code == 204
    c = Client.objects.get(name="Anvar")
    assert list(c.phones.values_list("phone", "label")) == [
        ("+998 91 555 66 77", "ishchisi Aziz"), ("+998 93 111 22 33", "")]


def test_editing_replaces_the_numbers_and_says_so(client, seller_user, anvar):
    resp = post_client(client, seller_user, f"/clients/{anvar.pk}/edit/", name=anvar.name,
                       extra_phones_label=["hisobchi"], extra_phones_phone=["+998 99 000 11 22"])
    assert resp.status_code == 204
    assert list(anvar.phones.values_list("phone", "label")) == [("+998 99 000 11 22", "hisobchi")]
    from crm.models import AuditLog
    summary = AuditLog.objects.filter(target_id=anvar.pk).latest("pk").summary
    assert "+998 99 000 11 22 (hisobchi)" in summary


def test_the_form_shows_the_saved_numbers(client, seller_user, anvar):
    client.force_login(seller_user)
    body = client.get(f"/clients/{anvar.pk}/edit/").content.decode()
    assert 'name="extra_phones_phone" value="+998 91 555 66 77"' in body
    assert 'name="extra_phones_label" value="ishchisi Aziz"' in body


@pytest.mark.parametrize("phones,labels,error", [
    (["+998 91"], [""], "to&#x27;liq emas"),
    ([""], ["Aziz"], "uchun raqam yozing"),
])
def test_a_bad_row_is_refused(client, seller_user, phones, labels, error):
    resp = post_client(client, seller_user, "/clients/new/",
                       extra_phones_label=labels, extra_phones_phone=phones)
    assert resp.status_code == 422 and error in resp.content.decode()
    assert not Client.objects.exists()


def test_the_same_number_twice_is_kept_once(client, seller_user):
    post_client(client, seller_user, "/clients/new/", extra_phones_label=["", "Aziz"],
                extra_phones_phone=["+998 91 555 66 77", "91 555 66 77 "])
    assert Client.objects.get().phones.count() == 1


@pytest.mark.parametrize("term", ["915556677", "+998 91 555 66 77", "93 111", "Aziz"])
def test_every_search_finds_the_client_by_an_extra_number(anvar, term):
    assert list(_client_search(Client.objects.all(), term)) == [anvar]
    # Sales and payments: the client's one sale, once — two numbers don't double it.
    assert _client_search(Sale.objects.all(), term, "client").count() == 1
    assert _client_search(Payment.objects.all(), term, "sale__client").count() == 1


def test_another_number_finds_nothing(anvar):
    assert not _client_search(Client.objects.all(), "977777777").exists()


def test_the_client_list_finds_and_shows_the_worker(client, seller_user, anvar):
    client.force_login(seller_user)
    body = client.get("/clients/", {"q": "915556677"}).content.decode()
    assert anvar.name in body and "+998 91 555 66 77 · ishchisi Aziz" in body


def test_the_sale_form_picker_searches_extra_numbers_in_one_query(client, seller_user, anvar):
    for n in range(5):
        c = Client.objects.create(name=f"Boshqa {n}", owner=seller_user)
        ClientPhone.objects.create(client=c, phone=f"+998 90 000 00 0{n}")
    client.force_login(seller_user)
    with CaptureQueriesContext(connection) as ctx:
        body = client.get("/sales/new/").content.decode()
    assert "915556677" in body and "ishchisi aziz" in body
    assert sum("crm_clientphone" in q["sql"] for q in ctx.captured_queries) == 1

