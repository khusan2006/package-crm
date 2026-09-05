"""Kassa Turkum filtri — bir nechta turkumni birdan belgilash.

Turkum is the one kassa filter that takes more than one answer: the drawer offers a
tick box per category, so it arrives as repeated `category=` params. These pin down
that several ticks narrow to all of them (not just the last one), that one tick still
behaves exactly as it did when the filter was a single select, and that an expense
filed under two categories at once fits in the column.
"""

from decimal import Decimal

import pytest
from django.utils import timezone

from crm.models import Expense

pytestmark = pytest.mark.django_db


def _expense(user, category, amount="10000"):
    return Expense.objects.create(
        date=timezone.localdate(), amount=Decimal(amount),
        amount_original=Decimal(amount), category=category,
        method="cash", created_by=user,
    )


@pytest.fixture
def expenses(admin_user):
    return {
        c: _expense(admin_user, c, amount)
        for c, amount in (
            ("Benzin / transport", "10000"),
            ("Ovqat (obed)", "20000"),
            ("Ijara", "30000"),
        )
    }


def _categories(response):
    return sorted(e.category for e in response.context["expenses"])


def test_no_filter_lists_every_category(client, admin_user, expenses):
    client.force_login(admin_user)
    resp = client.get("/kassa/")
    assert resp.status_code == 200
    assert resp.context["filters"]["category"] == []
    assert _categories(resp) == ["Benzin / transport", "Ijara", "Ovqat (obed)"]
    assert resp.context["active_filters"] == []


def test_one_ticked_category_narrows_to_it(client, admin_user, expenses):
    client.force_login(admin_user)
    resp = client.get("/kassa/", {"category": "Ijara"})
    assert _categories(resp) == ["Ijara"]
    assert resp.context["filters"]["category"] == ["Ijara"]
    # A category filter is expense-only: the kirim side is dropped, as before.
    assert resp.context["income_rows"] == []


def test_two_ticked_categories_keep_both(client, admin_user, expenses):
    client.force_login(admin_user)
    resp = client.get("/kassa/", {"category": ["Ijara", "Ovqat (obed)"]})
    assert _categories(resp) == ["Ijara", "Ovqat (obed)"]
    assert resp.context["outflow_total"] == Decimal("50000")


def test_chip_and_search_carry_every_ticked_category(client, admin_user, expenses):
    client.force_login(admin_user)
    resp = client.get("/kassa/", {"category": ["Ijara", "Ovqat (obed)"]})
    (chip,) = resp.context["active_filters"]
    assert chip["label"] == "Turkum"
    assert chip["value"] == "Ijara, Ovqat (obed)"
    # Removing the chip drops the whole filter, not just one of its values.
    assert "category" not in chip["remove_url"]
    # Searching from the toolbar must not quietly throw away the other ticks.
    kept = [k["value"] for k in resp.context["search_keep"] if k["name"] == "category"]
    assert kept == ["Ijara", "Ovqat (obed)"]


def test_drawer_marks_exactly_the_ticked_options(client, admin_user, expenses):
    client.force_login(admin_user)
    resp = client.get("/kassa/", {"category": ["Ijara", "Ovqat (obed)"]})
    html = resp.content.decode()
    assert '<option value="Ijara" selected' in html
    assert '<option value="Ovqat (obed)" selected' in html
    assert '<option value="Benzin / transport" selected' not in html
    assert 'name="category" multiple' in html


def test_blank_tick_is_not_a_filter(client, admin_user, expenses):
    """An empty value is "no category chosen", not a category nobody has used."""
    client.force_login(admin_user)
    resp = client.get("/kassa/", {"category": ["", "Ijara"]})
    assert resp.context["filters"]["category"] == ["Ijara"]
    assert _categories(resp) == ["Ijara"]


def test_several_categories_fit_in_one_expense(client, admin_user):
    """The form joins the ticks with ", " into the one free-text field — three of
    them is longer than any single label, which is why the column is wide."""
    combined = "Ovqat (obed), Benzin / transport, Mahsulot xaridi"
    expense = _expense(admin_user, combined)
    expense.full_clean()  # the max_length the form and the column agree on
    client.force_login(admin_user)
    resp = client.get("/kassa/", {"category": combined})
    assert _categories(resp) == [combined]
