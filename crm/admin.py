from django.contrib import admin

from .models import Client, Product, Sale


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "phone", "owner", "created_at"]
    search_fields = ["name", "company", "phone"]
    list_filter = ["owner"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "sku", "cost_price", "price", "is_active"]
    search_fields = ["name", "sku"]
    list_filter = ["is_active"]


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["date", "client", "product", "weight", "dimension", "price", "sales_rep"]
    list_filter = ["dimension", "sales_rep", "date"]
    date_hierarchy = "date"
