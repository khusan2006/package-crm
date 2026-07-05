import csv
from datetime import date, timedelta
from decimal import Decimal

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
    SaleItemFormSet,
    StockAdjustForm,
    StockEntryForm,
)
from .models import (
    COST,
    PROFIT,
    REVENUE,
    Client,
    Payment,
    Product,
    Sale,
    SaleItem,
    StockEntry,
)
from .utils import form_reload, form_response, form_success, is_ajax, render_confirm


def _visible_clients(user):
    qs = Client.objects.select_related("owner")
    return qs if user.can_see_all_records else qs.filter(owner=user)


def _sale_totals(sales):
    """Revenue/cost/profit summed over the line items of the given sales."""
    return SaleItem.objects.filter(sale__in=sales.values("pk")).aggregate(
        revenue=Sum(REVENUE), cost=Sum(COST), profit=Sum(PROFIT)
    )


def _warn_if_negative_stock(request, product):
    """Sales are allowed even without stock, but flag it so it's visible."""
    stock = product.current_stock
    if stock < 0:
        messages.warning(
            request,
            f"Diqqat: “{product.name}” ombori yetarli emas — qoldiq {stock:.3f} kg.",
        )


def _warn_if_negative_stock_items(request, sale):
    """Flag every distinct product on the sale whose stock went negative."""
    seen = set()
    for item in sale.items.select_related("product"):
        if item.product_id not in seen:
            seen.add(item.product_id)
            _warn_if_negative_stock(request, item.product)


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

    def _margin(t):
        rev = t["revenue"] or 0
        return (t["profit"] or 0) / rev * 100 if rev else 0

    top_clients = (
        _visible_clients(request.user)
        .filter(sales__items__isnull=False)
        .annotate(
            total=Sum(F("sales__items__weight") * F("sales__items__price")),
            profit=Sum(
                F("sales__items__weight")
                * (F("sales__items__price") - F("sales__items__cost_price"))
            ),
        )
        .order_by("-total")[:5]
    )

    recent_sales = (
        sales.select_related("client", "sales_rep")
        .prefetch_related("items__product")
        .with_totals()[:8]
    )

    open_sales = sales.outstanding()
    debt_total = _outstanding_balance(open_sales)
    overdue_count = open_sales.filter(debt_deadline__lt=timezone.localdate()).count()

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
        "all_time_count": sales.count(),
        "month_margin": _margin(month),
        "all_time_margin": _margin(all_time),
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
    recent_items = (
        product.sale_items.select_related("sale", "sale__client")
        .order_by("-sale__date", "-sale__created_at")[:10]
    )
    context = {
        "product": product,
        "current_stock": product.current_stock,
        "total_received": product.total_received,
        "total_sold": product.total_sold,
        "entries": entries,
        "recent_items": recent_items,
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
    """Filter sales by a date window (dan..gacha, default today..today; a single
    day is just an equal from/to) plus client/product/rep/status.
    Returns (queryset, filters, date_from, date_to)."""
    today = timezone.localdate()
    date_from = _parse_date(request.GET.get("dan")) or today
    date_to = _parse_date(request.GET.get("gacha")) or date_from
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    sales = sales.filter(date__gte=date_from, date__lte=date_to)

    filters = {key: request.GET.get(key, "") for key in ("client", "product", "rep", "status")}
    filters["dan"] = date_from.isoformat()
    filters["gacha"] = date_to.isoformat()
    if filters["client"].isdigit():
        sales = sales.filter(client_id=filters["client"])
    if filters["product"].isdigit():
        sales = sales.filter(items__product_id=filters["product"]).distinct()
    if filters["rep"].isdigit() and request.user.can_see_all_records:
        sales = sales.filter(sales_rep_id=filters["rep"])
    # Status is derived from the running balance (annotated by with_balance)
    if filters["status"] == "paid":
        sales = sales.filter(remaining__lte=0)
    elif filters["status"] == "debt":
        sales = sales.filter(remaining__gt=0)
    elif filters["status"] == "overdue":
        sales = sales.filter(remaining__gt=0, debt_deadline__lt=today)
    return sales, filters, date_from, date_to


def _outstanding_balance(sales):
    """Total still owed across the given sales: item revenue − payments."""
    pks = sales.values("pk")
    revenue = SaleItem.objects.filter(sale__in=pks).aggregate(v=Sum(REVENUE))["v"] or 0
    paid = Payment.objects.filter(sale__in=pks).aggregate(v=Sum("amount"))["v"] or 0
    return revenue - paid


def sale_list(request):
    base = (
        Sale.objects.visible_to(request.user)
        .select_related("client", "sales_rep")
        .prefetch_related("items__product")
        .with_balance()
    )
    sales, filters, date_from, date_to = _filter_sales(request, base)
    sales = sales.order_by("-date", "-created_at")

    totals = _sale_totals(sales)
    outstanding = sales.filter(remaining__gt=0)
    totals["debt"] = _outstanding_balance(outstanding)
    totals["debtors"] = outstanding.values("client").distinct().count()

    # Real ratios for the KPI card meta-lines (no fabricated trends)
    revenue = totals["revenue"] or 0
    total_clients = _visible_clients(request.user).count()
    totals["count"] = sales.count()
    totals["margin"] = (totals["profit"] or 0) / revenue * 100 if revenue else 0
    totals["debt_share"] = (totals["debt"] or 0) / revenue * 100 if revenue else 0
    totals["debtor_pct"] = totals["debtors"] / total_clients * 100 if total_clients else 0

    today = timezone.localdate()
    page = Paginator(sales, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "crm/sale_list.html",
        {
            "page": page,
            "totals": totals,
            "filters": filters,
            "date_from": date_from,
            "date_to": date_to,
            "range_days": (date_to - date_from).days + 1,
            "is_single_day": date_from == date_to,
            "is_today": date_from == today and date_to == today,
            "prev_from": (date_from - timedelta(days=1)).isoformat(),
            "prev_to": (date_to - timedelta(days=1)).isoformat(),
            "next_from": (date_from + timedelta(days=1)).isoformat(),
            "next_to": (date_to + timedelta(days=1)).isoformat(),
            "today_iso": today.isoformat(),
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


def debt_list(request):
    """One row per debtor client: total owed, open receipts, earliest deadline."""
    today = timezone.localdate()
    open_sales = (
        Sale.objects.visible_to(request.user).outstanding().select_related("client")
    )

    groups = {}
    total_debt = Decimal("0")
    overdue_total = Decimal("0")
    for sale in open_sales:
        remaining = sale.remaining
        total_debt += remaining
        group = groups.get(sale.client_id)
        if group is None:
            group = groups[sale.client_id] = {
                "client": sale.client,
                "remaining": Decimal("0"),
                "count": 0,
                "earliest": sale.debt_deadline,
                "overdue_count": 0,
            }
        group["remaining"] += remaining
        group["count"] += 1
        if sale.debt_deadline and (
            group["earliest"] is None or sale.debt_deadline < group["earliest"]
        ):
            group["earliest"] = sale.debt_deadline
        if sale.debt_deadline and sale.debt_deadline < today:
            group["overdue_count"] += 1
            overdue_total += remaining

    # Most urgent first: overdue (earliest deadlines) at the top
    debtors = sorted(groups.values(), key=lambda g: g["earliest"] or today)
    overdue_debtors = sum(1 for g in debtors if g["overdue_count"])

    return render(
        request,
        "crm/debt_list.html",
        {
            "debtors": debtors,
            "total_debt": total_debt,
            "overdue_total": overdue_total,
            "total_debtors": len(debtors),
            "overdue_debtors": overdue_debtors,
        },
    )


def debt_client(request, pk):
    """A single debtor's open receipts, with per-receipt balance and deadline."""
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    sales = (
        Sale.objects.visible_to(request.user)
        .filter(client=client)
        .outstanding()
        .select_related("client", "sales_rep")
        .prefetch_related("items__product")
        .order_by("debt_deadline")
    )
    total = sum((s.remaining for s in sales), Decimal("0"))
    return render(
        request,
        "crm/debt_client.html",
        {"client": client, "sales": sales, "total": total},
    )


def payment_list(request):
    payments = Payment.objects.select_related(
        "sale", "sale__client", "created_by"
    ).prefetch_related("sale__items__product")
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
    base = (
        Sale.objects.visible_to(request.user)
        .select_related("client", "sales_rep")
        .with_balance()
    )
    sales, _, _, _ = _filter_sales(request, base)
    sales = sales.order_by("-date", "-created_at").prefetch_related("items__product")

    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="sotuvlar.csv"'
    response.write("\ufeff")  # UTF-8 BOM so Excel reads Uzbek text correctly
    writer = csv.writer(response)
    writer.writerow([
        "Sana", "Mijoz", "Mahsulot", "Sotuvchi", "O'lchov", "Og'irligi",
        "Narxi", "Umumiy narx", "Tannarx", "Foyda", "To'lov", "Qarz muddati",
    ])
    # One row per line item, so a multi-product receipt still exports cleanly.
    for s in sales:
        deadline = s.debt_deadline.isoformat() if s.debt_deadline else ""
        status = "Qarz" if s.remaining > 0 else "To'langan"
        for item in s.items.all():
            writer.writerow([
                s.date.isoformat(),
                s.client.name,
                item.product.name,
                str(s.sales_rep),
                item.get_dimension_display(),
                f"{item.weight:.3f}",
                f"{item.price:.2f}",
                f"{item.total_price:.2f}",
                f"{item.total_cost:.2f}",
                f"{item.profit:.2f}",
                status,
                deadline,
            ])
    return response


def sale_detail(request, pk):
    sale = get_object_or_404(
        Sale.objects.visible_to(request.user)
        .select_related("client", "sales_rep")
        .prefetch_related("items__product"),
        pk=pk,
    )
    payments = sale.payments.select_related("created_by").order_by("-date", "-created_at")
    return render(
        request,
        "crm/sale_detail.html",
        {
            "sale": sale,
            "items": sale.items.all(),
            "payments": payments,
            "paid": sale.paid_amount,
            "remaining": sale.debt_remaining,
        },
    )


def _render_sale_form(request, form, formset, title, invalid=False):
    context = {
        "form": form,
        "formset": formset,
        "title": title,
        "products_json": _product_price_map(),
    }
    if is_ajax(request):
        return render(request, "crm/_sale_modal.html", context, status=422 if invalid else 200)
    return render(request, "crm/sale_form.html", context)


def _product_price_map():
    """Per-kg price/cost for each active product, so the form can auto-fill a row."""
    return {
        str(p.pk): {"price": str(p.price), "cost": str(p.cost_price)}
        for p in Product.objects.filter(is_active=True)
    }


def sale_create(request):
    form = SaleForm(request.POST or None, user=request.user)
    formset = SaleItemFormSet(request.POST or None, instance=Sale(), prefix="items")
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            sale = form.save(commit=False)
            sale.sales_rep = request.user
            sale.save()
            formset.instance = sale
            formset.save()
            # Every sale starts as a receivable; payment is recorded separately.
            messages.success(request, "Sotuv qo'shildi (qarz sifatida).")
            _warn_if_negative_stock_items(request, sale)
            return form_success(request, reverse("sale_list"))
        return _render_sale_form(request, form, formset, "Yangi sotuv", invalid=True)
    return _render_sale_form(request, form, formset, "Yangi sotuv")


def sale_edit(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    form = SaleForm(request.POST or None, instance=sale, user=request.user)
    formset = SaleItemFormSet(request.POST or None, instance=sale, prefix="items")
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            sale = form.save()
            formset.save()
            messages.success(request, "Sotuv yangilandi.")
            _warn_if_negative_stock_items(request, sale)
            return form_reload(request, reverse("sale_list"))
        return _render_sale_form(request, form, formset, "Sotuvni tahrirlash", invalid=True)
    return _render_sale_form(request, form, formset, "Sotuvni tahrirlash")


def sale_mark_paid(request, pk):
    """One-click: record a full cash payment so the sale is settled."""
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    if request.method == "POST":
        remaining = sale.debt_remaining
        if remaining > 0:
            Payment.objects.create(
                sale=sale, amount=remaining, method=Payment.Method.CASH,
                kind=Payment.Kind.SALE, date=timezone.localdate(), created_by=request.user,
            )
            messages.success(request, "Sotuv to'langan deb belgilandi.")
        return form_reload(request, reverse("sale_list"))
    return render_confirm(
        request,
        "To'langan deb belgilash",
        f"“{sale.client.name}” sotuvining qoldig'i "
        f"({sale.debt_remaining:,.0f} so'm) naqd to'langan deb belgilanadimi?",
        "Ha, to'landi",
    )


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
    if sale.is_paid:
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
