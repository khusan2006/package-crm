from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import Client, Payment, Product, Sale, StockEntry


def make_sale(client, rep, product, weight="10", price="24000", **kwargs):
    kwargs.setdefault("cost_price", product.cost_price)
    return Sale.objects.create(
        client=client,
        product=product,
        sales_rep=rep,
        weight=Decimal(weight),
        price=Decimal(price),
        **kwargs,
    )


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
        data = {
            "date": timezone.localdate().isoformat(),
            "client": self.client1.pk,
            "product": self.product.pk,
            "dimension": "kg",
            "weight": "5",
            "price": "24000",
            "cost_price": "",
            "is_debt": "",
            "debt_deadline": "",
        }
        data.update(overrides)
        return data

    def test_debt_requires_deadline(self):
        self.client.force_login(self.sales1)
        response = self.client.post(reverse("sale_create"), self._sale_data(is_debt="on"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("debt_deadline", response.context["form"].errors)

    def test_debt_with_deadline_saves(self):
        self.client.force_login(self.sales1)
        deadline = (timezone.localdate() + timedelta(days=30)).isoformat()
        response = self.client.post(
            reverse("sale_create"), self._sale_data(is_debt="on", debt_deadline=deadline)
        )
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest("created_at")
        self.assertTrue(sale.is_debt)
        self.assertEqual(sale.debt_deadline.isoformat(), deadline)

    def test_non_debt_clears_deadline(self):
        self.client.force_login(self.sales1)
        deadline = (timezone.localdate() + timedelta(days=30)).isoformat()
        self.client.post(reverse("sale_create"), self._sale_data(debt_deadline=deadline))
        sale = Sale.objects.latest("created_at")
        self.assertFalse(sale.is_debt)
        self.assertIsNone(sale.debt_deadline)

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
        data = {
            "date": timezone.localdate().isoformat(),
            "client": self.client1.pk,
            "product": self.product.pk,
            "dimension": "g",
            "weight": "500",
            "price": "24",
            "cost_price": "",
        }
        response = self.client.post(reverse("sale_create"), data)
        self.assertEqual(response.status_code, 302)
        sale = Sale.objects.latest("created_at")
        # per-gram tannarx = 18000 / 1000
        self.assertEqual(sale.cost_price, Decimal("18"))
        self.assertEqual(sale.sales_rep, self.sales1)


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
        response = self.client.get(reverse("sale_list"), {"sana": target.isoformat()})
        sales = list(response.context["page"].object_list)
        self.assertIn(old, sales)
        self.assertNotIn(self.sale1, sales)  # today's sale not on the target day
        self.assertFalse(response.context["is_today"])


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
        data = {
            "date": timezone.localdate().isoformat(),
            "client": self.client1.pk,
            "product": self.product.pk,
            "dimension": "kg",
            "weight": "500",  # far beyond the 80 kg on hand
            "price": "24000",
            "cost_price": "",
        }
        response = self.client.post(reverse("sale_create"), data, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Sale.objects.filter(weight=Decimal("500")).exists())
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
        self.assertTrue(all(s.is_debt for s in sales))

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
        self.overdue = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=today - timedelta(days=1),
        )
        self.soon = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=today + timedelta(days=3),
        )
        self.far = make_sale(
            self.client1, self.sales1, self.product,
            is_debt=True, debt_deadline=today + timedelta(days=30),
        )
        self.paid = make_sale(self.client1, self.sales1, self.product)

    def test_overdue_and_upcoming_are_split(self):
        self.client.force_login(self.sales1)
        ctx = self.client.get(reverse("debt_list")).context
        self.assertIn(self.overdue, ctx["overdue"])
        self.assertNotIn(self.overdue, ctx["upcoming"])
        self.assertIn(self.soon, ctx["upcoming"])
        self.assertNotIn(self.soon, ctx["overdue"])
        # a debt due far in the future is in neither
        self.assertNotIn(self.far, ctx["overdue"])
        self.assertNotIn(self.far, ctx["upcoming"])
        # a paid sale is in neither
        self.assertNotIn(self.paid, ctx["overdue"])
        self.assertNotIn(self.paid, ctx["upcoming"])

    def test_scoped_to_sales_rep(self):
        other = make_sale(
            self.client2, self.sales2, self.product,
            is_debt=True, debt_deadline=timezone.localdate() - timedelta(days=2),
        )
        self.client.force_login(self.sales1)
        ctx = self.client.get(reverse("debt_list")).context
        self.assertNotIn(other, ctx["overdue"])
        self.assertNotIn(other, ctx["upcoming"])


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

    def test_non_debt_sale_records_a_payment(self):
        self.client.force_login(self.sales1)
        data = {
            "date": timezone.localdate().isoformat(),
            "client": self.client1.pk,
            "product": self.product.pk,
            "dimension": "kg",
            "weight": "10",
            "price": "24000",
            "cost_price": "",
            "payment_method": "card",
        }
        self.client.post(reverse("sale_create"), data)
        sale = Sale.objects.latest("created_at")
        payment = sale.payments.get()
        self.assertEqual(payment.kind, "sale")
        self.assertEqual(payment.method, "card")
        self.assertEqual(payment.amount, Decimal("240000.00"))

    def test_debt_sale_with_down_payment(self):
        # 10 kg × 24000 = 240000 total; pay 100000 now, rest is debt
        self.client.force_login(self.sales1)
        data = {
            "date": timezone.localdate().isoformat(),
            "client": self.client1.pk,
            "product": self.product.pk,
            "dimension": "kg", "weight": "10", "price": "24000", "cost_price": "",
            "is_debt": "on",
            "down_payment": "100000",
            "payment_method": "cash",
            "debt_deadline": (timezone.localdate() + timedelta(days=10)).isoformat(),
        }
        self.client.post(reverse("sale_create"), data)
        sale = Sale.objects.latest("created_at")
        self.assertTrue(sale.is_debt)
        self.assertEqual(sale.paid_amount, Decimal("100000.00"))
        self.assertEqual(sale.debt_remaining, Decimal("140000.00"))
        self.assertEqual(sale.payments.get().kind, "sale")

    def test_down_payment_covering_total_is_not_debt(self):
        self.client.force_login(self.sales1)
        data = {
            "date": timezone.localdate().isoformat(),
            "client": self.client1.pk,
            "product": self.product.pk,
            "dimension": "kg", "weight": "10", "price": "24000", "cost_price": "",
            "is_debt": "on",
            "down_payment": "240000",
            "payment_method": "card",
            "debt_deadline": (timezone.localdate() + timedelta(days=10)).isoformat(),
        }
        self.client.post(reverse("sale_create"), data)
        sale = Sale.objects.latest("created_at")
        self.assertFalse(sale.is_debt)
        self.assertIsNone(sale.debt_deadline)
        self.assertEqual(sale.paid_amount, Decimal("240000.00"))

    def test_debt_sale_records_no_payment(self):
        self.client.force_login(self.sales1)
        data = {
            "date": timezone.localdate().isoformat(),
            "client": self.client1.pk,
            "product": self.product.pk,
            "dimension": "kg", "weight": "10", "price": "24000", "cost_price": "",
            "is_debt": "on",
            "debt_deadline": (timezone.localdate() + timedelta(days=10)).isoformat(),
        }
        self.client.post(reverse("sale_create"), data)
        sale = Sale.objects.latest("created_at")
        self.assertEqual(sale.payments.count(), 0)

    def test_partial_debt_payment_keeps_debt_open(self):
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_pay", args=[sale.pk]), {"amount": "100000", "method": "cash"})
        sale.refresh_from_db()
        self.assertTrue(sale.is_debt)  # still owed
        self.assertEqual(sale.debt_remaining, Decimal("140000"))

    def test_full_debt_payment_closes_debt(self):
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        self.client.post(reverse("sale_pay", args=[sale.pk]), {"amount": "240000", "method": "card"})
        sale.refresh_from_db()
        self.assertFalse(sale.is_debt)
        self.assertIsNone(sale.debt_deadline)
        self.assertEqual(sale.payments.get().kind, "debt")

    def test_payment_cannot_exceed_remaining(self):
        sale = self._debt_sale()
        self.client.force_login(self.sales1)
        response = self.client.post(
            reverse("sale_pay", args=[sale.pk]), {"amount": "999999", "method": "cash"}
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with error
        sale.refresh_from_db()
        self.assertTrue(sale.is_debt)
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
