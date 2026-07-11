"""End-to-end smoke tests: drive a real browser (Playwright) against Django's
live server and assert every main page renders for the right role.

These complement the unit tests in crm/tests.py — the unit tests prove the money
math and querysets; these prove the pages actually load and the nav works in a
browser, catching template/JS/wiring breakage the unit tests can't see.
"""

import pytest

from crm.models import Client

pytestmark = pytest.mark.e2e


# Pages every logged-in user may open (admin sees them all).
COMMON_PAGES = [
    ("/", "Asosiy"),
    ("/sales/", "Sotuvlar"),
    ("/debts/", "Qarzlar"),
    ("/kassa/", "Kassa"),
    ("/clients/", "Mijozlar"),
    ("/products/", "Ombor"),
]


def test_login_lands_on_dashboard(login, admin_user):
    page = login(admin_user)
    assert "Asosiy" in page.title()
    # The KPI cards the dashboard is built around are present.
    assert page.locator("text=SAVDO").first.is_visible()


@pytest.mark.parametrize("path,heading", COMMON_PAGES)
def test_main_pages_load_for_admin(login, admin_user, sample_data, live_server, path, heading):
    page = login(admin_user)
    page.goto(f"{live_server.url}{path}")
    # No server error page, and the expected section heading/title is shown.
    assert page.locator("body").is_visible()
    assert heading in page.content()


def test_admin_sees_user_management_link(login, admin_user):
    page = login(admin_user)
    assert page.locator('a[href="/users/"]').count() == 1


def test_seller_does_not_see_user_management_link(login, seller_user):
    page = login(seller_user)
    # User management is admin-only; the seller's sidebar must not link to it.
    assert page.locator('a[href="/users/"]').count() == 0


def test_dashboard_shows_sample_sale_total(login, admin_user, sample_data, live_server):
    page = login(admin_user)
    page.goto(f"{live_server.url}/sales/")
    # The sample client's sale should appear in the sales list.
    assert "Test mijoz" in page.content()


def _open_client_picker(page, live_server):
    """Open the sale form and return the client combobox locator, dropdown opened."""
    page.goto(f"{live_server.url}/sales/new/")
    box = page.locator(".combobox:has(#id_client)")
    box.locator(".combobox-input").click()
    return box


def test_sale_client_search_combines_name_and_address(login, admin_user, live_server, db):
    # Two clients named "Ali"; only one lives in Chilonzor.
    ali_chilonzor = Client.objects.create(
        name="Ali Valiyev", phone="+998 90 111 22 33",
        address="Chilonzor tumani", owner=admin_user,
    )
    Client.objects.create(
        name="Ali Karimov", phone="+998 91 444 55 66",
        address="Yunusobod tumani", owner=admin_user,
    )
    page = login(admin_user)
    box = _open_client_picker(page, live_server)
    box.locator(".combobox-input").fill("ali chilonzor")

    options = box.locator(".combobox-option")
    # Name + address combined narrows to the single matching Ali.
    assert options.count() == 1
    assert "Ali Valiyev" in options.first.inner_text()
    # The phone · address subtitle is shown to disambiguate.
    assert box.locator(".combobox-sub").first.is_visible()

    # Choosing it fills the hidden <select> the form actually submits.
    options.first.click()
    assert box.locator(".combobox-input").input_value() == "Ali Valiyev"
    assert page.locator("#id_client").input_value() == str(ali_chilonzor.pk)


def test_sale_client_search_by_phone_digits(login, admin_user, live_server, db):
    # Typing spaced/partial phone digits still matches the stored "+998 …" number.
    Client.objects.create(
        name="Ali Valiyev", phone="+998 90 111 22 33",
        address="Chilonzor", owner=admin_user,
    )
    karimov = Client.objects.create(
        name="Ali Karimov", phone="+998 91 444 55 66",
        address="Yunusobod", owner=admin_user,
    )
    page = login(admin_user)
    box = _open_client_picker(page, live_server)
    box.locator(".combobox-input").fill("444 55")

    options = box.locator(".combobox-option")
    assert options.count() == 1
    assert "Ali Karimov" in options.first.inner_text()
    options.first.click()
    assert page.locator("#id_client").input_value() == str(karimov.pk)


def test_sale_client_search_ignores_phone_formatting(login, admin_user, live_server, db):
    # A "+"-prefixed query that also crosses the number's spaces (e.g. "+99891444"
    # vs the stored "+998 91 444 55 66") must still match on the digits alone.
    karimov = Client.objects.create(
        name="Ali Karimov", phone="+998 91 444 55 66",
        address="Yunusobod", owner=admin_user,
    )
    Client.objects.create(
        name="Ali Valiyev", phone="+998 90 111 22 33",
        address="Chilonzor", owner=admin_user,
    )
    page = login(admin_user)
    box = _open_client_picker(page, live_server)
    box.locator(".combobox-input").fill("+99891444")

    options = box.locator(".combobox-option")
    assert options.count() == 1
    assert "Ali Karimov" in options.first.inner_text()
    options.first.click()
    assert page.locator("#id_client").input_value() == str(karimov.pk)
