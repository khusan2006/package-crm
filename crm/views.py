import math
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, ProtectedError, Q, Sum
from django.db.models.functions import TruncMonth
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from accounts.decorators import role_required
from accounts.models import User

from .forms import (
    ClientForm,
    ClientTransferForm,
    DebtPaymentForm,
    ExpenseForm,
    ProductForm,
    ReturnForm,
    SaleForm,
    SaleItemFormSet,
    StockAdjustForm,
    StockEntryForm,
)
from .models import (
    COST,
    PAYMENT_NET,
    PROFIT,
    RETURN_AMOUNT,
    REVENUE,
    AuditLog,
    Client,
    Expense,
    Payment,
    Product,
    Return,
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

UZ_MONTHS_SHORT = ["Yan", "Fev", "Mar", "Apr", "May", "Iyn", "Iyl", "Avg", "Sen", "Okt", "Noy", "Dek"]


def _monthly_series(sales, months=6):
    """Revenue / profit for the last `months` months (oldest first) as SVG line
    points scaled to a fixed viewBox, ready for a line chart."""
    today = timezone.localdate()
    buckets = []
    y, m = today.year, today.month
    for _ in range(months):
        buckets.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    buckets.reverse()
    start = date(buckets[0][0], buckets[0][1], 1)
    rows = (
        SaleItem.objects.filter(sale__in=sales, sale__date__gte=start)
        .annotate(mon=TruncMonth("sale__date"))
        .values("mon")
        .annotate(revenue=Sum(REVENUE), cost=Sum(COST), profit=Sum(PROFIT))
    )
    by_month = {(r["mon"].year, r["mon"].month): r for r in rows}
    data = []
    for yy, mm in buckets:
        row = by_month.get((yy, mm)) or {}
        last_day = (date(yy + (mm == 12), (mm % 12) + 1, 1) - timedelta(days=1)).day
        data.append({
            "label": UZ_MONTHS_SHORT[mm - 1],
            "revenue": row.get("revenue") or Decimal("0"),
            "cost": row.get("cost") or Decimal("0"),
            "profit": row.get("profit") or Decimal("0"),
            "dan": date(yy, mm, 1).isoformat(),
            "gacha": date(yy, mm, last_day).isoformat(),
        })

    # --- line-chart geometry (fixed viewBox, scaled to the tallest revenue) ---
    vb_w, vb_h = 720.0, 240.0
    pad_l, pad_r, pad_t, pad_b = 16.0, 16.0, 18.0, 30.0
    inner_w = vb_w - pad_l - pad_r
    inner_h = vb_h - pad_t - pad_b
    baseline = pad_t + inner_h
    n = len(data)
    peak = max((d["revenue"] for d in data), default=Decimal("0")) or Decimal("1")

    def _y(value):
        return round(baseline - float(value / peak) * inner_h, 2)

    for i, d in enumerate(data):
        d["x"] = round(pad_l + (inner_w * i / (n - 1) if n > 1 else inner_w / 2), 2)
        d["y_rev"] = _y(d["revenue"])
        d["y_profit"] = _y(d["profit"])

    rev_line = " ".join(f"{d['x']},{d['y_rev']}" for d in data)
    profit_line = " ".join(f"{d['x']},{d['y_profit']}" for d in data)
    first_x = data[0]["x"] if data else pad_l
    last_x = data[-1]["x"] if data else vb_w - pad_r
    rev_area = f"{rev_line} {last_x},{baseline} {first_x},{baseline}"

    return {
        "rows": data,
        "rev_line": rev_line,
        "profit_line": profit_line,
        "rev_area": rev_area,
        "viewbox": f"0 0 {vb_w:g} {vb_h:g}",
    }


def _short_money(value):
    """Compact so'm label (e.g. 44.4 mln) for tight spaces like a donut centre."""
    v = float(value or 0)
    if v >= 1e9:
        return f"{v / 1e9:.1f} mlrd"
    if v >= 1e6:
        return f"{v / 1e6:.1f} mln"
    if v >= 1e3:
        return f"{v / 1e3:.0f} ming"
    return f"{v:.0f}"


def _donut(items):
    """Build donut-ready arc segments from (key, label, amount, color) tuples: each
    segment carries the stroke-dasharray/offset that draws its slice of the ring.
    `key` is an optional cross-filter handle (e.g. a payment method) — may be None."""
    grand = sum((amount for _, _, amount, _ in items), Decimal("0"))
    radius = 56.0
    circumference = 2 * math.pi * radius
    segments = []
    cursor = 0.0
    for key, label, amount, color in items:
        frac = float(amount / grand) if grand else 0.0
        length = frac * circumference
        segments.append({
            "key": key,
            "label": label,
            "total": amount,
            "color": color,
            "pct": round(frac * 100, 1),
            "dash": round(length, 2),
            "gap": round(circumference - length, 2),
            "offset": round(-cursor, 2),
        })
        cursor += length
    return {
        "segments": segments,
        "grand": grand,
        "grand_short": _short_money(grand),
        "radius": radius,
    }


def _payment_donut(sales):
    """Payment totals split by method, as donut-ready arc segments."""
    rows = Payment.objects.filter(sale__in=sales).values("method").annotate(total=Sum("amount"))
    totals = {r["method"]: r["total"] or Decimal("0") for r in rows}
    palette = [
        ("cash", "Naqd", "var(--accent)"),
        ("card", "Karta", "var(--success)"),
        ("transfer", "Bank o'tkazmasi", "var(--warning)"),
    ]
    return _donut([(key, label, totals.get(key, Decimal("0")), color) for key, label, color in palette])


def _debt_overview(sales, top=5):
    """Outstanding receivables split into aging buckets (by days overdue) plus the
    biggest debtors — the dashboard's debt-at-a-glance. A company that sells heavily
    on credit needs to see which money is at risk and who to collect from first."""
    today = timezone.localdate()
    open_sales = sales.outstanding().select_related("client")

    # Risk gradient: neutral (not yet due) → gold → orange → red (deeply overdue).
    aging_defs = [
        ("Muddati kelmagan", "color-mix(in srgb, var(--accent) 45%, var(--surface))"),
        ("1–7 kun kechikkan", "var(--warning)"),
        ("8–30 kun kechikkan", "color-mix(in srgb, var(--warning) 45%, var(--danger))"),
        ("30+ kun kechikkan", "var(--danger)"),
    ]
    aging = [{"label": lbl, "color": clr, "amount": Decimal("0"), "count": 0}
             for lbl, clr in aging_defs]

    clients = {}
    total = Decimal("0")
    overdue_total = Decimal("0")
    for sale in open_sales:
        rem = sale.remaining or Decimal("0")
        if rem <= 0:
            continue
        total += rem
        deadline = sale.debt_deadline
        if deadline is None or deadline >= today:
            idx = 0
        else:
            overdue_days = (today - deadline).days
            overdue_total += rem
            idx = 1 if overdue_days <= 7 else 2 if overdue_days <= 30 else 3
        aging[idx]["amount"] += rem
        aging[idx]["count"] += 1

        debtor = clients.get(sale.client_id)
        if debtor is None:
            debtor = clients[sale.client_id] = {
                "client_id": sale.client_id, "name": sale.client.name,
                "amount": Decimal("0"), "overdue": False,
            }
        debtor["amount"] += rem
        if deadline is not None and deadline < today:
            debtor["overdue"] = True

    grand = total or Decimal("1")
    for bucket in aging:
        bucket["pct"] = round(float(bucket["amount"] / grand) * 100, 2)

    # Draw the aging split as a donut, then fold each bucket's receipt count back
    # onto its arc segment so the legend can show both amount and count.
    aging_donut = _donut([(None, b["label"], b["amount"], b["color"]) for b in aging])
    for segment, bucket in zip(aging_donut["segments"], aging):
        segment["count"] = bucket["count"]

    top_debtors = sorted(clients.values(), key=lambda d: d["amount"], reverse=True)[:top]
    peak = max((d["amount"] for d in top_debtors), default=Decimal("1")) or Decimal("1")
    for debtor in top_debtors:
        debtor["pct"] = round(float(debtor["amount"] / peak) * 100, 2)

    return {
        "aging": aging,
        "aging_donut": aging_donut,
        "top_debtors": top_debtors,
        "total": total,
        "overdue_total": overdue_total,
        "overdue_pct": round(float(overdue_total / grand) * 100, 1),
    }



def _top_clients(sales, limit=5):
    """Top clients by revenue within the given (already-scoped) sales."""
    rows = list(
        SaleItem.objects.filter(sale__in=sales.values("pk"))
        .values("sale__client_id", "sale__client__name")
        .annotate(total=Sum(REVENUE))
        .order_by("-total")[:limit]
    )
    peak = max((r["total"] or Decimal("0") for r in rows), default=Decimal("1")) or Decimal("1")
    for row in rows:
        row["name"] = row["sale__client__name"]
        row["client_id"] = row["sale__client_id"]
        row["pct"] = round(float((row["total"] or Decimal("0")) / peak * 100), 2)
    return rows


def dashboard(request):
    today = timezone.localdate()
    # The period drives every "flow" figure; it defaults to month-to-date.
    date_from = _parse_date(request.GET.get("dan")) or today.replace(day=1)
    date_to = _parse_date(request.GET.get("gacha")) or today
    if date_to < date_from:
        date_from, date_to = date_to, date_from

    # Cross-filters set by clicking a chart element (or the drawer). Rep is
    # admin/manager-only; rep+client scope the whole dashboard, the date window and
    # payment method only the sales "flows" (debt is a method-independent snapshot).
    filters = {key: request.GET.get(key, "") for key in ("rep", "client", "method")}
    rep_id = filters["rep"] if (filters["rep"].isdigit() and request.user.can_see_all_records) else ""
    client_id = filters["client"] if filters["client"].isdigit() else ""
    method = filters["method"] if filters["method"] in Payment.Method.values else ""

    scoped = Sale.objects.visible_to(request.user)
    if rep_id:
        scoped = scoped.filter(sales_rep_id=rep_id)
    if client_id:
        scoped = scoped.filter(client_id=client_id)
    # `flow` narrows the sales set by payment method for the flow figures, but debt
    # keeps using `scoped` — an unpaid receipt has no payment row of any method.
    flow = scoped.filter(payments__method=method).distinct() if method else scoped
    period = flow.filter(date__gte=date_from, date__lte=date_to)

    def _margin(t):
        rev = t["revenue"] or 0
        return (t["profit"] or 0) / rev * 100 if rev else 0

    period_totals = _sale_totals(period)
    period_count = period.count()
    period_revenue = period_totals["revenue"] or 0
    avg_check = period_revenue / period_count if period_count else 0

    # Debt is a live snapshot: rep/client scoped, but never date-scoped — an old
    # receipt is still owed today regardless of the selected window.
    open_sales = scoped.outstanding()
    debt_total = _outstanding_balance(open_sales)
    overdue_sales = open_sales.filter(debt_deadline__lt=today)
    overdue_count = overdue_sales.count()
    overdue_total = _outstanding_balance(overdue_sales)
    overdue_clients = overdue_sales.values("client").distinct().count()

    # New clients acquired in the period (owner-scoped when a rep is selected).
    new_clients_q = _visible_clients(request.user)
    if rep_id:
        new_clients_q = new_clients_q.filter(owner_id=rep_id)
    new_clients = new_clients_q.filter(
        created_at__date__gte=date_from, created_at__date__lte=date_to
    ).count()

    recent_sales = (
        period.select_related("client", "sales_rep")
        .prefetch_related("items__product")
        .with_totals()
        .order_by("-date", "-created_at")[:8]
    )

    # Shared toolbar / drawer plumbing (rep + client chips, the date-range picker).
    clients = _visible_clients(request.user).order_by("name")
    reps = (
        User.objects.filter(is_active=True).order_by("first_name", "username")
        if request.user.can_see_all_records
        else None
    )
    client_obj = clients.filter(pk=client_id).first() if client_id else None
    rep_obj = reps.filter(pk=rep_id).first() if reps and rep_id else None
    method_labels = dict(Payment.Method.choices)
    filters["dan"] = date_from.isoformat()
    filters["gacha"] = date_to.isoformat()
    filters["q"] = ""
    active_filters = _filter_chips(request, [
        {"param": "rep", "label": "Sotuvchi", "value": str(rep_obj) if rep_obj else ""},
        {"param": "client", "label": "Mijoz", "value": client_obj.name if client_obj else ""},
        {"param": "method", "label": "To'lov usuli", "value": method_labels.get(method, "")},
    ])

    context = {
        "monthly": _monthly_series(flow),
        "donut": _payment_donut(period),
        "debt": _debt_overview(scoped),
        "top_clients": _top_clients(period),
        "recent_sales": recent_sales,
        "period_revenue": period_revenue,
        "period_profit": period_totals["profit"] or 0,
        "period_count": period_count,
        "period_margin": _margin(period_totals),
        "avg_check": avg_check,
        "new_clients": new_clients,
        "debt_total": debt_total,
        "overdue_count": overdue_count,
        "overdue_total": overdue_total,
        "overdue_clients": overdue_clients,
        "filters": filters,
        "reps": reps,
        "clients": clients,
        "active_filters": active_filters,
        "has_filters": bool(active_filters),
        "filter_count": len(active_filters),
        "filter_url": reverse("dashboard"),
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
        # Broad match: name, company, phone, location, notes, or responsible
        # employee — so "sergeli" finds every client in that district.
        clients = clients.filter(
            Q(name__icontains=q)
            | Q(company__icontains=q)
            | Q(phone__icontains=q)
            | Q(address__icontains=q)
            | Q(notes__icontains=q)
            | Q(owner__first_name__icontains=q)
            | Q(owner__last_name__icontains=q)
            | Q(owner__username__icontains=q)
        ).distinct()
    page = Paginator(clients, 25).get_page(request.GET.get("page"))
    return render(request, "crm/client_list.html", {"page": page, "q": q})


def client_create(request):
    form = ClientForm(request.POST or None, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            client = form.save(commit=False)
            # Admins/managers pick the responsible employee on the form; sellers'
            # clients are always owned by themselves.
            if not client.owner_id:
                client.owner = request.user
            client.save()
            messages.success(request, f"“{client.name}” mijozi qo'shildi.")
            return form_success(request, reverse("client_list"))
        return form_response(request, form, "Yangi mijoz", invalid=True)
    return form_response(request, form, "Yangi mijoz")


def client_quick_create(request):
    """Create a client inline (from the sale form) and return it as JSON.

    Guards against accidental duplicates: an existing same-name client is
    reported back (409) so the caller can reuse it, unless allow_duplicate is set.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST kerak"}, status=405)
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Ism kiritilishi shart"}, status=400)
    if not request.POST.get("allow_duplicate"):
        dup = Client.find_duplicate(request.user, name)
        if dup:
            return JsonResponse(
                {
                    "error": f"“{dup.name}” allaqachon bor",
                    "duplicate": True,
                    "existing": {"id": dup.pk, "text": dup.name},
                },
                status=409,
            )
    client = Client.objects.create(
        name=name, phone=request.POST.get("phone", "").strip(), owner=request.user
    )
    return JsonResponse({"id": client.pk, "text": client.name})


def client_edit(request, pk):
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    form = ClientForm(
        request.POST or None, instance=client, user=request.user, check_duplicates=False
    )
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


def _render_client_transfer(request, client, form, invalid=False):
    context = {
        "form": form,
        "client": client,
        "sales_count": Sale.objects.filter(client=client).count(),
        "title": f"Mijozni o'tkazish: {client.name}",
    }
    if is_ajax(request):
        return render(
            request, "crm/_client_transfer_modal.html", context,
            status=422 if invalid else 200,
        )
    return render(request, "crm/form.html", context)


def client_transfer(request, pk):
    """Hand a client — and their whole sales history — to another seller.

    Full handover: the client's owner and every one of their sales' sales_rep
    move to the target, atomically. Sellers may transfer only clients they own
    (a non-owned client 404s via the visible-clients scope); admins/managers
    may transfer anyone's."""
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    if request.method == "POST":
        form = ClientTransferForm(request.POST, client=client)
        if form.is_valid():
            target = form.cleaned_data["new_owner"]
            old_owner = client.owner
            with transaction.atomic():
                moved = Sale.objects.filter(client=client).update(sales_rep=target)
                client.owner = target
                client.save(update_fields=["owner"])
                AuditLog.record(
                    request.user, AuditLog.Action.TRANSFER, "Mijoz", client.pk,
                    f"{client.name}: {old_owner} → {target} ({moved} ta sotuv)",
                )
            messages.success(
                request, f"“{client.name}” {target}ga o'tkazildi ({moved} ta sotuv)."
            )
            return form_reload(request, reverse("client_list"))
        return _render_client_transfer(request, client, form, invalid=True)
    form = ClientTransferForm(client=client)
    return _render_client_transfer(request, client, form)


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

def _client_search_q(term, base):
    """Q matching a client by name/company/phone/location (case-insensitive) for
    the toolbar's search box. `base` is the lookup path to the Client — e.g.
    "client" for Sale, "sale__client" for Payment."""
    return (
        Q(**{f"{base}__name__icontains": term})
        | Q(**{f"{base}__company__icontains": term})
        | Q(**{f"{base}__phone__icontains": term})
        | Q(**{f"{base}__address__icontains": term})
    )


def _filter_sales(request, sales):
    """Filter sales by client/product/rep/status and, only when no such filter
    is active, a date window (dan..gacha, default today..today).

    A content filter searches across ALL dates — the date window is the default
    (unfiltered) view's concern, so the two never apply at once.
    Returns (queryset, filters, date_from, date_to, has_filters)."""
    today = timezone.localdate()
    filters = {key: request.GET.get(key, "") for key in ("client", "product", "rep", "status")}
    filters["q"] = request.GET.get("q", "").strip()
    has_filters = bool(
        filters["q"]
        or filters["client"].isdigit()
        or filters["product"].isdigit()
        or filters["status"] in ("paid", "debt", "overdue")
        or (filters["rep"].isdigit() and request.user.can_see_all_records)
    )

    date_from = _parse_date(request.GET.get("dan")) or today
    date_to = _parse_date(request.GET.get("gacha")) or date_from
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if not has_filters:
        sales = sales.filter(date__gte=date_from, date__lte=date_to)

    filters["dan"] = date_from.isoformat()
    filters["gacha"] = date_to.isoformat()
    if filters["q"]:
        sales = sales.filter(_client_search_q(filters["q"], "client"))
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
    return sales, filters, date_from, date_to, has_filters


def _filter_chips(request, specs):
    """Build removable filter chips from a list of specs:
    {"param", "label", "value"}. Only specs with a truthy `value` produce a chip.
    The remove-URL drops that param, the page, and any empty filter params."""
    params = [s["param"] for s in specs]

    def without(param):
        qs = request.GET.copy()
        qs.pop(param, None)
        qs.pop("page", None)
        for key in params:
            if not qs.get(key):
                qs.pop(key, None)
        query = qs.urlencode()
        return f"{request.path}?{query}" if query else request.path

    return [
        {"label": s["label"], "value": s["value"], "remove_url": without(s["param"])}
        for s in specs
        if s.get("value")
    ]


def _active_filter_chips(request, filters, clients, products, reps):
    """Sotuvlar filter chips (client/product/rep/status)."""
    status_labels = {"paid": "To'langan", "debt": "Qarz", "overdue": "Muddati o'tgan"}
    client = clients.filter(pk=filters["client"]).first() if filters["client"].isdigit() else None
    product = products.filter(pk=filters["product"]).first() if filters["product"].isdigit() else None
    rep = reps.filter(pk=filters["rep"]).first() if reps and filters["rep"].isdigit() else None
    specs = [
        {"param": "client", "label": "Mijoz", "value": client.name if client else ""},
        {"param": "product", "label": "Mahsulot", "value": product.name if product else ""},
        {"param": "rep", "label": "Sotuvchi", "value": str(rep) if rep else ""},
        {"param": "status", "label": "To'lov", "value": status_labels.get(filters["status"], "")},
    ]
    return _filter_chips(request, specs)


def _date_range_context(request):
    """Parse ?dan/?gacha into a today-default window plus the navigation vars
    the shared toolbar's date-range picker needs."""
    today = timezone.localdate()
    date_from = _parse_date(request.GET.get("dan")) or today
    date_to = _parse_date(request.GET.get("gacha")) or date_from
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    return {
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
    }


def _outstanding_balance(sales):
    """Total still owed across the given sales: item revenue − returns − net payments.

    Payments are netted of bank fees (amount − commission) and returned goods are
    subtracted, matching how each sale's remaining balance is computed."""
    pks = sales.values("pk")
    revenue = SaleItem.objects.filter(sale__in=pks).aggregate(v=Sum(REVENUE))["v"] or 0
    returned = Return.objects.filter(sale__in=pks).aggregate(v=Sum(RETURN_AMOUNT))["v"] or 0
    paid = Payment.objects.filter(sale__in=pks).aggregate(v=Sum(PAYMENT_NET))["v"] or 0
    return revenue - returned - paid


def sale_list(request):
    base = (
        Sale.objects.visible_to(request.user)
        .select_related("client", "sales_rep")
        .prefetch_related("items__product")
        .with_balance()
    )
    sales, filters, date_from, date_to, has_filters = _filter_sales(request, base)
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

    clients = _visible_clients(request.user).order_by("name")
    products = Product.objects.order_by("name")
    reps = (
        User.objects.filter(is_active=True).order_by("first_name", "username")
        if request.user.can_see_all_records
        else None
    )
    active_filters = _active_filter_chips(request, filters, clients, products, reps)
    page = Paginator(sales, 25).get_page(request.GET.get("page"))
    export_qs = request.GET.urlencode()
    return render(
        request,
        "crm/sale_list.html",
        {
            "page": page,
            "totals": totals,
            "filters": filters,
            "has_filters": has_filters,
            "active_filters": active_filters,
            "filter_count": len(active_filters),
            **_date_range_context(request),
            "clients": clients,
            "products": products,
            "reps": reps,
            "export_qs": export_qs,
            "filter_url": reverse("sale_list"),
            "sale_export_url": reverse("sale_export") + (f"?{export_qs}" if export_qs else ""),
        },
    )


def debt_list(request):
    """One row per debtor client: total owed, open receipts, earliest deadline."""
    today = timezone.localdate()
    open_sales = (
        Sale.objects.visible_to(request.user).outstanding().select_related("client")
    )

    filters = {key: request.GET.get(key, "") for key in ("client", "rep", "overdue")}
    filters["q"] = request.GET.get("q", "").strip()
    if filters["q"]:
        open_sales = open_sales.filter(_client_search_q(filters["q"], "client"))
    if filters["client"].isdigit():
        open_sales = open_sales.filter(client_id=filters["client"])
    if filters["rep"].isdigit() and request.user.can_see_all_records:
        open_sales = open_sales.filter(sales_rep_id=filters["rep"])
    if filters["overdue"] == "1":
        open_sales = open_sales.filter(debt_deadline__lt=today)

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

    clients = _visible_clients(request.user).order_by("name")
    reps = (
        User.objects.filter(is_active=True).order_by("first_name", "username")
        if request.user.can_see_all_records
        else None
    )
    client_obj = clients.filter(pk=filters["client"]).first() if filters["client"].isdigit() else None
    rep_obj = reps.filter(pk=filters["rep"]).first() if reps and filters["rep"].isdigit() else None
    active_filters = _filter_chips(request, [
        {"param": "client", "label": "Mijoz", "value": client_obj.name if client_obj else ""},
        {"param": "rep", "label": "Sotuvchi", "value": str(rep_obj) if rep_obj else ""},
        {"param": "overdue", "label": "Holat", "value": "Muddati o'tgan" if filters["overdue"] == "1" else ""},
    ])

    return render(
        request,
        "crm/debt_list.html",
        {
            "debtors": debtors,
            "total_debt": total_debt,
            "overdue_total": overdue_total,
            "total_debtors": len(debtors),
            "overdue_debtors": overdue_debtors,
            "filters": filters,
            "clients": clients,
            "reps": reps,
            "active_filters": active_filters,
            "filter_count": len(active_filters),
            "has_filters": bool(active_filters),
            "filter_url": reverse("debt_list"),
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


def _client_outstanding_fifo(request, client):
    """A client's open receipts ordered oldest debt first (FIFO)."""
    return list(
        Sale.objects.visible_to(request.user)
        .filter(client=client)
        .outstanding()
        .order_by("date", "created_at")
    )


def _distribute_debt_payment(
    sales, amount, method, percent, note, user, currency=None, exchange_rate=Decimal("0")
):
    """Spread a lump payment across FIFO-ordered debts, oldest first.

    `amount` is the gross the client handed over; on a bank transfer the bank
    withholds `percent`, so only the net (amount − commission) reduces the debt.
    Each receipt is credited its net share up to its outstanding balance; the
    last one reached may receive a partial payment. Returns the receipts touched.
    """
    is_transfer = method == Payment.Method.TRANSFER
    percent = percent if is_transfer else Decimal("0")
    currency = currency or Payment.Currency.UZS
    commission_total = (amount * percent / Decimal("100")).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )
    net_left = amount - commission_total
    touched = 0
    with transaction.atomic():
        for sale in sales:
            if net_left <= 0:
                break
            due = sale.remaining or Decimal("0")
            chunk_net = min(net_left, due)
            if chunk_net <= 0:
                continue
            # Gross this slice back up so the recorded fee stays at `percent`;
            # commission is the exact difference, so the net credited is precise.
            if is_transfer and percent < Decimal("100"):
                chunk_gross = (
                    chunk_net / (Decimal("1") - percent / Decimal("100"))
                ).quantize(Decimal("0.01"), ROUND_HALF_UP)
            else:
                chunk_gross = chunk_net
            # Each so'm chunk's dollar figure (for the dollar till) is its share of
            # the gross at the payment's rate; a so'm payment's original == the so'm.
            if currency == Payment.Currency.USD and exchange_rate:
                chunk_original = (chunk_gross / exchange_rate).quantize(
                    Decimal("0.01"), ROUND_HALF_UP
                )
            else:
                chunk_original = chunk_gross
            Payment.objects.create(
                sale=sale,
                amount=chunk_gross,
                amount_original=chunk_original,
                currency=currency,
                exchange_rate=exchange_rate,
                method=method,
                commission=chunk_gross - chunk_net,
                commission_percent=percent,
                note=note,
                kind=Payment.Kind.DEBT,
                date=timezone.localdate(),
                created_by=user,
            )
            net_left -= chunk_net
            touched += 1
    return touched


def _clean_amount(value):
    """Trim meaningless trailing zeros so a pre-filled amount reads '579300',
    not '579300,00000'. Quantity (3dp) × price (2dp) leaves up to 5 decimal
    places; real so'm never needs more than 2, and whole amounts need none."""
    value = value.quantize(Decimal("0.01"), ROUND_HALF_UP)
    if value == value.to_integral_value():
        return value.to_integral_value()
    return value.normalize()


def _usd_note(cleaned):
    """A ' · $100.00 × 12 700' suffix for audit/success lines on a dollar payment."""
    if cleaned.get("currency") == Payment.Currency.USD and cleaned.get("exchange_rate"):
        usd = cleaned["amount"] / cleaned["exchange_rate"]
        return f" · ${usd:,.2f} × {cleaned['exchange_rate']:,.0f}"
    return ""


def _render_client_pay(request, client, total, form, invalid=False):
    context = {
        "form": form,
        "client": client,
        "remaining": total,
        "title": f"Umumiy to'lov: {client.name}",
    }
    if is_ajax(request):
        return render(
            request, "crm/_client_pay_modal.html", context, status=422 if invalid else 200
        )
    return render(request, "crm/_client_pay_page.html", context)


def client_debt_pay(request, pk):
    """Take one amount and pay down the client's debts oldest-first (FIFO)."""
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    sales = _client_outstanding_fifo(request, client)
    total = sum((s.remaining for s in sales), Decimal("0")).quantize(
        Decimal("0.01"), ROUND_HALF_UP
    )
    if total <= 0:
        return form_reload(request, reverse("debt_client", args=[client.pk]))
    if request.method == "POST":
        form = DebtPaymentForm(request.POST, max_amount=total)
        if form.is_valid():
            touched = _distribute_debt_payment(
                sales,
                form.cleaned_data["amount"],
                form.cleaned_data["method"],
                form.cleaned_data["commission_percent"],
                form.cleaned_data["note"],
                request.user,
                currency=form.cleaned_data["currency"],
                exchange_rate=form.cleaned_data["exchange_rate"],
            )
            AuditLog.record(
                request.user, AuditLog.Action.PAYMENT, "To'lov", client.pk,
                f"{client.name} — {form.cleaned_data['amount']:,.0f} so'm "
                f"({touched} ta chekka, {form.cleaned_data['method']}){_usd_note(form.cleaned_data)}",
            )
            messages.success(
                request,
                f"{form.cleaned_data['amount']:,.0f} so'm {touched} ta chekka taqsimlandi.",
            )
            return form_reload(request, reverse("debt_client", args=[client.pk]))
        return _render_client_pay(request, client, total, form, invalid=True)
    form = DebtPaymentForm(
        initial={"amount": _clean_amount(total), "method": Payment.Method.CASH},
        max_amount=total,
    )
    return _render_client_pay(request, client, total, form)


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def payment_delete(request, pk):
    """Void a mistaken payment by removing it. The debt it covered is restored
    automatically (remaining is derived). Admin/manager only — sellers must not
    be able to erase money records."""
    payment = get_object_or_404(
        Payment.objects.select_related("sale", "sale__client"), pk=pk
    )
    if request.method == "POST":
        sale_pk = payment.sale_id
        summary = f"{payment.sale.client.name} — {payment.amount:,.0f} so'm ({payment.get_method_display()})"
        payment.delete()
        AuditLog.record(request.user, AuditLog.Action.VOID, "To'lov", sale_pk, summary)
        messages.success(request, "To'lov o'chirildi — qarz qayta tiklandi.")
        return form_reload(request, reverse("sale_detail", args=[sale_pk]))
    return render_confirm(
        request,
        "To'lovni bekor qilish",
        f"“{payment.sale.client.name}” — {payment.amount:,.0f} so'm to'lov "
        f"({payment.get_method_display()}) o'chiriladi va qarz qayta tiklanadi. "
        f"Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def audit_list(request):
    """The money-action audit trail (admin/manager only)."""
    logs = AuditLog.objects.select_related("user")
    page = Paginator(logs, 50).get_page(request.GET.get("page"))
    return render(request, "crm/audit_list.html", {"page": page})


# --- Kassa (cash register) ----------------------------------------------------

def _currency_till(payments, expenses, date_from, date_to, *, som):
    """Income (by method), expense and the running balance for ONE currency drawer.

    The so'm drawer counts net so'm (amount − bank fee); the dollar drawer counts the
    physical dollars handed over (`amount_original`). The balance is cumulative:
    opening = everything strictly before date_from, closing = opening + in − out."""
    field = PAYMENT_NET if som else F("amount_original")
    exp_field = "amount" if som else "amount_original"

    def income(**flt):
        return payments.filter(**flt).aggregate(s=Sum(field))["s"] or Decimal("0")

    def outflow(**flt):
        return expenses.filter(**flt).aggregate(s=Sum(exp_field))["s"] or Decimal("0")

    window = {"date__gte": date_from, "date__lte": date_to}
    cash = income(method=Payment.Method.CASH, **window)
    card = income(method=Payment.Method.CARD, **window)
    bank = income(method=Payment.Method.TRANSFER, **window)
    total_in = cash + card + bank
    total_out = outflow(**window)
    opening = income(date__lt=date_from) - outflow(date__lt=date_from)
    return {
        "cash": cash, "card": card, "bank": bank, "income": total_in,
        "expense": total_out, "opening": opening,
        "closing": opening + total_in - total_out,
    }


def _kassa_summary(date_from, date_to, rep=None):
    """Two side-by-side till drawers — so'm and dollar — each with its income by
    method, expense and running balance. Scoped to one employee when `rep` is given."""
    payments = Payment.objects.all()
    expenses = Expense.objects.all()
    if rep is not None:
        payments = payments.filter(created_by=rep)
        expenses = expenses.filter(created_by=rep)
    uzs, usd = Payment.Currency.UZS, Payment.Currency.USD
    return {
        "som": _currency_till(
            payments.filter(currency=uzs), expenses.filter(currency=uzs),
            date_from, date_to, som=True,
        ),
        "usd": _currency_till(
            payments.filter(currency=usd), expenses.filter(currency=usd),
            date_from, date_to, som=False,
        ),
    }


def _per_employee_kassa(date_from, date_to):
    """Per-employee kassa flow + sales performance for the window: money they took in
    (so'm net / dollars), money they paid out, the profit their sales earned, and the
    net of that profit less all their expenses (in so'm)."""
    window = {"date__gte": date_from, "date__lte": date_to}
    users = {u.pk: u for u in User.objects.all()}
    usd = Payment.Currency.USD

    def blank(uid):
        return {
            "employee": str(users.get(uid)) if users.get(uid) else "—",
            "in_som": Decimal("0"), "in_usd": Decimal("0"),
            "out_som": Decimal("0"), "out_usd": Decimal("0"),
            "expense_total": Decimal("0"), "profit": Decimal("0"),
        }

    rows = {}

    def row(uid):
        return rows.setdefault(uid, blank(uid))

    for r in (
        Payment.objects.filter(**window)
        .values("created_by", "currency")
        .annotate(som=Sum(PAYMENT_NET), usd_amt=Sum("amount_original"))
    ):
        rr = row(r["created_by"])
        if r["currency"] == usd:
            rr["in_usd"] += r["usd_amt"] or Decimal("0")
        else:
            rr["in_som"] += r["som"] or Decimal("0")

    for r in (
        Expense.objects.filter(**window)
        .values("created_by", "currency")
        .annotate(som=Sum("amount"), usd_amt=Sum("amount_original"))
    ):
        rr = row(r["created_by"])
        rr["expense_total"] += r["som"] or Decimal("0")  # so'm value of every expense
        if r["currency"] == usd:
            rr["out_usd"] += r["usd_amt"] or Decimal("0")
        else:
            rr["out_som"] += r["som"] or Decimal("0")

    for r in (
        SaleItem.objects.filter(sale__date__gte=date_from, sale__date__lte=date_to)
        .values("sale__sales_rep")
        .annotate(profit=Sum(PROFIT))
    ):
        row(r["sale__sales_rep"])["profit"] += r["profit"] or Decimal("0")

    result = []
    for rr in rows.values():
        rr["net"] = rr["profit"] - rr["expense_total"]  # samaradorlik: foyda − rasxot
        result.append(rr)
    result.sort(key=lambda r: (r["in_som"] + r["profit"]), reverse=True)
    return result


def _kassa_expenses(request):
    """The kassa expense queryset for the window, narrowed by the drawer filters
    (employee, turkum, usul, valyuta). Shared by the page and its CSV export.
    Returns (expenses, dates, filters, rep, reps)."""
    dates = _date_range_context(request)
    filters = {key: request.GET.get(key, "") for key in ("method", "category", "currency", "rep")}
    filters["dan"] = dates["date_from"].isoformat()
    filters["gacha"] = dates["date_to"].isoformat()
    # Admins/managers may filter by any employee; a seller is locked to their own
    # till, so the employee filter is never offered to them.
    if request.user.can_see_all_records:
        reps = User.objects.filter(is_active=True).order_by("first_name", "username")
        rep = reps.filter(pk=filters["rep"]).first() if filters["rep"].isdigit() else None
    else:
        reps = None
        rep = request.user
    expenses = Expense.objects.select_related("created_by").filter(
        date__gte=dates["date_from"], date__lte=dates["date_to"]
    )
    if rep is not None:
        expenses = expenses.filter(created_by=rep)
    if filters["method"] in dict(Payment.Method.choices):
        expenses = expenses.filter(method=filters["method"])
    if filters["category"] in dict(Expense.Category.choices):
        expenses = expenses.filter(category=filters["category"])
    if filters["currency"] in dict(Payment.Currency.choices):
        expenses = expenses.filter(currency=filters["currency"])
    return expenses.order_by("-date", "-created_at"), dates, filters, rep, reps


def kassa_view(request):
    """The cash register (Kassa): two till drawers (so'm + dollar) with income by
    method and running balance, per-employee kassa & performance, and the expense
    list. Visible to everyone — the shared company till. Any filter (employee, turkum,
    usul, valyuta) scopes the figures so a supervisor can drill into one employee."""
    expenses, dates, filters, rep, reps = _kassa_expenses(request)
    date_from, date_to = dates["date_from"], dates["date_to"]
    summary = _kassa_summary(date_from, date_to, rep=rep)

    method_labels = dict(Payment.Method.choices)
    category_labels = dict(Expense.Category.choices)
    currency_labels = dict(Payment.Currency.choices)
    # Only the company view exposes a rep chip; a seller's own scope isn't a filter.
    rep_chip = str(rep) if (reps is not None and rep) else ""
    active_filters = _filter_chips(request, [
        {"param": "rep", "label": "Xodim", "value": rep_chip},
        {"param": "category", "label": "Turkum", "value": category_labels.get(filters["category"], "")},
        {"param": "method", "label": "Usul", "value": method_labels.get(filters["method"], "")},
        {"param": "currency", "label": "Valyuta", "value": currency_labels.get(filters["currency"], "")},
    ])
    export_qs = request.GET.urlencode()
    return render(request, "crm/kassa.html", {
        "summary": summary,
        "expenses": expenses,
        "per_employee": _per_employee_kassa(date_from, date_to) if request.user.can_see_all_records else None,
        "filters": filters,
        "reps": reps,
        "active_filters": active_filters,
        "filter_count": len(active_filters),
        "has_filters": bool(active_filters),
        "filter_url": reverse("kassa"),
        "rep_label": "Xodim",
        "show_daterange_picker": True,
        "keep_daterange": True,
        "show_method": True,
        "show_category": True,
        "show_currency": True,
        "export_url": reverse("expense_export") + (f"?{export_qs}" if export_qs else ""),
        **dates,
    })


XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx_response(filename, sheet_title, headers, rows, number_formats=None):
    """Build an .xlsx download: bold frozen header, one row per record, columns
    sized to their widest value. `number_formats` maps a 1-based column index to
    an Excel format string, applied to that column's data cells."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_title
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    for row in rows:
        ws.append(row)
    number_formats = number_formats or {}
    for i, header in enumerate(headers, start=1):
        letter = get_column_letter(i)
        longest = max([len(str(header))] + [len(str(r[i - 1])) for r in rows])
        ws.column_dimensions[letter].width = min(longest + 2, 40)
        fmt = number_formats.get(i)
        if fmt:
            for cell in ws[letter][1:]:  # data cells only, skip the header
                cell.number_format = fmt
    response = HttpResponse(content_type=XLSX_CONTENT_TYPE)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
    return response


def expense_export(request):
    """Excel (.xlsx) of the kassa expenses for the current window and drawer filters."""
    expenses = _kassa_expenses(request)[0]
    headers = [
        "Sana", "Turkum", "Usul", "Valyuta", "Summa (so'm)",
        "Asl summa", "Kurs", "Izoh", "Kim kiritdi",
    ]
    rows = []
    for e in expenses:
        is_usd = e.currency == Payment.Currency.USD
        rows.append([
            e.date.isoformat(),
            e.get_category_display(),
            e.get_method_display(),
            e.get_currency_display(),
            float(e.amount),
            float(e.original_amount),
            float(e.exchange_rate) if is_usd else "",
            e.note,
            str(e.created_by),
        ])
    number_formats = {5: "#,##0.00", 6: "#,##0.00", 7: "#,##0.00"}
    return _xlsx_response("chiqimlar.xlsx", "Chiqimlar", headers, rows, number_formats)


def expense_create(request):
    """Record a payout from the till. Any logged-in user may add one — staff come to
    the cashier and the expense is written against the kassa (logged for audit)."""
    form = ExpenseForm(request.POST or None)
    title = "Chiqim qo'shish"
    if request.method == "POST":
        if form.is_valid():
            expense = form.save(commit=False)
            expense.created_by = request.user
            expense.save()
            usd = (
                f" · ${expense.original_amount:,.2f} × {expense.exchange_rate:,.0f}"
                if expense.currency == Payment.Currency.USD else ""
            )
            AuditLog.record(
                request.user, AuditLog.Action.CREATE, "Chiqim", expense.pk,
                f"{expense.get_category_display()} — {expense.amount:,.0f} so'm "
                f"({expense.get_method_display()}){usd}",
            )
            messages.success(request, f"Chiqim qo'shildi: {expense.amount:,.0f} so'm.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_expense_modal.html")
    return form_response(request, form, title, modal_template="crm/_expense_modal.html")


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def expense_edit(request, pk):
    """Fix a mistaken expense. Admin/manager only — sellers add but can't edit."""
    expense = get_object_or_404(Expense, pk=pk)
    title = "Chiqimni tahrirlash"
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Chiqim", expense.pk,
                f"{expense.get_category_display()} — {expense.amount:,.0f} so'm",
            )
            messages.success(request, "Chiqim yangilandi.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_expense_modal.html")
    return form_response(request, form, title, modal_template="crm/_expense_modal.html")


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def expense_delete(request, pk):
    """Remove a mistaken expense. Admin/manager only — sellers add but can't erase."""
    expense = get_object_or_404(Expense.objects.select_related("created_by"), pk=pk)
    if request.method == "POST":
        summary = f"{expense.get_category_display()} — {expense.amount:,.0f} so'm"
        expense.delete()
        AuditLog.record(request.user, AuditLog.Action.DELETE, "Chiqim", pk, summary)
        messages.success(request, "Chiqim o'chirildi.")
        return form_reload(request, reverse("kassa"))
    return render_confirm(
        request,
        "Chiqimni o'chirish",
        f"{expense.get_category_display()} — {expense.amount:,.0f} so'm chiqim "
        f"o'chiriladi. Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


def sale_export(request):
    base = (
        Sale.objects.visible_to(request.user)
        .select_related("client", "sales_rep")
        .with_balance()
    )
    sales, _, _, _, _ = _filter_sales(request, base)
    sales = sales.order_by("-date", "-created_at").prefetch_related("items__product")

    headers = [
        "Sana", "Mijoz", "Mahsulot", "Sotuvchi", "O'lchov", "Og'irligi",
        "Narxi", "Umumiy narx", "Tannarx", "Foyda", "To'lov", "Qarz muddati",
    ]
    # One row per line item, so a multi-product receipt still exports cleanly.
    rows = []
    for s in sales:
        deadline = s.debt_deadline.isoformat() if s.debt_deadline else ""
        status = "Qarz" if s.remaining > 0 else "To'langan"
        for item in s.items.all():
            rows.append([
                s.date.isoformat(),
                s.client.name,
                item.product.name,
                str(s.sales_rep),
                item.get_dimension_display(),
                float(item.weight),
                float(item.price),
                float(item.total_price),
                float(item.total_cost),
                float(item.profit),
                status,
                deadline,
            ])
    number_formats = {6: "0.000", 7: "#,##0.00", 8: "#,##0.00", 9: "#,##0.00", 10: "#,##0.00"}
    return _xlsx_response("sotuvlar.xlsx", "Sotuvlar", headers, rows, number_formats)


def sale_detail(request, pk):
    sale = get_object_or_404(
        Sale.objects.visible_to(request.user)
        .select_related("client", "sales_rep")
        .prefetch_related("items__product"),
        pk=pk,
    )
    payments = sale.payments.select_related("created_by").order_by("-date", "-created_at")
    returns = sale.returns.select_related("product", "created_by")
    return render(
        request,
        "crm/sale_detail.html",
        {
            "sale": sale,
            "items": sale.items.all(),
            "payments": payments,
            "returns": returns,
            "returned": sale.returned_amount,
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
            AuditLog.record(
                request.user, AuditLog.Action.CREATE, "Sotuv", sale.pk,
                f"{sale.client.name} — {sale.total_price:,.0f} so'm",
            )
            messages.success(request, "Sotuv qo'shildi (qarz sifatida).")
            _warn_if_negative_stock_items(request, sale)
            return form_success(request, reverse("sale_list"))
        return _render_sale_form(request, form, formset, "Yangi sotuv", invalid=True)
    return _render_sale_form(request, form, formset, "Yangi sotuv")


def _formset_total(formset):
    """Revenue (weight × price) of the formset's surviving (non-deleted) items."""
    total = Decimal("0")
    for f in formset.forms:
        cleaned = getattr(f, "cleaned_data", None)
        if not cleaned or cleaned.get("DELETE"):
            continue
        weight = cleaned.get("weight")
        price = cleaned.get("price")
        if weight is not None and price is not None:
            total += weight * price
    return total


def sale_edit(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    form = SaleForm(request.POST or None, instance=sale, user=request.user)
    formset = SaleItemFormSet(request.POST or None, instance=sale, prefix="items")
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            # An edit must not drop the total below what's already been paid, or
            # the sale would read as over-paid (a negative balance) with no refund.
            paid = sale.paid_amount
            new_total = _formset_total(formset)
            if new_total < paid:
                form.add_error(
                    None,
                    f"Jami summa ({new_total:,.0f} so'm) allaqachon to'langan "
                    f"puldan ({paid:,.0f} so'm) kam bo'lishi mumkin emas.",
                )
                return _render_sale_form(request, form, formset, "Sotuvni tahrirlash", invalid=True)
            sale = form.save()
            formset.save()
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Sotuv", sale.pk,
                f"{sale.client.name} — {sale.total_price:,.0f} so'm",
            )
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
                sale=sale, amount=remaining, amount_original=remaining,
                method=Payment.Method.CASH,
                kind=Payment.Kind.SALE, date=timezone.localdate(), created_by=request.user,
            )
            AuditLog.record(
                request.user, AuditLog.Action.PAYMENT, "To'lov", sale.pk,
                f"{sale.client.name} — {remaining:,.0f} so'm (naqd, to'liq)",
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
                amount_original=form.cleaned_data["amount_original"],
                currency=form.cleaned_data["currency"],
                exchange_rate=form.cleaned_data["exchange_rate"],
                method=form.cleaned_data["method"],
                commission=form.cleaned_data["commission"],
                commission_percent=form.cleaned_data["commission_percent"],
                note=form.cleaned_data["note"],
                kind=Payment.Kind.DEBT,
                date=timezone.localdate(),
                created_by=request.user,
            )
            AuditLog.record(
                request.user, AuditLog.Action.PAYMENT, "To'lov", sale.pk,
                f"{sale.client.name} — {form.cleaned_data['amount']:,.0f} so'm "
                f"({form.cleaned_data['method']}){_usd_note(form.cleaned_data)}",
            )
            if sale.debt_remaining <= 0:
                messages.success(request, "Qarz to'liq to'landi.")
            else:
                messages.success(
                    request, f"To'lov qabul qilindi. Qoldiq: {sale.debt_remaining:,.0f} so'm."
                )
            return form_reload(request, reverse("debt_list"))
        return _render_debt_pay(request, sale, form, invalid=True)
    form = DebtPaymentForm(
        initial={"amount": _clean_amount(remaining), "method": Payment.Method.CASH}
    )
    return _render_debt_pay(request, sale, form)


def _render_return_form(request, sale, form, invalid=False):
    context = {"form": form, "sale": sale, "title": f"Qaytarish: {sale.client.name}"}
    if is_ajax(request):
        return render(request, "crm/_return_modal.html", context, status=422 if invalid else 200)
    return render(request, "crm/_return_page.html", context)


def sale_return(request, pk):
    sale = get_object_or_404(
        Sale.objects.visible_to(request.user).prefetch_related("items__product", "returns"),
        pk=pk,
    )
    if request.method == "POST":
        form = ReturnForm(request.POST, sale=sale)
        if form.is_valid():
            ret = form.save(commit=False)
            ret.sale = sale
            ret.created_by = request.user
            ret.save()
            AuditLog.record(
                request.user, AuditLog.Action.RETURN, "Qaytarish", sale.pk,
                f"{sale.client.name} — {ret.amount:,.0f} so'm ({ret.product.name})",
            )
            messages.success(request, f"Qaytarish qabul qilindi: {ret.amount:,.0f} so'm.")
            return form_reload(request, reverse("sale_detail", args=[sale.pk]))
        return _render_return_form(request, sale, form, invalid=True)
    form = ReturnForm(sale=sale, initial={"restock": True})
    return _render_return_form(request, sale, form)


def sale_delete(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    # A sale with recorded payments must not be deleted — it would silently wipe
    # money already booked in the till/ledger. Reverse the payments first.
    if sale.payments.exists():
        messages.error(
            request,
            "Bu sotuvni o'chirib bo'lmaydi — unga to'lovlar yozilgan. "
            "Avval to'lovlarni bekor qiling.",
        )
        return form_reload(request, reverse("sale_list"))
    if request.method == "POST":
        summary = f"{sale.client.name} — {sale.total_price:,.0f} so'm"
        sale_pk = sale.pk
        sale.delete()
        AuditLog.record(request.user, AuditLog.Action.DELETE, "Sotuv", sale_pk, summary)
        messages.success(request, "Sotuv o'chirildi.")
        return form_reload(request, reverse("sale_list"))
    return render_confirm(
        request,
        "Sotuvni o'chirish",
        "Bu sotuv butunlay o'chiriladi. Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )
