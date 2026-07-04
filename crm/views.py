import csv
from datetime import date, timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, ProtectedError, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import User

from .forms import (
    ClientForm,
    DebtPaymentForm,
    ProductForm,
    SaleForm,
    StockAdjustForm,
    StockEntryForm,
)
from .models import COST, PROFIT, REVENUE, Client, Payment, Product, Sale, StockEntry
from .utils import form_reload, form_response, form_success, is_ajax, render_confirm


def _visible_clients(user):
    qs = Client.objects.select_related("owner")
    return qs if user.can_see_all_records else qs.filter(owner=user)


def _sale_totals(qs):
    return qs.aggregate(revenue=Sum(REVENUE), cost=Sum(COST), profit=Sum(PROFIT))


def _warn_if_negative_stock(request, product):
    """Sales are allowed even without stock, but flag it so it's visible."""
    stock = product.current_stock
    if stock < 0:
        messages.warning(
            request,
            f"Diqqat: “{product.name}” ombori yetarli emas — qoldiq {stock:.3f} kg.",
        )


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# --- Dashboard ---------------------------------------------------------------

def dashboard(request):
    sales = Sale.objects.visible_to(request.user)
    month_start = timezone.localdate().replace(day=1)
    month_sales = sales.filter(date__gte=month_start)

    month = _sale_totals(month_sales)
    all_time = _sale_totals(sales)

    top_clients = (
        _visible_clients(request.user)
        .filter(sales__isnull=False)
        .annotate(
            total=Sum(F("sales__weight") * F("sales__price")),
            profit=Sum(F("sales__weight") * (F("sales__price") - F("sales__cost_price"))),
        )
        .order_by("-total")[:5]
    )

    recent_sales = (
        sales.select_related("client", "product", "sales_rep").with_totals()[:8]
    )

    debt_total = sales.filter(is_debt=True).aggregate(v=Sum(REVENUE))["v"]
    overdue_count = sales.filter(
        is_debt=True, debt_deadline__lt=timezone.localdate()
    ).count()

    low_stock_count = (
        Product.objects.filter(is_active=True)
        .with_stock()
        .filter(stock__lte=F("low_stock_threshold"))
        .count()
    )

    context = {
        "month": month,
        "all_time": all_time,
        "month_count": month_sales.count(),
        "top_clients": top_clients,
        "recent_sales": recent_sales,
        "client_count": _visible_clients(request.user).count(),
        "debt_total": debt_total,
        "overdue_count": overdue_count,
        "low_stock_count": low_stock_count,
    }
    return render(request, "crm/dashboard.html", context)


# --- Clients ------------------------------------------------------------------

def client_list(request):
    clients = (
        _visible_clients(request.user)
        .annotate(sale_count=Count("sales"))
        .order_by("name")
    )
    q = request.GET.get("q", "").strip()
    if q:
        clients = clients.filter(
            Q(name__icontains=q) | Q(company__icontains=q) | Q(phone__icontains=q)
        )
    page = Paginator(clients, 25).get_page(request.GET.get("page"))
    return render(request, "crm/client_list.html", {"page": page, "q": q})


def client_create(request):
    form = ClientForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            client = form.save(commit=False)
            client.owner = request.user
            client.save()
            messages.success(request, f"“{client.name}” mijozi qo'shildi.")
            return form_success(request, reverse("client_list"))
        return form_response(request, form, "Yangi mijoz", invalid=True)
    return form_response(request, form, "Yangi mijoz")


def client_quick_create(request):
    """Create a client inline (from the sale form) and return it as JSON."""
    if request.method != "POST":
        return JsonResponse({"error": "POST kerak"}, status=405)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Ism kiritilishi shart"}, status=400)
    client = Client.objects.create(
        name=name, phone=request.POST.get("phone", "").strip(), owner=request.user
    )
    return JsonResponse({"id": client.pk, "text": client.name})


def client_edit(request, pk):
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"“{client.name}” mijozi yangilandi.")
        return redirect("client_list")
    return render(
        request, "crm/form.html", {"form": form, "title": f"Tahrirlash: {client.name}"}
    )


def client_delete(request, pk):
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    if request.method == "POST":
        try:
            client.delete()
            messages.success(request, f"“{client.name}” mijozi o'chirildi.")
        except ProtectedError:
            messages.error(
                request,
                f"“{client.name}” mijozini o'chirib bo'lmaydi — sotuvlari mavjud.",
            )
        return redirect("client_list")
    return render(request, "crm/confirm_delete.html", {"object": client, "back": "client_list"})


# --- Products -----------------------------------------------------------------

def product_list(request):
    products = Product.objects.with_stock().order_by("name")
    q = request.GET.get("q", "").strip()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    page = Paginator(products, 25).get_page(request.GET.get("page"))
    return render(request, "crm/product_list.html", {"page": page, "q": q})


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    entries = product.stock_entries.select_related("created_by")[:50]
    recent_sales = (
        product.sales.select_related("client", "sales_rep").with_totals()[:10]
    )
    context = {
        "product": product,
        "current_stock": product.current_stock,
        "total_received": product.total_received,
        "total_sold": product.total_sold,
        "entries": entries,
        "recent_sales": recent_sales,
    }
    return render(request, "crm/product_detail.html", context)


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def product_create(request):
    form = ProductForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            product = form.save()
            messages.success(request, f"“{product.name}” mahsuloti qo'shildi.")
            return form_success(request, reverse("product_detail", args=[product.pk]))
        return form_response(request, form, "Yangi mahsulot", invalid=True)
    return form_response(request, form, "Yangi mahsulot")


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"“{product.name}” mahsuloti yangilandi.")
        return redirect("product_detail", pk=product.pk)
    return render(
        request, "crm/form.html", {"form": form, "title": f"Tahrirlash: {product.name}"}
    )


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def stock_entry_create(request, pk):
    product = get_object_or_404(Product, pk=pk)
    title = f"Kirim qo'shish: {product.name}"
    form = StockEntryForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            entry = form.save(commit=False)
            entry.product = product
            entry.created_by = request.user
            entry.save()
            messages.success(
                request, f"“{product.name}” omboriga {entry.quantity_kg} kg kirim qilindi."
            )
            return form_success(request, reverse("product_detail", args=[product.pk]))
        return form_response(request, form, title, invalid=True)
    return form_response(request, form, title)


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def stock_adjust(request, pk):
    product = get_object_or_404(Product, pk=pk)
    title = f"Miqdorni tuzatish: {product.name}"
    if request.method == "POST":
        form = StockAdjustForm(request.POST)
        if form.is_valid():
            current = product.current_stock
            target = form.cleaned_data["quantity"]
            delta = target - current
            if delta != 0:
                note = form.cleaned_data["note"] or (
                    f"Miqdor tuzatildi: {current:.3f} → {target:.3f} kg"
                )
                StockEntry.objects.create(
                    product=product, quantity_kg=delta, note=note, created_by=request.user
                )
                messages.success(
                    request, f"“{product.name}” ombori {target:.3f} kg qilib belgilandi."
                )
            else:
                messages.info(request, "Miqdor o'zgarmadi.")
            return form_success(request, reverse("product_detail", args=[product.pk]))
        return form_response(request, form, title, invalid=True)
    form = StockAdjustForm(initial={"quantity": product.current_stock})
    return form_response(request, form, title)


# --- Sales --------------------------------------------------------------------

def _filter_sales(request, sales):
    """Apply the advanced sales filters from GET params. Returns (queryset, filters)."""
    filters = {key: request.GET.get(key, "") for key in ("dan", "gacha", "client", "product", "rep", "status")}

    date_from = _parse_date(filters["dan"])
    date_to = _parse_date(filters["gacha"])
    if date_from:
        sales = sales.filter(date__gte=date_from)
    if date_to:
        sales = sales.filter(date__lte=date_to)
    if filters["client"].isdigit():
        sales = sales.filter(client_id=filters["client"])
    if filters["product"].isdigit():
        sales = sales.filter(product_id=filters["product"])
    if filters["rep"].isdigit() and request.user.can_see_all_records:
        sales = sales.filter(sales_rep_id=filters["rep"])
    if filters["status"] == "paid":
        sales = sales.filter(is_debt=False)
    elif filters["status"] == "debt":
        sales = sales.filter(is_debt=True)
    elif filters["status"] == "overdue":
        sales = sales.filter(is_debt=True, debt_deadline__lt=timezone.localdate())
    return sales, filters


def sale_list(request):
    base = (
        Sale.objects.visible_to(request.user)
        .select_related("client", "product", "sales_rep")
        .with_totals()
    )
    sales, filters = _filter_sales(request, base)
    sales = sales.order_by("-date", "-created_at")

    totals = _sale_totals(sales)
    debt_sales = sales.filter(is_debt=True)
    totals["debt"] = debt_sales.aggregate(v=Sum(REVENUE))["v"]
    totals["debtors"] = debt_sales.values("client").distinct().count()

    # Real ratios for the KPI card meta-lines (no fabricated trends)
    revenue = totals["revenue"] or 0
    total_clients = _visible_clients(request.user).count()
    totals["count"] = sales.count()
    totals["margin"] = (totals["profit"] or 0) / revenue * 100 if revenue else 0
    totals["debt_share"] = (totals["debt"] or 0) / revenue * 100 if revenue else 0
    totals["debtor_pct"] = totals["debtors"] / total_clients * 100 if total_clients else 0

    page = Paginator(sales, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "crm/sale_list.html",
        {
            "page": page,
            "totals": totals,
            "filters": filters,
            "clients": _visible_clients(request.user).order_by("name"),
            "products": Product.objects.order_by("name"),
            "reps": (
                User.objects.filter(is_active=True).order_by("first_name", "username")
                if request.user.can_see_all_records
                else None
            ),
            "export_qs": request.GET.urlencode(),
        },
    )


DEBT_SOON_DAYS = 7


def debt_list(request):
    today = timezone.localdate()
    base = (
        Sale.objects.visible_to(request.user)
        .filter(is_debt=True)
        .select_related("client", "product", "sales_rep")
        .with_totals()
    )
    overdue = list(base.filter(debt_deadline__lt=today).order_by("debt_deadline"))
    upcoming = list(
        base.filter(
            debt_deadline__gte=today,
            debt_deadline__lte=today + timedelta(days=DEBT_SOON_DAYS),
        ).order_by("debt_deadline")
    )
    for sale in overdue + upcoming:
        sale.remaining = sale.debt_remaining

    # Totals across every outstanding debt (remaining = full total − payments so far)
    open_debts = Sale.objects.visible_to(request.user).filter(is_debt=True)
    total_full = open_debts.aggregate(v=Sum(REVENUE))["v"] or 0
    total_paid = (
        Payment.objects.filter(sale__in=open_debts, kind=Payment.Kind.DEBT)
        .aggregate(v=Sum("amount"))["v"]
        or 0
    )
    total_debtors = open_debts.values("client").distinct().count()

    return render(
        request,
        "crm/debt_list.html",
        {
            "overdue": overdue,
            "upcoming": upcoming,
            "overdue_total": sum((s.remaining for s in overdue), 0),
            "upcoming_total": sum((s.remaining for s in upcoming), 0),
            "overdue_debtors": len({s.client_id for s in overdue}),
            "total_debt": total_full - total_paid,
            "total_debtors": total_debtors,
            "soon_days": DEBT_SOON_DAYS,
        },
    )


def payment_list(request):
    payments = Payment.objects.select_related(
        "sale", "sale__client", "sale__product", "created_by"
    )
    if not request.user.can_see_all_records:
        payments = payments.filter(sale__sales_rep=request.user)

    date_from = _parse_date(request.GET.get("dan"))
    date_to = _parse_date(request.GET.get("gacha"))
    if date_from:
        payments = payments.filter(date__gte=date_from)
    if date_to:
        payments = payments.filter(date__lte=date_to)
    payments = payments.order_by("-date", "-created_at")

    totals = payments.aggregate(
        total=Sum("amount"),
        cash=Sum("amount", filter=Q(method=Payment.Method.CASH)),
        card=Sum("amount", filter=Q(method=Payment.Method.CARD)),
        debt=Sum("amount", filter=Q(kind=Payment.Kind.DEBT)),
    )
    page = Paginator(payments, 30).get_page(request.GET.get("page"))
    return render(
        request,
        "crm/payment_list.html",
        {"page": page, "totals": totals, "date_from": date_from, "date_to": date_to},
    )


def sale_export(request):
    base = Sale.objects.visible_to(request.user).select_related("client", "product", "sales_rep")
    sales, _ = _filter_sales(request, base)
    sales = sales.order_by("-date", "-created_at")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="sotuvlar.csv"'
    response.write("\ufeff")  # UTF-8 BOM so Excel reads Uzbek text correctly
    writer = csv.writer(response)
    writer.writerow([
        "Sana", "Mijoz", "Mahsulot", "Sotuvchi", "O'lchov", "Og'irligi",
        "Narxi", "Umumiy narx", "Tannarx", "Foyda", "To'lov", "Qarz muddati",
    ])
    for s in sales:
        writer.writerow([
            s.date.isoformat(),
            s.client.name,
            s.product.name,
            str(s.sales_rep),
            s.get_dimension_display(),
            f"{s.weight:.3f}",
            f"{s.price:.2f}",
            f"{s.total_price:.2f}",
            f"{s.total_cost:.2f}",
            f"{s.profit:.2f}",
            "Qarz" if s.is_debt else "To'langan",
            s.debt_deadline.isoformat() if s.debt_deadline else "",
        ])
    return response


def sale_create(request):
    form = SaleForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            sale = form.save(commit=False)
            sale.sales_rep = request.user
            sale.save()
            if not sale.is_debt:
                # Paid at the point of sale — record the transaction
                Payment.objects.create(
                    sale=sale,
                    amount=sale.total_price,
                    method=form.cleaned_data.get("payment_method") or Payment.Method.CASH,
                    kind=Payment.Kind.SALE,
                    date=sale.date,
                    created_by=request.user,
                )
            messages.success(request, "Sotuv qo'shildi.")
            _warn_if_negative_stock(request, sale.product)
            return form_success(request, reverse("sale_list"))
        return form_response(request, form, "Yangi sotuv", invalid=True, modal_template="crm/_sale_modal.html")
    return form_response(request, form, "Yangi sotuv", modal_template="crm/_sale_modal.html")


def sale_edit(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    form = SaleForm(request.POST or None, instance=sale, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            sale = form.save()
            messages.success(request, "Sotuv yangilandi.")
            _warn_if_negative_stock(request, sale.product)
            return form_reload(request, reverse("sale_list"))
        return form_response(request, form, "Sotuvni tahrirlash", invalid=True, modal_template="crm/_sale_modal.html")
    return form_response(request, form, "Sotuvni tahrirlash", modal_template="crm/_sale_modal.html")


def _render_debt_pay(request, sale, form, invalid=False):
    context = {
        "form": form,
        "sale": sale,
        "remaining": sale.debt_remaining,
        "title": f"To'lov: {sale.client.name}",
    }
    if is_ajax(request):
        return render(request, "crm/_debt_pay_modal.html", context, status=422 if invalid else 200)
    return render(request, "crm/_debt_pay_page.html", context)


def sale_pay(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    if not sale.is_debt:
        return form_reload(request, reverse("debt_list"))
    remaining = sale.debt_remaining
    if request.method == "POST":
        form = DebtPaymentForm(request.POST, max_amount=remaining)
        if form.is_valid():
            Payment.objects.create(
                sale=sale,
                amount=form.cleaned_data["amount"],
                method=form.cleaned_data["method"],
                kind=Payment.Kind.DEBT,
                date=timezone.localdate(),
                created_by=request.user,
            )
            if sale.debt_remaining <= 0:
                sale.is_debt = False
                sale.debt_deadline = None
                sale.save(update_fields=["is_debt", "debt_deadline"])
                messages.success(request, "Qarz to'liq to'landi.")
            else:
                messages.success(
                    request, f"To'lov qabul qilindi. Qoldiq: {sale.debt_remaining:,.0f} so'm."
                )
            return form_reload(request, reverse("debt_list"))
        return _render_debt_pay(request, sale, form, invalid=True)
    form = DebtPaymentForm(initial={"amount": remaining, "method": Payment.Method.CASH})
    return _render_debt_pay(request, sale, form)


def sale_delete(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    if request.method == "POST":
        sale.delete()
        messages.success(request, "Sotuv o'chirildi.")
        return form_reload(request, reverse("sale_list"))
    return render_confirm(
        request,
        "Sotuvni o'chirish",
        "Bu sotuv butunlay o'chiriladi. Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )
