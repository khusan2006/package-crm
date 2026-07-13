from django.contrib import admin

from .models import (
    Attendance,
    Client,
    Employee,
    Payment,
    Product,
    ProductionRemittance,
    Sale,
    SaleItem,
    StockEntry,
)


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


@admin.register(ProductionRemittance)
class ProductionRemittanceAdmin(admin.ModelAdmin):
    list_display = ["date", "seller", "amount", "method", "created_by"]
    list_filter = ["method", "date", "seller"]
    date_hierarchy = "date"


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ["name", "salary_type", "hourly_rate", "monthly_salary", "is_active"]
    list_filter = ["salary_type", "is_active"]
    search_fields = ["name"]


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ["employee", "date", "hours", "shift"]
    list_filter = ["date", "shift", "employee"]
    date_hierarchy = "date"
    search_fields = ["employee__name"]


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 1


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["date", "client", "debt_deadline", "sales_rep"]
    list_filter = ["sales_rep", "date"]
    date_hierarchy = "date"
    inlines = [SaleItemInline]
