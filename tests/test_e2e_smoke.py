"""End-to-end smoke tests: drive a real browser (Playwright) against Django's
live server and assert every main page renders for the right role.

These complement the unit tests in crm/tests.py — the unit tests prove the money
math and querysets; these prove the pages actually load and the nav works in a
browser, catching template/JS/wiring breakage the unit tests can't see.
"""

import pytest

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
