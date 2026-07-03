from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User

from .models import Client, Product, Sale, StockEntry


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


class DateFilterTests(BaseSetup):
    def test_sale_list_date_filter(self):
        old = make_sale(
            self.client1, self.sales1, self.product,
            date=timezone.localdate() - timedelta(days=90),
        )
        self.client.force_login(self.sales1)
        cutoff = (timezone.localdate() - timedelta(days=30)).isoformat()
        response = self.client.get(reverse("sale_list"), {"dan": cutoff})
        sales = list(response.context["page"].object_list)
        self.assertIn(self.sale1, sales)
        self.assertNotIn(old, sales)


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
