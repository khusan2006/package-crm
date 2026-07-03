from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import User

from .models import Client, Order, OrderItem, Product


def make_order(client, rep, status=Order.Status.PAID, qty=10, price="1000"):
    order = Order.objects.create(client=client, sales_rep=rep, status=status)
    product = Product.objects.create(
        name=f"Box {order.pk}", sku=f"SKU-{order.pk}", price=Decimal(price), stock=100
    )
    OrderItem.objects.create(order=order, product=product, quantity=qty, unit_price=price)
    return order


class BaseSetup(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("t_admin", password="x", role=User.Role.ADMIN)
        cls.manager = User.objects.create_user("t_manager", password="x", role=User.Role.MANAGER)
        cls.sales1 = User.objects.create_user("t_sales1", password="x", role=User.Role.SALES)
        cls.sales2 = User.objects.create_user("t_sales2", password="x", role=User.Role.SALES)
        cls.client1 = Client.objects.create(name="Client A", owner=cls.sales1)
        cls.client2 = Client.objects.create(name="Client B", owner=cls.sales2)
        cls.order1 = make_order(cls.client1, cls.sales1)
        cls.order2 = make_order(cls.client2, cls.sales2)


class OrderTotalTests(BaseSetup):
    def test_total_amount(self):
        self.assertEqual(self.order1.total_amount, Decimal("10000"))

    def test_with_totals_annotation(self):
        order = Order.objects.with_totals().get(pk=self.order1.pk)
        self.assertEqual(order.total, Decimal("10000"))


class RoleScopingTests(BaseSetup):
    def test_sales_sees_only_own_orders(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("order_list"))
        orders = list(response.context["page"].object_list)
        self.assertEqual(orders, [self.order1])

    def test_manager_sees_all_orders(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("order_list"))
        self.assertEqual(len(response.context["page"].object_list), 2)

    def test_sales_cannot_open_others_order(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("order_detail", args=[self.order2.pk]))
        self.assertEqual(response.status_code, 404)

    def test_sales_sees_only_own_clients(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("client_list"))
        clients = list(response.context["page"].object_list)
        self.assertEqual(clients, [self.client1])

    def test_sales_cannot_create_product(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("product_create"))
        self.assertEqual(response.status_code, 403)

    def test_manager_can_create_product(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("product_create"))
        self.assertEqual(response.status_code, 200)

    def test_only_admin_can_manage_users(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("user_list")).status_code, 200)


class AuthTests(BaseSetup):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_login_page_accessible(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_renders_for_each_role(self):
        for user in (self.admin, self.manager, self.sales1):
            self.client.force_login(user)
            response = self.client.get(reverse("dashboard"))
            self.assertEqual(response.status_code, 200)


class OrderCreateTests(BaseSetup):
    def test_sales_creates_order_with_items(self):
        product = Product.objects.create(
            name="Tape", sku="TAPE-1", price=Decimal("5000"), stock=10
        )
        self.client.force_login(self.sales1)
        data = {
            "client": self.client1.pk,
            "notes": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "1",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-product": product.pk,
            "items-0-quantity": "4",
            "items-0-unit_price": "",
        }
        response = self.client.post(reverse("order_create"), data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.latest("created_at")
        self.assertEqual(order.sales_rep, self.sales1)
        # Empty unit price falls back to the product's current price
        self.assertEqual(order.items.first().unit_price, Decimal("5000"))

    def test_sales_cannot_pick_others_client(self):
        self.client.force_login(self.sales1)
        response = self.client.get(reverse("order_create"))
        client_qs = response.context["form"].fields["client"].queryset
        self.assertNotIn(self.client2, client_qs)
