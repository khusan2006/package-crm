from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.decorators import role_required
from accounts.models import User
from crm.models import AuditLog
from crm.utils import form_response, form_success

from . import services
from .forms import MaterialPurchaseForm, RawMaterialForm
from .models import MaterialPurchase, RawMaterial

SKLAD_ROLES = (User.Role.ADMIN, User.Role.MANAGER, User.Role.OMBORCHI)


@role_required(*SKLAD_ROLES)
def material_list(request):
    materials = RawMaterial.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        materials = materials.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    page = Paginator(materials, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/material_list.html", {"page": page, "q": q})


@role_required(*SKLAD_ROLES)
def material_create(request):
    form = RawMaterialForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        material = form.save()
        AuditLog.record(request.user, AuditLog.Action.CREATE, "Xomashyo", material.pk, material.name)
        messages.success(request, f"“{material.name}” xomashyosi qo'shildi.")
        return form_success(request, reverse("manufacturing:material_list"))
    return form_response(request, form, "Yangi xomashyo", invalid=request.method == "POST")


@role_required(*SKLAD_ROLES)
def material_edit(request, pk):
    material = get_object_or_404(RawMaterial, pk=pk)
    form = RawMaterialForm(request.POST or None, instance=material)
    if request.method == "POST" and form.is_valid():
        form.save()
        AuditLog.record(request.user, AuditLog.Action.UPDATE, "Xomashyo", material.pk, material.name)
        messages.success(request, f"“{material.name}” yangilandi.")
        return form_success(request, reverse("manufacturing:material_list"))
    return form_response(request, form, f"Tahrirlash: {material.name}", invalid=request.method == "POST")


@role_required(*SKLAD_ROLES)
def material_detail(request, pk):
    material = get_object_or_404(RawMaterial, pk=pk)
    purchases = material.purchases.select_related("created_by")[:50]
    return render(request, "manufacturing/material_detail.html", {
        "material": material, "purchases": purchases,
        "current_stock": material.current_stock,
    })


@role_required(*SKLAD_ROLES)
def purchase_list(request):
    purchases = MaterialPurchase.objects.select_related("material", "created_by")
    page = Paginator(purchases, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/purchase_list.html", {"page": page})


@role_required(*SKLAD_ROLES)
def purchase_create(request):
    form = MaterialPurchaseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        purchase = services.create_purchase(
            material=cd["material"], quantity_kg=cd["quantity_kg"],
            price_per_kg=cd["price_per_kg"], date=cd["date"], method=cd["method"],
            supplier=cd["supplier"], note=cd["note"], user=request.user,
        )
        AuditLog.record(request.user, AuditLog.Action.CREATE, "Xomashyo xaridi", purchase.pk,
                        f"{purchase.material.name} — {purchase.total:,.0f} so'm")
        messages.success(request, "Xarid qo'shildi.")
        return form_success(request, reverse("manufacturing:purchase_list"))
    return form_response(request, form, "Yangi xarid", invalid=request.method == "POST")
