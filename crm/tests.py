from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import AuditLog, Client, Payment, Product, Sale, SaleItem, StockEntry


def make_sale(client, rep, product, weight="10", price="24000", **kwargs):
    """Create a sale (header) with a single line item.

    Every sale is a receivable; pass is_debt=True to leave it unpaid, otherwise
    a full cash payment is recorded so the sale reads as paid (the old default).
    Header kwargs (date, debt_deadline) go to the Sale; cost_price/dimension to
    the item."""
    is_debt = kwargs.pop("is_debt", False)
    deadline = kwargs.pop("debt_deadline", None)
    cost_price = kwargs.pop("cost_price", product.cost_price)
    dimension = kwargs.pop("dimension", "kg")
    header = {"client": client, "sales_rep": rep}
    if "date" in kwargs:
        header["date"] = kwargs.pop("date")
    header["debt_deadline"] = deadline or (timezone.localdate() + timedelta(days=7))
    sale = Sale.objects.create(**header)
    SaleItem.objects.create(
        sale=sale,
        product=product,
        dimension=dimension,
        weight=Decimal(weight),
        price=Decimal(price),
        cost_price=Decimal(cost_price),
    )
    if not is_debt:
        Payment.objects.create(
            sale=sale, amount=sale.total_price, method=Payment.Method.CASH,
            kind=Payment.Kind.SALE, date=sale.date, created_by=rep,
        )
    return sale


def sale_post(client_pk, items, **header):
    """Build POST data for the sale create/edit form (header + item formset)."""
    data = {
        "date": timezone.localdate().isoformat(),
        "client": client_pk,
        "items-TOTAL_FORMS": str(len(items)),
        "items-INITIAL_FORMS": "0",
        "items-MIN_NUM_FORMS": "1",
        "items-MAX_NUM_FORMS": "1000",
    }
    for i, item in enumerate(items):
        for key, value in item.items():
            data[f"items-{i}-{key}"] = value
    data.update(header)
    return data


def one_item(product, weight="5", price="24000", dimension="kg", cost_price=""):
    return {
        "product": product.pk,
        "dimension": dimension,
        "weight": weight,
        "price": price,
        "cost_price": cost_price,
    }


class BaseSetup(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("t_admin", password="x", role=User.Role.ADMIN)
        cls.manager = User.objects.create_user("t_manager", password="x", role=User.Role.MANAGER)
        cls.sales1 = User.objects.create_user("t_sales1", password="x", role=User.Role.SALES)
        cls.sales2 = User.objects.create_user("t_sales2", password="x", role=User.Role.SALES)
        cls.client1 = Client.objects.create(name="Mijoz A", owner=cls.sales1)
        cls.client2 = Client.objects.create(name="Mijoz B", owner=cls.sales2)
        cls.product = Product.objects.create(
            name="Polietilen paket", sku="PKT-1",
            cost_price=Decimal("18000"), price=Decimal("24000"),
        )
        cls.sale1 = make_sale(cls.client1, cls.sales1, cls.product)
        cls.sale2 = make_sale(cls.client2, cls.sales2, cls.product)


class SaleMathTests(BaseSetup):
    def test_totals_and_profit(self):
        # 10 kg × 24 000 = 240 000; tannarx 10 × 18 000 = 180 000; foyda 60 000
        self.assertEqual(self.sale1.total_price, Decimal("240000"))
        self.assertEqual(self.sale1.total_cost, Decimal("180000"))
        self.assertEqual(self.sale1.profit, Decimal("60000"))

    def test_with_totals_annotation(self):
        sale = Sale.objects.with_totals().get(pk=self.sale1.pk)
        self.assertEqual(sale.total, Decimal("240000"))
        self.assertEqual(sale.profit_total, Decimal("60000"))

    def test_cost_price_for_grams(self):
        self.assertEqual(self.product.cost_price_for(Sale.Dimension.G), Decimal("18"))


class DebtTests(BaseSetup):
    def _sale_data(self, **overrides):
        return sale_post(self.client1.pk, [one_item(self.product)], **overrides)

    def test_new_sale_is_outstanding_with_no_payment(self):
        self.client.force_login(self.sales1)
        response = self.client.post(reverse("sale_create"), self._sale_data())
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest("created_at")
        self.assertTrue(sale.is_outstanding)
        self.assertEqual(sale.payments.count(), 0)

    def test_blank_deadline_defaults_to_seven_days(self):
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_create"), self._sale_data(debt_deadline=""))
        sale = Sale.objects.latest("created_at")
        self.assertEqual(sale.debt_deadline, timezone.localdate() + timedelta(days=7))

    def test_explicit_deadline_is_saved(self):
        self.client.force_login(self.sales1)
        deadline = (timezone.localdate() + timedelta(days=30)).isoformat()
        self.client.post(reverse("sale_create"), self._sale_data(debt_deadline=deadline))
        sale = Sale.objects.latest("created_at")
        self.assertEqual(sale.debt_deadline.isoformat(), deadline)

    def test_overdue_flag(self):
        overdue = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=timezone.localdate() - timedelta(days=1),
        )
        not_due = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=timezone.localdate() + timedelta(days=1),
        )
        self.assertTrue(overdue.is_overdue)
        self.assertFalse(not_due.is_overdue)


class CostFallbackTests(BaseSetup):
    def test_empty_cost_price_uses_product_cost(self):
        self.client.force_login(self.sales1)
        data = sale_post(
            self.client1.pk,
            [one_item(self.product, weight="500", price="24", dimension="g")],
        )
        response = self.client.post(reverse("sale_create"), data)
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest("created_at")
        item = sale.items.get()
        # per-gram tannarx = 18000 / 1000
        self.assertEqual(item.cost_price, Decimal("18"))
        self.assertEqual(sale.sales_rep, self.sales1)


class MultiItemSaleTests(BaseSetup):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.product2 = Product.objects.create(
            name="Mayka paket", sku="MYK-1",
            cost_price=Decimal("17000"), price=Decimal("23000"),
        )

    def test_creates_one_sale_with_multiple_items(self):
        self.client.force_login(self.sales1)
        data = sale_post(
            self.client1.pk,
            [
                one_item(self.product, weight="10", price="24000"),   # 240 000
                one_item(self.product2, weight="5", price="23000"),   # 115 000
            ],
        )
        response = self.client.post(reverse("sale_create"), data)
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest("created_at")
        self.assertEqual(sale.items.count(), 2)
        self.assertEqual(sale.total_price, Decimal("355000"))
        # a new receipt is an unpaid receivable — no payment recorded yet
        self.assertEqual(sale.payments.count(), 0)
        self.assertTrue(sale.is_outstanding)

    def test_requires_at_least_one_item(self):
        self.client.force_login(self.sales1)
        data = sale_post(self.client1.pk, [])
        response = self.client.post(reverse("sale_create"), data)
        self.assertEqual(response.status_code, 200)  # re-rendered, not saved
        self.assertFalse(Sale.objects.filter(client=self.client1).exclude(pk=self.sale1.pk).exists())


class RoleScopingTests(BaseSetup):
    def test_sales_sees_only_own_sales(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_list"))
        self.assertEqual(list(response.context["page"].object_list), [self.sale1])

    def test_manager_sees_all_sales(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("sale_list"))
        self.assertEqual(len(response.context["page"].object_list), 2)

    def test_sales_cannot_edit_others_sale(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_edit", args=[self.sale2.pk]))
        self.assertEqual(response.status_code, 404)

    def test_sales_sees_only_own_clients(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("client_list"))
        self.assertEqual(list(response.context["page"].object_list), [self.client1])

    def test_sales_cannot_create_product(self):
        self.client.force_login(self.sales1)
        self.assertEqual(self.client.get(reverse("product_create")).status_code, 403)

    def test_manager_can_create_product(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("product_create")).status_code, 200)

    def test_only_admin_can_manage_users(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 200)

    def test_sales_cannot_pick_others_client(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_create"))
        self.assertNotIn(self.client2, response.context["form"].fields["client"].queryset)


class AuthTests(BaseSetup):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_dashboard_renders_for_each_role(self):
        for user in (self.admin, self.manager, self.sales1):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_dashboard_provides_chart_data(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("dashboard"))
        ctx = response.context
        self.assertEqual(len(ctx["monthly"]), 6)              # 6-month trend
        self.assertIn("cost_pct", ctx["monthly"][0])
        self.assertEqual(len(ctx["donut"]["segments"]), 3)    # cash / card / transfer
        self.assertIsNotNone(ctx["donut"]["grand_short"])
        self.assertTrue(all(hasattr(c, "pct") for c in ctx["top_clients"]))
        # Numbers inside style/SVG attributes must stay unlocalised — a comma
        # decimal separator silently breaks CSS lengths and SVG dash arrays.
        body = response.content.decode()
        self.assertNotRegex(body, r"(?:height|width):\s*\d+,\d")
        self.assertNotRegex(body, r'stroke-dash(?:array|offset)="[^"]*,')

    def test_dashboard_flags_overdue_debt(self):
        make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=timezone.localdate() - timedelta(days=3),
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.context["overdue_count"], 1)
        self.assertEqual(response.context["overdue_total"], Decimal("240000"))
        self.assertContains(response, "overdue-banner")


class DayViewTests(BaseSetup):
    def test_defaults_to_today(self):
        old = make_sale(
            self.client1, self.sales1, self.product,
            date=timezone.localdate() - timedelta(days=3),
        )
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_list"))
        sales = list(response.context["page"].object_list)
        self.assertIn(self.sale1, sales)   # today
        self.assertNotIn(old, sales)       # 3 days ago
        self.assertTrue(response.context["is_today"])

    def test_shows_selected_day(self):
        target = timezone.localdate() - timedelta(days=3)
        old = make_sale(self.client1, self.sales1, self.product, date=target)
        self.client.force_login(self.sales1)
        response = self.client.get(
            reverse("sale_list"), {"dan": target.isoformat(), "gacha": target.isoformat()}
        )
        sales = list(response.context["page"].object_list)
        self.assertIn(old, sales)
        self.assertNotIn(self.sale1, sales)  # today's sale not on the target day
        self.assertFalse(response.context["is_today"])
        self.assertTrue(response.context["is_single_day"])

    def test_date_range_spans_multiple_days(self):
        today = timezone.localdate()
        d5 = make_sale(self.client1, self.sales1, self.product, date=today - timedelta(days=5))
        d2 = make_sale(self.client1, self.sales1, self.product, date=today - timedelta(days=2))
        old = make_sale(self.client1, self.sales1, self.product, date=today - timedelta(days=20))
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_list"), {
            "dan": (today - timedelta(days=7)).isoformat(),
            "gacha": today.isoformat(),
        })
        sales = list(response.context["page"].object_list)
        self.assertIn(d5, sales)
        self.assertIn(d2, sales)
        self.assertIn(self.sale1, sales)     # today, within range
        self.assertNotIn(old, sales)         # 20 days ago, outside range
        self.assertFalse(response.context["is_single_day"])

    def test_filter_ignores_date_range(self):
        # An out-of-window sale must surface once a content filter is applied
        old = make_sale(
            self.client1, self.sales1, self.product,
            date=timezone.localdate() - timedelta(days=30),
        )
        self.client.force_login(self.sales1)
        default = self.client.get(reverse("sale_list"))
        self.assertNotIn(old, list(default.context["page"].object_list))
        self.assertFalse(default.context["has_filters"])

        filtered = self.client.get(reverse("sale_list"), {"client": self.client1.pk})
        self.assertIn(old, list(filtered.context["page"].object_list))  # date window bypassed
        self.assertTrue(filtered.context["has_filters"])
        chips = filtered.context["active_filters"]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0]["label"], "Mijoz")
        self.assertEqual(chips[0]["value"], self.client1.name)


class StockTests(BaseSetup):
    def setUp(self):
        # BaseSetup already created sale1 (10 kg) + sale2 for self.product
        StockEntry.objects.create(
            product=self.product, quantity_kg=Decimal("100"), created_by=self.admin
        )

    def test_current_stock_is_received_minus_sold(self):
        # 100 kg in; two 10 kg sales out (sale1 + sale2) => 80 kg
        self.product.refresh_from_db()
        self.assertEqual(self.product.total_received, Decimal("100"))
        self.assertEqual(self.product.total_sold, Decimal("20"))
        self.assertEqual(self.product.current_stock, Decimal("80"))

    def test_gram_sale_converts_to_kg(self):
        make_sale(self.client1, self.sales1, self.product, weight="500", dimension="g")
        # 500 g = 0.5 kg extra sold => 20.5 kg out; 100 - 20.5 = 79.5
        self.assertEqual(self.product.total_sold, Decimal("20.500"))
        self.assertEqual(self.product.current_stock, Decimal("79.500"))

    def test_with_stock_annotation_matches_property(self):
        annotated = Product.objects.with_stock().get(pk=self.product.pk)
        self.assertEqual(annotated.stock, self.product.current_stock)

    def test_manager_can_add_kirim(self):
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("stock_entry_create", args=[self.product.pk]),
            {"date": timezone.localdate().isoformat(), "quantity_kg": "250", "note": "Yangi partiya"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.product.total_received, Decimal("350"))
        entry = StockEntry.objects.latest("created_at")
        self.assertEqual(entry.created_by, self.manager)

    def test_sales_cannot_add_kirim(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("stock_entry_create", args=[self.product.pk]))
        self.assertEqual(response.status_code, 403)

    def test_sale_beyond_stock_warns_but_saves(self):
        self.client.force_login(self.sales1)
        data = sale_post(
            self.client1.pk,
            [one_item(self.product, weight="500")],  # far beyond the 80 kg on hand
        )
        response = self.client.post(reverse("sale_create"), data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(SaleItem.objects.filter(weight=Decimal("500")).exists())
        msgs = [m.message for m in response.context["messages"]]
        self.assertTrue(any("yetarli emas" in m for m in msgs))

    def test_low_stock_flag(self):
        self.product.low_stock_threshold = Decimal("90")
        self.product.save()
        # current stock 80 <= threshold 90 => low
        self.assertTrue(self.product.is_low_stock)

    def test_product_detail_accessible_to_sales(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("product_detail", args=[self.product.pk]))
        self.assertEqual(response.status_code, 200)

    def test_adjust_sets_exact_quantity(self):
        # current stock is 80 (100 in - 20 sold)
        self.client.force_login(self.manager)
        before = StockEntry.objects.count()
        response = self.client.post(
            reverse("stock_adjust", args=[self.product.pk]), {"quantity": "200", "note": ""}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.product.current_stock, Decimal("200.000"))
        # exactly one movement logged, holding the +120 delta
        self.assertEqual(StockEntry.objects.count(), before + 1)
        self.assertEqual(StockEntry.objects.latest("created_at").quantity_kg, Decimal("120.000"))

    def test_adjust_can_decrease_below_current(self):
        self.client.force_login(self.manager)
        self.client.post(
            reverse("stock_adjust", args=[self.product.pk]), {"quantity": "50"}
        )
        self.assertEqual(self.product.current_stock, Decimal("50.000"))
        self.assertEqual(StockEntry.objects.latest("created_at").quantity_kg, Decimal("-30.000"))

    def test_sales_cannot_adjust(self):
        self.client.force_login(self.sales1)
        self.assertEqual(
            self.client.get(reverse("stock_adjust", args=[self.product.pk])).status_code, 403
        )


class SaleFilterExportTests(BaseSetup):
    def setUp(self):
        self.debt_sale = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=timezone.localdate() + timedelta(days=10),
        )

    def test_filter_by_client(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("sale_list"), {"client": self.client2.pk})
        for sale in response.context["page"].object_list:
            self.assertEqual(sale.client, self.client2)

    def test_debtors_count_is_distinct_clients(self):
        # A second debt sale for the same client must not double-count
        make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=timezone.localdate() + timedelta(days=5),
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("sale_list"))
        self.assertEqual(response.context["totals"]["debtors"], 1)

    def test_filter_by_status_debt(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_list"), {"status": "debt"})
        sales = list(response.context["page"].object_list)
        self.assertIn(self.debt_sale, sales)
        self.assertTrue(all(s.is_outstanding for s in sales))

    def test_export_returns_csv_scoped_and_filtered(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_export"), {"status": "debt"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode("utf-8")
        lines = [ln for ln in body.splitlines() if ln.strip()]
        # header + exactly the one debt sale owned by sales1
        self.assertEqual(len(lines), 2)
        self.assertIn("Mijoz", lines[0])

    def test_sales_export_excludes_other_reps(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_export"))
        body = response.content.decode("utf-8")
        self.assertNotIn(self.client2.name, body)  # client2 belongs to sales2


class DebtPageTests(BaseSetup):
    def setUp(self):
        today = timezone.localdate()
        # client1: two open debts (one overdue, one due soon) plus a paid sale
        self.overdue = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=today - timedelta(days=5),
        )
        self.soon = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=today + timedelta(days=3),
        )
        self.paid = make_sale(self.client1, self.sales1, self.product)

    def test_debtors_grouped_by_client(self):
        self.client.force_login(self.sales1)
        ctx = self.client.get(reverse("debt_list")).context
        debtors = ctx["debtors"]
        self.assertEqual(len(debtors), 1)  # only client1 owes money
        group = debtors[0]
        self.assertEqual(group["client"], self.client1)
        self.assertEqual(group["count"], 2)               # two open receipts
        self.assertEqual(group["overdue_count"], 1)       # one is overdue
        self.assertEqual(group["earliest"], timezone.localdate() - timedelta(days=5))
        self.assertEqual(group["remaining"], Decimal("480000"))  # 2 × 240000

    def test_paid_client_not_listed(self):
        # client2 only has a paid sale → should not appear as a debtor
        make_sale(self.client2, self.sales2, self.product)
        self.client.force_login(self.manager)
        ctx = self.client.get(reverse("debt_list")).context
        self.assertNotIn(self.client2, {g["client"] for g in ctx["debtors"]})

    def test_scoped_to_sales_rep(self):
        make_sale(
            self.client2, self.sales2, self.product,
            is_debt=True, debt_deadline=timezone.localdate() - timedelta(days=2),
        )
        self.client.force_login(self.sales1)
        ctx = self.client.get(reverse("debt_list")).context
        self.assertEqual({g["client"] for g in ctx["debtors"]}, {self.client1})

    def test_client_debt_detail_lists_open_receipts(self):
        self.client.force_login(self.sales1)
        ctx = self.client.get(reverse("debt_client", args=[self.client1.pk])).context
        self.assertEqual(set(ctx["sales"]), {self.overdue, self.soon})
        self.assertNotIn(self.paid, ctx["sales"])
        self.assertEqual(ctx["total"], Decimal("480000"))

    def test_client_debt_detail_scoped(self):
        # sales1 cannot open another rep's client debt page
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("debt_client", args=[self.client2.pk]))
        self.assertEqual(response.status_code, 404)


class SaleDetailTests(BaseSetup):
    def test_detail_shows_sale_and_payments(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_detail", args=[self.sale1.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["sale"], self.sale1)

    def test_detail_scoped_to_owner(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("sale_detail", args=[self.sale2.pk]))
        self.assertEqual(response.status_code, 404)


class PaymentTests(BaseSetup):
    def _debt_sale(self):
        # 10 kg × 24000 = 240000 total
        return make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=timezone.localdate() + timedelta(days=5),
        )

    def test_new_sale_records_no_payment(self):
        self.client.force_login(self.sales1)
        data = sale_post(self.client1.pk, [one_item(self.product, weight="10")])
        self.client.post(reverse("sale_create"), data)
        sale = Sale.objects.latest("created_at")
        self.assertEqual(sale.payments.count(), 0)
        self.assertTrue(sale.is_outstanding)

    def test_mark_paid_settles_the_sale(self):
        # 10 kg × 24000 = 240000; one click records a full cash payment
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_mark_paid", args=[sale.pk]))
        sale.refresh_from_db()
        self.assertTrue(sale.is_paid)
        self.assertEqual(sale.paid_amount, Decimal("240000"))
        payment = sale.payments.get()
        self.assertEqual(payment.amount, Decimal("240000"))
        self.assertEqual(payment.method, "cash")

    def test_partial_debt_payment_keeps_debt_open(self):
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_pay", args=[sale.pk]), {"amount": "100000", "method": "cash"})
        sale.refresh_from_db()
        self.assertTrue(sale.is_outstanding)  # still owed
        self.assertEqual(sale.debt_remaining, Decimal("140000"))

    def test_full_debt_payment_closes_debt(self):
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_pay", args=[sale.pk]), {"amount": "240000", "method": "card"})
        sale.refresh_from_db()
        self.assertTrue(sale.is_paid)
        self.assertEqual(sale.payments.get().kind, "debt")

    def test_transfer_payment_records_commission_from_percent(self):
        sale = self._debt_sale()  # 240000 debt
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("sale_pay", args=[sale.pk]),
            {
                "amount": "200000",
                "method": "transfer",
                "commission_percent": "1.5",
                "note": "Bank o'tkazma",
            },
        )
        sale.refresh_from_db()
        payment = sale.payments.get()
        self.assertEqual(payment.method, "transfer")
        self.assertEqual(payment.commission_percent, Decimal("1.5"))
        self.assertEqual(payment.commission, Decimal("3000.00"))  # 200000 × 1.5%
        self.assertEqual(payment.net_amount, Decimal("197000.00"))  # what hits the till
        self.assertEqual(payment.note, "Bank o'tkazma")
        # Only the net (amount − commission) reduces the debt; the client bears the fee
        self.assertEqual(sale.debt_remaining, Decimal("43000"))  # 240000 − 197000

    def test_commission_ignored_for_non_transfer(self):
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("sale_pay", args=[sale.pk]),
            {"amount": "100000", "method": "cash", "commission_percent": "5"},
        )
        payment = sale.payments.get()
        self.assertEqual(payment.commission, Decimal("0"))
        self.assertEqual(payment.commission_percent, Decimal("0"))

    def test_transfer_grossed_up_settles_debt(self):
        # A transfer can be grossed up over the balance so the net clears it:
        # 240000 / 0.96 = 250000, a 4% fee of 10000 leaves 240000 net.
        sale = self._debt_sale()  # 240000
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("sale_pay", args=[sale.pk]),
            {"amount": "250000", "method": "transfer", "commission_percent": "4"},
        )
        sale.refresh_from_db()
        self.assertTrue(sale.is_paid)
        payment = sale.payments.get()
        self.assertEqual(payment.commission, Decimal("10000.00"))
        self.assertEqual(payment.net_amount, Decimal("240000.00"))
        self.assertEqual(sale.debt_remaining, Decimal("0"))

    def test_client_debt_pay_transfer_credits_net(self):
        # 200000 transfer at 5% → 10000 fee, 190000 net credited to the debt.
        sale = self._debt_sale()  # 240000
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("client_debt_pay", args=[self.client1.pk]),
            {"amount": "200000", "method": "transfer", "commission_percent": "5"},
        )
        sale.refresh_from_db()
        payment = sale.payments.get()
        self.assertEqual(payment.amount, Decimal("200000.00"))
        self.assertEqual(payment.commission, Decimal("10000.00"))
        self.assertEqual(payment.net_amount, Decimal("190000.00"))
        self.assertEqual(sale.debt_remaining, Decimal("50000"))  # 240000 − 190000

    def test_client_debt_pay_distributes_fifo(self):
        today = timezone.localdate()
        older = make_sale(
            self.client1, self.sales1, self.product, is_debt=True, date=today - timedelta(days=10)
        )
        newer = make_sale(
            self.client1, self.sales1, self.product, is_debt=True, date=today - timedelta(days=2)
        )
        self.client.force_login(self.sales1)
        # 300000 clears the older 240000 debt in full, then 60000 of the newer
        self.client.post(
            reverse("client_debt_pay", args=[self.client1.pk]),
            {"amount": "300000", "method": "cash"},
        )
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertTrue(older.is_paid)  # oldest debt cleared first (FIFO)
        self.assertEqual(older.paid_amount, Decimal("240000"))
        self.assertEqual(newer.debt_remaining, Decimal("180000"))  # partially paid
        self.assertEqual(older.payments.filter(kind="debt").count(), 1)
        self.assertEqual(newer.payments.filter(kind="debt").count(), 1)

    def test_client_debt_pay_capped_at_total(self):
        sale = self._debt_sale()  # 240000
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("client_debt_pay", args=[self.client1.pk]),
            {"amount": "999999999", "method": "cash"},
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        sale.refresh_from_db()
        self.assertTrue(sale.is_outstanding)
        self.assertEqual(sale.payments.count(), 0)

    def test_payment_cannot_exceed_remaining(self):
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("sale_pay", args=[sale.pk]), {"amount": "999999", "method": "cash"}
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        sale.refresh_from_db()
        self.assertTrue(sale.is_outstanding)
        self.assertEqual(sale.payments.count(), 0)

    def test_ledger_scoped_to_sales_rep(self):
        mine = self._debt_sale()
        Payment.objects.create(sale=mine, amount=Decimal("50000"), method="cash", kind="debt", created_by=self.sales1)
        others = make_sale(self.client2, self.sales2, self.product, is_debt=True, debt_deadline=timezone.localdate())
        Payment.objects.create(sale=others, amount=Decimal("50000"), method="cash", kind="debt", created_by=self.sales2)
        self.client.force_login(self.sales1)
        rows = list(self.client.get(reverse("payment_list")).context["page"].object_list)
        self.assertIn(mine, [p.sale for p in rows])
        self.assertNotIn(others, [p.sale for p in rows])


class SaleIntegrityTests(BaseSetup):
    def _paid_sale(self):
        # make_sale with is_debt=False books a full cash payment (240000)
        return make_sale(self.client1, self.sales1, self.product)

    def _edit_post(self, sale, weight):
        item = sale.items.get()
        return {
            "date": sale.date.isoformat(),
            "client": self.client1.pk,
            "debt_deadline": sale.debt_deadline.isoformat(),
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "1",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-id": item.pk,
            "items-0-product": self.product.pk,
            "items-0-dimension": "kg",
            "items-0-weight": weight,
            "items-0-price": "24000",
            "items-0-cost_price": "18000",
        }

    def test_cannot_delete_sale_with_payments(self):
        sale = self._paid_sale()
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_delete", args=[sale.pk]))
        self.assertTrue(Sale.objects.filter(pk=sale.pk).exists())

    def test_can_delete_sale_without_payments(self):
        sale = make_sale(self.client1, self.sales1, self.product, is_debt=True)
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_delete", args=[sale.pk]))
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())

    def test_edit_cannot_drop_total_below_paid(self):
        sale = self._paid_sale()  # 240000 paid in full
        self.client.force_login(self.sales1)
        # 5 kg × 24000 = 120000, below the 240000 already paid — must be rejected
        response = self.client.post(
            reverse("sale_edit", args=[sale.pk]), self._edit_post(sale, "5")
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        self.assertEqual(sale.items.get().weight, Decimal("10"))  # unchanged

    def test_edit_allowed_when_total_stays_at_or_above_paid(self):
        sale = self._paid_sale()
        self.client.force_login(self.sales1)
        # 12 kg × 24000 = 288000, above the 240000 paid — allowed
        self.client.post(reverse("sale_edit", args=[sale.pk]), self._edit_post(sale, "12"))
        self.assertEqual(sale.items.get().weight, Decimal("12"))

    def test_sale_rejects_zero_weight(self):
        self.client.force_login(self.sales1)
        before = Sale.objects.count()
        data = sale_post(self.client1.pk, [one_item(self.product, weight="0")])
        self.client.post(reverse("sale_create"), data)
        self.assertEqual(Sale.objects.count(), before)

    def test_sale_rejects_zero_price(self):
        self.client.force_login(self.sales1)
        before = Sale.objects.count()
        data = sale_post(self.client1.pk, [one_item(self.product, weight="5", price="0")])
        self.client.post(reverse("sale_create"), data)
        self.assertEqual(Sale.objects.count(), before)


class PaymentVoidTests(BaseSetup):
    def test_manager_can_void_payment_and_restore_debt(self):
        sale = make_sale(self.client1, self.sales1, self.product)  # paid 240000
        payment = sale.payments.get()
        self.client.force_login(self.manager)
        self.client.post(reverse("payment_delete", args=[payment.pk]))
        sale.refresh_from_db()
        self.assertFalse(Payment.objects.filter(pk=payment.pk).exists())
        self.assertTrue(sale.is_outstanding)
        self.assertEqual(sale.debt_remaining, Decimal("240000"))

    def test_sales_cannot_void_payment(self):
        sale = make_sale(self.client1, self.sales1, self.product)
        payment = sale.payments.get()
        self.client.force_login(self.sales1)
        response = self.client.post(reverse("payment_delete", args=[payment.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Payment.objects.filter(pk=payment.pk).exists())

    def test_void_frees_sale_for_deletion(self):
        # The delete guard blocks a paid sale; voiding its payment unblocks it
        sale = make_sale(self.client1, self.sales1, self.product)
        payment = sale.payments.get()
        self.client.force_login(self.manager)
        self.client.post(reverse("payment_delete", args=[payment.pk]))
        self.client.post(reverse("sale_delete", args=[sale.pk]))
        self.assertFalse(Sale.objects.filter(pk=sale.pk).exists())


class QuickAddClientTests(BaseSetup):
    def test_creates_client_owned_by_current_user(self):
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("client_quick_create"), {"name": "Tez Mijoz", "phone": "+998900000000"}
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["text"], "Tez Mijoz")
        self.assertEqual(Client.objects.get(pk=data["id"]).owner, self.sales1)

    def test_requires_name(self):
        self.client.force_login(self.sales1)
        response = self.client.post(reverse("client_quick_create"), {"name": "  "})
        self.assertEqual(response.status_code, 400)

    def test_get_not_allowed(self):
        self.client.force_login(self.sales1)
        self.assertEqual(self.client.get(reverse("client_quick_create")).status_code, 405)


class ClientDuplicateTests(BaseSetup):
    # BaseSetup: client1 "Mijoz A" (owner sales1), client2 "Mijoz B" (owner sales2)

    def test_create_blocks_same_name(self):
        self.client.force_login(self.sales1)
        self.client.post(reverse("client_create"), {"name": "Mijoz A"})
        self.assertEqual(Client.objects.filter(name="Mijoz A").count(), 1)

    def test_create_allows_same_name_with_override(self):
        self.client.force_login(self.sales1)
        self.client.post(reverse("client_create"), {"name": "Mijoz A", "allow_duplicate": "on"})
        self.assertEqual(Client.objects.filter(name="Mijoz A").count(), 2)

    def test_sales_duplicate_scoped_to_own_clients(self):
        # "Mijoz B" belongs to sales2; sales1 doesn't see it, so no clash
        self.client.force_login(self.sales1)
        self.client.post(reverse("client_create"), {"name": "Mijoz B"})
        self.assertEqual(Client.objects.filter(name="Mijoz B").count(), 2)

    def test_manager_duplicate_checks_all_clients(self):
        self.client.force_login(self.manager)
        self.client.post(reverse("client_create"), {"name": "Mijoz A"})
        self.assertEqual(Client.objects.filter(name="Mijoz A").count(), 1)  # blocked

    def test_quick_create_reports_duplicate(self):
        self.client.force_login(self.sales1)
        response = self.client.post(reverse("client_quick_create"), {"name": "Mijoz A"})
        self.assertEqual(response.status_code, 409)
        data = response.json()
        self.assertTrue(data["duplicate"])
        self.assertEqual(data["existing"]["id"], self.client1.pk)

    def test_quick_create_override_creates_duplicate(self):
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("client_quick_create"), {"name": "Mijoz A", "allow_duplicate": "1"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Client.objects.filter(name="Mijoz A").count(), 2)


class AuditLogTests(BaseSetup):
    def test_sale_create_is_logged(self):
        self.client.force_login(self.sales1)
        data = sale_post(self.client1.pk, [one_item(self.product, weight="5")])
        self.client.post(reverse("sale_create"), data)
        log = AuditLog.objects.filter(action="create", target_type="Sotuv").latest("created_at")
        self.assertEqual(log.user, self.sales1)

    def test_payment_is_logged(self):
        sale = make_sale(self.client1, self.sales1, self.product, is_debt=True)
        self.client.force_login(self.sales1)
        self.client.post(
            reverse("sale_pay", args=[sale.pk]), {"amount": "100000", "method": "cash"}
        )
        self.assertTrue(
            AuditLog.objects.filter(action="payment", target_id=sale.pk).exists()
        )

    def test_void_is_logged(self):
        sale = make_sale(self.client1, self.sales1, self.product)  # paid
        payment = sale.payments.get()
        self.client.force_login(self.manager)
        self.client.post(reverse("payment_delete", args=[payment.pk]))
        self.assertTrue(AuditLog.objects.filter(action="void", target_id=sale.pk).exists())

    def test_audit_list_is_admin_manager_only(self):
        self.client.force_login(self.sales1)
        self.assertEqual(self.client.get(reverse("audit_list")).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("audit_list")).status_code, 200)


class ModalFormTests(BaseSetup):
    def _ajax(self):
        return {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def test_ajax_get_returns_modal_partial(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("client_create"), **self._ajax())
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "_modal.html")
        self.assertNotContains(response, "<aside")  # no full page chrome

    def test_ajax_success_returns_204_with_redirect(self):
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("client_create"), {"name": "Yangi Mijoz"}, **self._ajax()
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["X-Redirect"], reverse("client_list"))

    def test_ajax_invalid_returns_422_partial(self):
        self.client.force_login(self.sales1)
        response = self.client.post(reverse("client_create"), {"name": ""}, **self._ajax())
        self.assertEqual(response.status_code, 422)
        self.assertTemplateUsed(response, "_modal.html")

    def test_non_ajax_get_returns_full_page(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("client_create"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "crm/form.html")
