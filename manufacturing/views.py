from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from accounts.decorators import role_required
from accounts.models import User
from crm.models import AuditLog, Expense, Payment, Product, ProductionRemittance
from crm.utils import form_response, form_success

from . import services
from .forms import (
    MaterialPurchaseForm,
    ProductionRunForm,
    ProductionRunItemFormSet,
    RawMaterialForm,
    SellerStockEntryForm,
    StockTransferForm,
)
from .models import MaterialPurchase, ProductionRun, RawMaterial, SellerStockEntry, StockTransfer
from .queries import annotate_company_net, annotate_seller_ombor, annotate_sklad_stock
from .services import InsufficientStock

SKLAD_ROLES = (User.Role.ADMIN, User.Role.MANAGER, User.Role.OMBORCHI)


def _range(request):
    def parse(v):
        try:
            return date.fromisoformat(v)
        except (TypeError, ValueError):
            return None
    today = date.today()
    d_from = parse(request.GET.get("from")) or today.replace(day=1)
    d_to = parse(request.GET.get("to")) or today
    return d_from, d_to


@role_required(*SKLAD_ROLES)
def sklad_kassa(request):
    d_from, d_to = _range(request)
    rng = {"date__gte": d_from, "date__lte": d_to}

    remitted = ProductionRemittance.objects.filter(**rng).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    direct_paid = (
        Payment.objects.filter(sale__sales_rep__role=User.Role.OMBORCHI, **rng)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    purchases = MaterialPurchase.objects.filter(**rng)
    purchase_total = sum((p.total for p in purchases), Decimal("0"))
    expenses = (
        Expense.objects.filter(created_by__role=User.Role.OMBORCHI, **rng)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )

    inflow = remitted + direct_paid
    outflow = purchase_total + expenses
    ctx = {
        "d_from": d_from, "d_to": d_to,
        "remitted": remitted, "direct_paid": direct_paid,
        "purchase_total": purchase_total, "expense_total": expenses,
        "inflow": inflow, "outflow": outflow, "balance": inflow - outflow,
        "purchases": purchases.select_related("material")[:100],
    }
    return render(request, "manufacturing/sklad_kassa.html", ctx)


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


def _render_production_form(request, form, formset, invalid=False):
    from crm.utils import is_ajax

    ctx = {"form": form, "formset": formset, "title": "Yangi ishlab chiqarish"}
    if is_ajax(request):
        return render(request, "manufacturing/production_form.html", ctx,
                      status=422 if invalid else 200)
    return render(request, "manufacturing/production_form.html", ctx)


@role_required(*SKLAD_ROLES)
def production_list(request):
    runs = ProductionRun.objects.select_related("product", "created_by")
    page = Paginator(runs, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/production_list.html", {"page": page})


@role_required(*SKLAD_ROLES)
def production_create(request):
    form = ProductionRunForm(request.POST or None)
    formset = ProductionRunItemFormSet(request.POST or None, instance=ProductionRun(), prefix="items")
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            items = [
                (f.cleaned_data["material"], f.cleaned_data["quantity_kg"])
                for f in formset.forms
                if f.cleaned_data and not f.cleaned_data.get("DELETE")
                and f.cleaned_data.get("material") and f.cleaned_data.get("quantity_kg")
            ]
            try:
                run = services.create_production_run(
                    product=form.cleaned_data["product"], output_kg=form.cleaned_data["output_kg"],
                    date=form.cleaned_data["date"], note=form.cleaned_data["note"],
                    user=request.user, items=items,
                )
            except InsufficientStock as exc:
                form.add_error(
                    None,
                    f"“{exc.label}”: omborda {exc.available:.3f} kg bor, {exc.requested:.3f} kg kerak.",
                )
                return _render_production_form(request, form, formset, invalid=True)
            AuditLog.record(request.user, AuditLog.Action.CREATE, "Ishlab chiqarish", run.pk,
                            f"{run.product.name} — {run.output_kg} kg")
            messages.success(request, "Ishlab chiqarish qo'shildi.")
            return form_success(request, reverse("manufacturing:production_list"))
        return _render_production_form(request, form, formset, invalid=True)
    return _render_production_form(request, form, formset)


@role_required(*SKLAD_ROLES)
def sklad_ombor(request):
    products = annotate_sklad_stock(Product.objects.filter(is_active=True)).order_by("name")
    q = request.GET.get("q", "").strip()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    page = Paginator(products, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/sklad_ombor.html", {"page": page, "q": q})


@role_required(*SKLAD_ROLES)
def transfer_list(request):
    transfers = StockTransfer.objects.select_related("product", "seller", "created_by")
    page = Paginator(transfers, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/transfer_list.html", {"page": page})


@role_required(*SKLAD_ROLES)
def transfer_create(request):
    form = StockTransferForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        try:
            transfer = services.create_transfer(
                product=cd["product"], seller=cd["seller"], quantity_kg=cd["quantity_kg"],
                date=cd["date"], note=cd["note"], user=request.user,
            )
        except InsufficientStock as exc:
            form.add_error("quantity_kg",
                           f"Omborda {exc.available:.3f} kg bor, {exc.requested:.3f} kg so'raldi.")
            return form_response(request, form, "Sotuvchiga topshirish", invalid=True)
        AuditLog.record(request.user, AuditLog.Action.TRANSFER, "Omborga topshiruv", transfer.pk,
                        f"{transfer.product.name} → {transfer.seller} — {transfer.quantity_kg} kg")
        messages.success(request, "Topshiruv qo'shildi.")
        return form_success(request, reverse("manufacturing:transfer_list"))
    return form_response(request, form, "Sotuvchiga topshirish", invalid=request.method == "POST")


@role_required(User.Role.SALES, User.Role.ADMIN, User.Role.MANAGER)
def my_ombor(request):
    target = request.user
    seller_pk = request.GET.get("seller", "")
    if request.user.can_see_all_records and seller_pk.isdigit():
        target = get_object_or_404(User, pk=seller_pk)
    products = annotate_seller_ombor(
        Product.objects.filter(is_active=True), target
    ).filter(ombor__gt=0).order_by("name")
    transfers = (
        StockTransfer.objects.filter(seller=target)
        .select_related("product", "created_by")[:50]
    )
    return render(request, "manufacturing/seller_ombor.html", {
        "products": products, "transfers": transfers, "target": target,
        "is_self": target == request.user,
    })


@role_required(User.Role.SALES, User.Role.ADMIN, User.Role.MANAGER)
def seller_entry_create(request):
    form = SellerStockEntryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.seller = request.user
        entry.created_by = request.user
        entry.save()
        AuditLog.record(request.user, AuditLog.Action.CREATE, "Sotuvchi ombor", entry.pk,
                        f"{entry.product.name} — {entry.quantity_kg} kg")
        messages.success(request, "Ombor harakati qo'shildi.")
        return form_success(request, reverse("manufacturing:my_ombor"))
    return form_response(request, form, "Omboriga qo'shish", invalid=request.method == "POST")


@role_required(*SKLAD_ROLES)
def needs_production(request):
    """Make-to-order backlog: products whose company-wide net stock (sklad + all
    seller ombors) is negative — i.e. ordered from customers but not yet produced.
    The shortfall (|company_net|) is how much to manufacture."""
    products = (
        annotate_company_net(Product.objects.filter(is_active=True))
        .filter(company_net__lt=0)
        .order_by("company_net")
    )
    q = request.GET.get("q", "").strip()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    page = Paginator(products, 25).get_page(request.GET.get("page"))
    return render(request, "manufacturing/needs_production.html", {"page": page, "q": q})
