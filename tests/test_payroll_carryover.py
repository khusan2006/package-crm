"""Xodimlar oyligi — the part that makes a month more than a fresh sheet.

A wage that was not paid in full is still owed next month, and money drawn beyond it
is still drawn. These tests pin down the running balance that follows from that, the
opening figure a payroll account starts from, and the rule that a raise prices the
months from which it was agreed — never the ones already settled.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from crm.models import Employee, Expense, SalaryRate

pytestmark = pytest.mark.django_db

WAGE = Decimal("2000000")


def this_month():
    return timezone.localdate().replace(day=1)


def last_month():
    return (this_month() - timedelta(days=1)).replace(day=1)


def make_worker(start=None, opening="0", salary=WAGE, name="Ишчи Аваз"):
    return Employee.objects.create(
        name=name, salary=salary,
        start_month=start or last_month(),
        opening_balance=Decimal(opening),
    )


def pay(worker, user, amount, on=None, counts=True):
    """A till payout tagged with the worker: a wage/advance (counts) or an errand they
    paid for on the firm's behalf (does not)."""
    return Expense.objects.create(
        date=on or timezone.localdate(),
        amount=Decimal(amount), category="Oylik / xodim", method="cash",
        employee=worker, counts_against_salary=counts, created_by=user,
    )


def month_of(day):
    return day.year, day.month


def test_unpaid_remainder_rides_into_the_next_month(seller_user):
    worker = make_worker()
    pay(worker, seller_user, "500000", on=last_month())
    # Last month: 2 000 000 earned, 500 000 drawn.
    assert worker.balance_through(*month_of(last_month())) == Decimal("1500000")
    # This month adds its own wage on top of what was left owing.
    assert worker.balance_through(*month_of(this_month())) == Decimal("3500000")


def test_drawing_ahead_carries_as_a_minus(seller_user):
    """Taking more than the month's wage is an advance against the next one, so it
    rides forward exactly like a shortfall does — in the other direction."""
    worker = make_worker()
    pay(worker, seller_user, "2500000", on=last_month())
    assert worker.balance_through(*month_of(last_month())) == Decimal("-500000")
    assert worker.balance_through(*month_of(this_month())) == Decimal("1500000")


def test_opening_balance_is_where_the_account_starts(seller_user):
    """Hired long before the CRM: the arrears are stated once, by hand, and nothing
    from before that month is counted again."""
    worker = make_worker(start=this_month(), opening="1000000")
    # A payout from before the account opened belongs to the period the opening
    # figure already summarises — counting it here would credit the firm twice.
    pay(worker, seller_user, "800000", on=last_month())
    assert worker.balance_through(*month_of(this_month())) == Decimal("3000000")


def test_nothing_accrues_before_the_start_month(seller_user):
    worker = make_worker(start=this_month())
    assert worker.accrued_in(*month_of(last_month())) == Decimal("0")
    assert worker.accrued_in(*month_of(this_month())) == WAGE


def test_a_raise_does_not_reprice_settled_months(client, admin_user):
    """The whole reason wages are dated: agreeing 3 mln from September must not turn
    August's settled 2 mln into 3 mln behind everyone's back."""
    client.force_login(admin_user)
    start = last_month()
    client.post(reverse("employee_create"), {
        "name": "Бригадир Шер", "salary": "2000000",
        "start_month": start.strftime("%Y-%m"), "opening_balance": "0",
        "is_active": "on",
    })
    worker = Employee.objects.get(name="Бригадир Шер")

    client.post(reverse("employee_edit", args=[worker.pk]), {
        "name": "Бригадир Шер", "salary": "3000000",
        "start_month": start.strftime("%Y-%m"), "opening_balance": "0",
        "salary_from": this_month().strftime("%Y-%m"), "is_active": "on",
    })
    worker.refresh_from_db()

    assert worker.salary_for(*month_of(start)) == Decimal("2000000")
    assert worker.salary_for(*month_of(this_month())) == Decimal("3000000")
    # Nothing paid yet, so the balance is simply the two months at their own rates.
    assert worker.balance_through(*month_of(this_month())) == Decimal("5000000")


def test_a_raise_cannot_start_before_the_account_does(client, admin_user):
    client.force_login(admin_user)
    worker = make_worker(start=this_month())
    response = client.post(reverse("employee_edit", args=[worker.pk]), {
        "name": worker.name, "salary": "3000000",
        "start_month": this_month().strftime("%Y-%m"), "opening_balance": "0",
        "salary_from": last_month().strftime("%Y-%m"), "is_active": "on",
    }, headers={"x-requested-with": "XMLHttpRequest"})
    assert response.status_code == 422
    worker.refresh_from_db()
    assert worker.salary == WAGE


def test_leaving_stops_the_wage(client, admin_user, seller_user):
    """Deactivating someone with history closes the account at this month: they must
    not keep earning through December for a job they left in August."""
    worker = make_worker()
    pay(worker, seller_user, "100000")
    client.force_login(admin_user)
    client.post(reverse("employee_delete", args=[worker.pk]))
    worker.refresh_from_db()
    assert worker.is_active is False
    assert worker.end_month == this_month()

    settled_now = worker.balance_through(*month_of(this_month()))
    nxt = (this_month() + timedelta(days=32)).replace(day=1)
    # Next month adds nothing — what was owed is still owed, no more.
    assert worker.balance_through(*month_of(nxt)) == settled_now


def test_page_shows_what_rode_in_and_what_rides_on(client, seller_user):
    worker = make_worker()
    pay(worker, seller_user, "500000", on=last_month())   # oylikning bir qismi
    pay(worker, seller_user, "300000")                    # shu oy avans
    pay(worker, seller_user, "120000", counts=False)      # benzin — oylikka tegmaydi

    client.force_login(seller_user)
    response = client.get(reverse("employee_list"))
    row = {r["employee"].pk: r for r in response.context["rows"]}[worker.pk]

    assert row["carried"] == Decimal("1500000")   # o'tgan oydan
    assert row["salary"] == WAGE
    assert row["due"] == Decimal("3500000")
    assert row["paid"] == Decimal("300000")       # benzin bu yerga kirmaydi
    assert row["remaining"] == Decimal("3200000")
    # The errand is listed, in its own table, and out of the wage math.
    assert len(response.context["errands"]) == 1
    assert response.context["errand_total"] == Decimal("120000")
    # "Pul berish" offers to settle the whole remainder — a plain number in the URL,
    # not the space-grouped figure the page prints.
    assert "summa=3200000" in response.content.decode()


def test_a_month_before_the_account_opened_shows_no_balance(client, seller_user):
    """The row still reports what the till paid that month — the payout list right
    below it does — but it claims no wage and no remainder for a month it never had."""
    worker = make_worker(start=this_month())
    pay(worker, seller_user, "400000", on=last_month())
    client.force_login(seller_user)
    response = client.get(
        reverse("employee_list"), {"oy": last_month().strftime("%Y-%m")}
    )
    row = {r["employee"].pk: r for r in response.context["rows"]}[worker.pk]
    assert row["tracked"] is False
    assert row["paid"] == Decimal("400000")
    assert row["remaining"] is None
    assert response.context["totals"]["remaining"] == Decimal("0")


def test_seller_can_add_a_worker_and_the_wage_is_dated(client, seller_user):
    client.force_login(seller_user)
    start = last_month()
    response = client.post(reverse("employee_create"), {
        "name": "Кассир Дилноза", "salary": "1500000",
        "start_month": start.strftime("%Y-%m"), "opening_balance": "250000",
        "is_active": "on",
    })
    assert response.status_code in (200, 302)
    worker = Employee.objects.get(name="Кассир Дилноза")
    assert worker.start_month == start
    assert worker.opening_balance == Decimal("250000")
    rate = SalaryRate.objects.get(employee=worker)
    assert (rate.effective_from, rate.amount) == (start, Decimal("1500000"))
