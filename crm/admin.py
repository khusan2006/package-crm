from django.contrib import admin

from .models import Client, Payment, Product, Sale, StockEntry


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "phone", "owner", "created_at"]
    search_fields = ["name", "company", "phone"]
    list_filter = ["owner"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "sku", "cost_price", "price", "low_stock_threshold", "is_active"]
    search_fields = ["name", "sku"]
    list_filter = ["is_active"]


@admin.register(StockEntry)
class StockEntryAdmin(admin.ModelAdmin):
    list_display = ["product", "date", "quantity_kg", "created_by", "note"]
    list_filter = ["date", "created_by"]
    search_fields = ["product__name", "product__sku"]
    date_hierarchy = "date"


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["date", "sale", "amount", "method", "kind", "created_by"]
    list_filter = ["method", "kind", "date"]
    date_hierarchy = "date"


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["date", "client", "product", "weight", "dimension", "price", "sales_rep"]
    list_filter = ["dimension", "sales_rep", "date"]
    date_hierarchy = "date"
