from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

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
    path("clients/<int:pk>/edit/", crm_views.client_edit, name="client_edit"),
    path("clients/<int:pk>/delete/", crm_views.client_delete, name="client_delete"),
    # products
    path("products/", crm_views.product_list, name="product_list"),
    path("products/new/", crm_views.product_create, name="product_create"),
    path("products/<int:pk>/", crm_views.product_detail, name="product_detail"),
    path("products/<int:pk>/edit/", crm_views.product_edit, name="product_edit"),
    path("products/<int:pk>/kirim/", crm_views.stock_entry_create, name="stock_entry_create"),
    # sales
    path("sales/", crm_views.sale_list, name="sale_list"),
    path("sales/new/", crm_views.sale_create, name="sale_create"),
    path("sales/<int:pk>/edit/", crm_views.sale_edit, name="sale_edit"),
    path("sales/<int:pk>/delete/", crm_views.sale_delete, name="sale_delete"),
]
