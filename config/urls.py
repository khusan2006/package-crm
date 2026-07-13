from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts import views as accounts_views
from crm import views as crm_views

urlpatterns = [
    path("admin/", admin.site.urls),
    # auth
    path("login/", accounts_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # users (admin only)
    path("users/", accounts_views.user_list, name="user_list"),
    path("users/new/", accounts_views.user_create, name="user_create"),
    path("users/<int:pk>/edit/", accounts_views.user_edit, name="user_edit"),
    # dashboard
    path("", crm_views.dashboard, name="dashboard"),
    # clients
    path("clients/", crm_views.client_list, name="client_list"),
    path("clients/new/", crm_views.client_create, name="client_create"),
    path("clients/quick/", crm_views.client_quick_create, name="client_quick_create"),
    path("clients/<int:pk>/edit/", crm_views.client_edit, name="client_edit"),
    path("clients/<int:pk>/delete/", crm_views.client_delete, name="client_delete"),
    path("clients/<int:pk>/transfer/", crm_views.client_transfer, name="client_transfer"),
    # products
    path("products/", crm_views.product_list, name="product_list"),
    path("products/new/", crm_views.product_create, name="product_create"),
    path("products/<int:pk>/", crm_views.product_detail, name="product_detail"),
    path("products/<int:pk>/edit/", crm_views.product_edit, name="product_edit"),
    path("products/<int:pk>/delete/", crm_views.product_delete, name="product_delete"),
    path("products/<int:pk>/kirim/", crm_views.stock_entry_create, name="stock_entry_create"),
    path("products/<int:pk>/tuzatish/", crm_views.stock_adjust, name="stock_adjust"),
    # sales
    path("sales/", crm_views.sale_list, name="sale_list"),
    path("sales/export/", crm_views.sale_export, name="sale_export"),
    path("debts/", crm_views.debt_list, name="debt_list"),
    path("debts/<int:pk>/", crm_views.debt_client, name="debt_client"),
    path("debts/<int:pk>/pay/", crm_views.client_debt_pay, name="client_debt_pay"),
    path("payments/<int:pk>/edit/", crm_views.payment_edit, name="payment_edit"),
    path("payments/<int:pk>/delete/", crm_views.payment_delete, name="payment_delete"),
    path("audit/", crm_views.audit_list, name="audit_list"),
    path("kassa/", crm_views.kassa_view, name="kassa"),
    path("kassa/chiqim/", crm_views.expense_create, name="expense_create"),
    path("kassa/chiqim/export/", crm_views.expense_export, name="expense_export"),
    path("kassa/chiqim/<int:pk>/edit/", crm_views.expense_edit, name="expense_edit"),
    path("kassa/chiqim/<int:pk>/delete/", crm_views.expense_delete, name="expense_delete"),
    path("kassa/topshirish/", crm_views.remittance_create, name="remittance_create"),
    path("kassa/topshirish/<int:pk>/edit/", crm_views.remittance_edit, name="remittance_edit"),
    path("kassa/topshirish/<int:pk>/delete/", crm_views.remittance_delete, name="remittance_delete"),
    # hr / oylik
    path("hr/xodimlar/", crm_views.employee_list, name="employee_list"),
    path("hr/xodimlar/yangi/", crm_views.employee_create, name="employee_create"),
    path("hr/xodimlar/<int:pk>/edit/", crm_views.employee_edit, name="employee_edit"),
    path("hr/davomad/", crm_views.hr_attendance, name="hr_attendance"),
    path("hr/davomad/kiritish/", crm_views.attendance_cell, name="attendance_cell"),
    path("hr/davomad/<int:pk>/delete/", crm_views.attendance_delete, name="attendance_delete"),
    path("hr/oylik/", crm_views.hr_payroll, name="hr_payroll"),
    path("hr/oylik/tolov/", crm_views.salary_pay, name="salary_pay"),
    path("sales/new/", crm_views.sale_create, name="sale_create"),
    path("sales/<int:pk>/", crm_views.sale_detail, name="sale_detail"),
    path("sales/<int:pk>/edit/", crm_views.sale_edit, name="sale_edit"),
    path("sales/<int:pk>/pay/", crm_views.sale_pay, name="sale_pay"),
    path("sales/<int:pk>/return/", crm_views.sale_return, name="sale_return"),
    path("sales/<int:pk>/mark-paid/", crm_views.sale_mark_paid, name="sale_mark_paid"),
    path("sales/<int:pk>/delete/", crm_views.sale_delete, name="sale_delete"),
    # manufacturing
    path("ishlab-chiqarish/", include("manufacturing.urls")),
]
