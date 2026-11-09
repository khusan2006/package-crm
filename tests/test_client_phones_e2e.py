"""Extra phone numbers in a real browser: «+ Telefon» on the new-client form, the
+998 mask on the added row, saved, and the client found by the worker's number."""

import os

import pytest

from crm.models import Client

pytestmark = pytest.mark.e2e


def test_a_worker_number_is_added_and_finds_the_client(login, seller_user, live_server):
    page = login(seller_user)
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(f"{live_server.url}/clients/")
    page.click("a.fab")
    form = page.locator(".modal form")
    form.wait_for()
    form.locator("[name=name]").fill("Anvar savdo")
    form.locator("[data-xphones-add]").click()
    row = form.locator("[data-xphones-row]").first
    # The cursor goes to «kimning raqami»; the number box has the +998 mask.
    assert row.locator("[name=extra_phones_label]").evaluate("el => el === document.activeElement")
    row.locator("[name=extra_phones_label]").fill("ishchisi Aziz")
    row.locator("[name=extra_phones_phone]").click()
    page.keyboard.type("915556677")
    assert row.locator("[name=extra_phones_phone]").input_value() == "+998 91 555 66 77"
    # A second row, then taken away again.
    form.locator("[data-xphones-add]").click()
    form.locator("[data-xphones-row]").nth(1).locator("[data-xphones-remove]").click()
    assert form.locator("[data-xphones-row]").count() == 1
    if os.environ.get("SHOT_DIR"):
        page.locator("#modal").screenshot(path=os.path.join(os.environ["SHOT_DIR"], "client_form.png"))
    form.locator("button[type=submit]").click()
    page.wait_for_selector("text=mijozi qo'shildi")
    assert list(Client.objects.get(name="Anvar savdo").phones.values_list("phone", "label")) == [
        ("+998 91 555 66 77", "ishchisi Aziz")]

    page.goto(f"{live_server.url}/clients/?q=91 555 66")
    assert page.locator("text=Anvar savdo").count() >= 1
    assert page.locator("text=+998 91 555 66 77 · ishchisi Aziz").is_visible()
    if os.environ.get("SHOT_DIR"):
        page.locator("table").first.screenshot(path=os.path.join(os.environ["SHOT_DIR"], "client_list.png"))
