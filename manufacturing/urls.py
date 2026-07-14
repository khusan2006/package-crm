from django.urls import path

from . import views

app_name = "manufacturing"

urlpatterns = [
    path("xomashyo/", views.material_list, name="material_list"),
    path("xomashyo/yangi/", views.material_create, name="material_create"),
    path("xomashyo/<int:pk>/", views.material_detail, name="material_detail"),
    path("xomashyo/<int:pk>/edit/", views.material_edit, name="material_edit"),
    path("xaridlar/", views.purchase_list, name="purchase_list"),
    path("xaridlar/yangi/", views.purchase_create, name="purchase_create"),
    path("ishlab-chiqarish/", views.production_list, name="production_list"),
    path("ishlab-chiqarish/yangi/", views.production_create, name="production_create"),
    path("ombor/", views.sklad_ombor, name="sklad_ombor"),
    path("topshiruvlar/", views.transfer_list, name="transfer_list"),
    path("topshiruvlar/yangi/", views.transfer_create, name="transfer_create"),
    path("mening-omborim/", views.my_ombor, name="my_ombor"),
    path("mening-omborim/qoshish/", views.seller_entry_create, name="seller_entry_create"),
]
