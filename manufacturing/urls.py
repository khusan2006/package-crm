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
]
