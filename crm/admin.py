from django.contrib import admin

from .models import Client, Order, OrderItem, Product


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "phone", "owner", "created_at"]
    search_fields = ["name", "company", "phone"]
    list_filter = ["owner"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "sku", "unit", "price", "stock", "is_active"]
    search_fields = ["name", "sku"]
    list_filter = ["unit", "is_active"]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["__str__", "client", "sales_rep", "status", "created_at"]
    list_filter = ["status", "sales_rep"]
    inlines = [OrderItemInline]
