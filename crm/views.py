import math
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, F, Max, ProtectedError, Q, Sum
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
    PaymentEditForm,
    ProductForm,
    ProductionReceiptForm,
    ProductionReceiptItemFormSet,
    ProductionRemittanceForm,
    ProfitPayoutForm,
    ReturnForm,
    SaleForm,
    SaleItemFormSet,
    StockAdjustForm,
    StockEntryForm,
)
from .models import (
    COST,
    ITEM_WEIGHT_KG,
    PAYING_KINDS,
    PAYMENT_NET,
    PROFIT,
    RETURN_AMOUNT,
    RETURN_COST,
    REVENUE,
    AuditLog,
    Client,
    Expense,
    Payment,
    client_advance_balance,
    Product,
    ProductionReceipt,
    ProductionRemittance,
    ProfitPayout,
    Return,
    Sale,
    SaleItem,
    StockEntry,
    seller_cash_on_hand,
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


def _ombor_shortfall(seller, formset, existing_sale=None):
    """Products on the sale whose requested kg exceed the seller's own ombor.

    Returns a list of (product, requested_kg, available_kg). On edit, this sale's
    current lines are added back to availability — they're already counted as sold
    against the seller, so editing must not double-count them."""
    requested = {}  # product_pk -> {"product": Product, "kg": Decimal}
    for f in formset.forms:
        cd = getattr(f, "cleaned_data", None)
        if not cd or cd.get("DELETE"):
            continue
        product, weight = cd.get("product"), cd.get("weight")
        if not product or weight is None:
            continue
        kg = weight / Decimal("1000") if cd.get("dimension") == Sale.Dimension.G else weight
        row = requested.setdefault(product.pk, {"product": product, "kg": Decimal("0")})
        row["kg"] += kg
    if not requested:
        return []
    on_hand = {
        p.pk: p.stock
        for p in Product.objects.filter(pk__in=requested).with_stock(seller=seller)
    }
    freed = {}
    if existing_sale is not None:
        for item in existing_sale.items.all():
            freed[item.product_id] = freed.get(item.product_id, Decimal("0")) + item.weight_kg
    shortfalls = []
    for pk, row in requested.items():
        available = (on_hand.get(pk) or Decimal("0")) + freed.get(pk, Decimal("0"))
        if row["kg"] > available:
            shortfalls.append((row["product"], row["kg"], available))
    return shortfalls


def _mark_fulfilment(sale, shortfall, only_unset=False):
    """Set each line's fulfilment after a sale saves. In-stock lines are ready on
    the sale date; a line whose product was short is a pending zakaz (fulfilled_at
    stays NULL until stock is bound to it). On edit (`only_unset`) already-fulfilled
    lines are left untouched."""
    short_pks = {p.pk for p, _req, _avail in shortfall}
    items = sale.items.all()
    if only_unset:
        items = items.filter(fulfilled_at__isnull=True)
    for item in items:
        if item.product_id in short_pks:
            item.fulfilled_kg = Decimal("0")
            item.fulfilled_at = None
        else:
            item.fulfilled_kg = item.weight_kg
            item.fulfilled_at = sale.date
        item.save(update_fields=["fulfilled_kg", "fulfilled_at"])


def _zakaz_confirm_response(request, form, formset, title, shortfall):
    """A modal oversell asks the browser to pop a confirm dialog (the X-Zakaz-Confirm
    signal) rather than rejecting. Without JS, fall back to the inline warning
    re-render so the flow still works."""
    if is_ajax(request):
        msg = "; ".join(
            f"{p.name}: qoldiq {available:.0f} kg, so'raldi {requested:.0f} kg"
            for p, requested, available in shortfall
        )
        resp = JsonResponse({"message": msg}, status=409)
        resp["X-Zakaz-Confirm"] = "1"
        return resp
    return _render_sale_form(request, form, formset, title, zakaz_shortfall=shortfall)


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

    band = inner_w / (n - 1) if n > 1 else inner_w
    for i, d in enumerate(data):
        d["x"] = round(pad_l + (inner_w * i / (n - 1) if n > 1 else inner_w / 2), 2)
        d["y_rev"] = _y(d["revenue"])
        d["y_profit"] = _y(d["profit"])
        # Full-height transparent hit band so a click anywhere in the month's
        # column drills into it — not just on the tiny label/point.
        left = max(d["x"] - band / 2, 0.0)
        right = min(d["x"] + band / 2, vb_w)
        d["hit_x"] = round(left, 2)
        d["hit_w"] = round(right - left, 2)

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
        "vb_h": vb_h,
        "viewbox": f"0 0 {vb_w:g} {vb_h:g}",
    }


def _spark_points(values, width=118.0, height=30.0, pad=3.0):
    """Scale a numeric series into an SVG polyline points string for a KPI
    sparkline. A flat or empty series renders as a centred flat line."""
    nums = [float(v or 0) for v in values]
    n = len(nums)
    if n == 0:
        return ""
    lo, hi = min(nums), max(nums)
    span = hi - lo
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad
    pts = []
    for i, v in enumerate(nums):
        x = pad + (inner_w * i / (n - 1) if n > 1 else inner_w / 2)
        frac = (v - lo) / span if span else 0.5   # higher value → higher on screen
        y = pad + (1 - frac) * inner_h
        pts.append(f"{round(x, 1)},{round(y, 1)}")
    return " ".join(pts)


def _kpi_sparklines(flow, scoped, clients_q, months=6):
    """Six-month trend for each dashboard KPI as sparkline point strings. Uses the
    same rep/client/method scoping as the rest of the dashboard (date window aside,
    since a sparkline shows the longer trend, not just the selected period)."""
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

    item_rows = (
        SaleItem.objects.filter(sale__in=flow, sale__date__gte=start)
        .annotate(mon=TruncMonth("sale__date"))
        .values("mon")
        .annotate(revenue=Sum(REVENUE), profit=Sum(PROFIT))
    )
    rev_by = {(r["mon"].year, r["mon"].month): r["revenue"] or 0 for r in item_rows}
    prof_by = {(r["mon"].year, r["mon"].month): r["profit"] or 0 for r in item_rows}

    cnt_rows = (
        flow.filter(date__gte=start).annotate(mon=TruncMonth("date"))
        .values("mon").annotate(c=Count("pk"))
    )
    cnt_by = {(r["mon"].year, r["mon"].month): r["c"] for r in cnt_rows}

    cli_rows = (
        clients_q.filter(created_at__date__gte=start)
        .annotate(mon=TruncMonth("created_at"))
        .values("mon").annotate(c=Count("pk"))
    )
    cli_by = {(r["mon"].year, r["mon"].month): r["c"] for r in cli_rows}

    debt_rows = (
        scoped.outstanding().filter(date__gte=start)
        .annotate(mon=TruncMonth("date"))
        .values("mon").annotate(c=Count("pk"))
    )
    debt_by = {(r["mon"].year, r["mon"].month): r["c"] for r in debt_rows}

    revenue, profit, avg, clients, debt = [], [], [], [], []
    for yy, mm in buckets:
        rev = float(rev_by.get((yy, mm), 0))
        cnt = cnt_by.get((yy, mm), 0)
        revenue.append(rev)
        profit.append(float(prof_by.get((yy, mm), 0)))
        avg.append(rev / cnt if cnt else 0)
        clients.append(cli_by.get((yy, mm), 0))
        debt.append(debt_by.get((yy, mm), 0))

    return {
        "revenue": _spark_points(revenue),
        "profit": _spark_points(profit),
        "avg": _spark_points(avg),
        "clients": _spark_points(clients),
        "debt": _spark_points(debt),
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


def _debt_overview(sales, aging_filter=None, top=5):
    """Outstanding receivables split into aging buckets (by days overdue) plus the
    biggest debtors — the dashboard's debt-at-a-glance. A company that sells heavily
    on credit needs to see which money is at risk and who to collect from first.

    `aging_filter` (a bucket key) cross-filters the top-debtors list to that bucket
    while the donut keeps showing the full split, so another bucket stays one click away."""
    today = timezone.localdate()
    open_sales = sales.outstanding().select_related("client")

    # Risk gradient: neutral (not yet due) → gold → orange → red (deeply overdue).
    aging_defs = [
        ("current", "Muddati kelmagan", "color-mix(in srgb, var(--accent) 45%, var(--surface))"),
        ("d1_7", "1–7 kun kechikkan", "var(--warning)"),
        ("d8_30", "8–30 kun kechikkan", "color-mix(in srgb, var(--warning) 45%, var(--danger))"),
        ("d30", "30+ kun kechikkan", "var(--danger)"),
    ]
    aging = [{"key": key, "label": lbl, "color": clr, "amount": Decimal("0"), "count": 0}
             for key, lbl, clr in aging_defs]

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

        # The donut sees every receipt; the debtor list only the selected bucket.
        if aging_filter and aging[idx]["key"] != aging_filter:
            continue
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

    # Draw the aging split as a donut (always the full picture), then fold each
    # bucket's receipt count back onto its arc segment for the legend.
    aging_donut = _donut([(b["key"], b["label"], b["amount"], b["color"]) for b in aging])
    for segment, bucket in zip(aging_donut["segments"], aging):
        segment["count"] = bucket["count"]
        segment["active"] = aging_filter == bucket["key"]

    top_debtors = sorted(clients.values(), key=lambda d: d["amount"], reverse=True)[:top]
    peak = max((d["amount"] for d in top_debtors), default=Decimal("1")) or Decimal("1")
    for debtor in top_debtors:
        debtor["pct"] = round(float(debtor["amount"] / peak) * 100, 2)

    return {
        "aging": aging,
        "aging_donut": aging_donut,
        "top_debtors": top_debtors,
        "selected": aging_filter,
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
    filters = {key: request.GET.get(key, "") for key in ("rep", "client", "method", "aging")}
    rep_id = filters["rep"] if (filters["rep"].isdigit() and request.user.can_see_all_records) else ""
    client_id = filters["client"] if filters["client"].isdigit() else ""
    method = filters["method"] if filters["method"] in Payment.Method.values else ""
    aging = filters["aging"] if filters["aging"] in ("current", "d1_7", "d8_30", "d30") else ""

    scoped = Sale.objects.visible_to(request.user)
    if rep_id:
        scoped = scoped.filter(sales_rep_id=rep_id)
    if client_id:
        scoped = scoped.filter(client_id=client_id)
    # `flow` narrows the sales set by payment method for the flow figures, but debt
    # keeps using `scoped` — an unpaid receipt has no payment row of any method.
    flow = scoped.filter(payments__method=method).distinct() if method else scoped
    # Opening-balance carry-overs are receivables, not sales — keep them out of the
    # period revenue/count/recent figures (debt below still uses `scoped`, with them in).
    period = flow.filter(date__gte=date_from, date__lte=date_to).real()

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
    aging_labels = {
        "current": "Muddati kelmagan", "d1_7": "1–7 kun kechikkan",
        "d8_30": "8–30 kun kechikkan", "d30": "30+ kun kechikkan",
    }
    filters["dan"] = date_from.isoformat()
    filters["gacha"] = date_to.isoformat()
    filters["q"] = ""
    active_filters = _filter_chips(request, [
        {"param": "rep", "label": "Sotuvchi", "value": str(rep_obj) if rep_obj else ""},
        {"param": "client", "label": "Mijoz", "value": client_obj.name if client_obj else ""},
        {"param": "method", "label": "To'lov usuli", "value": method_labels.get(method, "")},
        {"param": "aging", "label": "Qarz muddati", "value": aging_labels.get(aging, "")},
    ])

    context = {
        "monthly": _monthly_series(flow),
        "sparks": _kpi_sparklines(flow, scoped, new_clients_q),
        "donut": _payment_donut(period),
        "debt": _debt_overview(scoped, aging_filter=aging),
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
        .annotate(sale_count=Count("sales"), last_sale=Max("sales__date"))
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
    # Advance (prepaid) balance shown per row. A seller sees their own till's balance;
    # an admin/manager sees the client's total held across every seller's till.
    scope = None if request.user.can_see_all_records else request.user
    for c in page:
        c.advance = client_advance_balance(c, scope)
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


def _unique_product_sku(name):
    """A short, unique SKU derived from the product name (auto-assigned on a quick
    add; the admin can rename it later)."""
    base = "".join(c for c in name.upper() if c.isalnum())[:8] or "MHS"
    sku, i = base, 1
    while Product.objects.filter(sku=sku).exists():
        i += 1
        sku = f"{base}{i}"
    return sku


def product_quick_create(request):
    """Create a product inline (from the receipt form) so a seller can log goods for
    a product the admin hasn't defined yet. Returns it as JSON. A same-name product
    is reported back (409) so the caller can reuse it, unless allow_duplicate is set."""
    if request.method != "POST":
        return JsonResponse({"error": "POST kerak"}, status=405)
    name = (request.POST.get("name") or "").strip()
    if not name:
        return JsonResponse({"error": "Nom kiriting"}, status=400)
    if not request.POST.get("allow_duplicate") and Product.objects.filter(name__iexact=name).exists():
        return JsonResponse({"duplicate": True, "error": "Bu nomli mahsulot bor"}, status=409)

    def _dec(key):
        raw = (request.POST.get(key) or "").replace(" ", "").replace(",", ".")
        try:
            return Decimal(raw) if raw else Decimal("0")
        except (ArithmeticError, ValueError):
            return Decimal("0")

    product = Product.objects.create(
        name=name, sku=_unique_product_sku(name),
        price=_dec("price"), cost_price=_dec("cost_price"),
    )
    AuditLog.record(
        request.user, AuditLog.Action.CREATE, "Mahsulot", product.pk, f"{name} (tez qo'shildi)"
    )
    return JsonResponse({"id": product.pk, "text": str(product)})


def client_edit(request, pk):
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    form = ClientForm(
        request.POST or None, instance=client, user=request.user, check_duplicates=False
    )
    title = f"Tahrirlash: {client.name}"
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"“{client.name}” mijozi yangilandi.")
            return form_reload(request, reverse("client_list"))
        return form_response(request, form, title, invalid=True)
    return form_response(request, form, title)


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
        return form_reload(request, reverse("client_list"))
    return render_confirm(
        request,
        "Mijozni o'chirish",
        f"“{client.name}” mijozi o'chiriladi. Bu amalni qaytarib bo'lmaydi.",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


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

# Paket catalogue facets, mirroring seed_paket_products: a SKU reads
# "{size}-{micron}-{colour}" (e.g. "1,5m-015-oq"), so each facet is a slice of it.
# "-01-" never matches "-015-", so the micron grades stay distinct.
PAKET_COLORS = [("oq", "ОҚ"), ("qora", "ҚОРА"), ("novot", "НОВОТ")]
PAKET_SIZES = [("1,5m", "1,5м"), ("2m", "2м"), ("6m", "6м")]
PAKET_MICRONS = ["015", "01", "08", "06", "05", "04", "03", "02"]


def product_list(request):
    # A plain shared catalog — the reference list sellers pick from when selling.
    # With 56 paket SKUs a text search alone is coarse, so the drawer also filters
    # by the three facets encoded in the SKU.
    products = Product.objects.order_by("name")
    filters = {key: request.GET.get(key, "") for key in ("color", "size", "micron")}
    filters["q"] = request.GET.get("q", "").strip()

    if filters["q"]:
        products = products.filter(
            Q(name__icontains=filters["q"]) | Q(sku__icontains=filters["q"])
        )

    colors, sizes = dict(PAKET_COLORS), dict(PAKET_SIZES)
    # Only known facet values bite; anything else is ignored rather than 0-matching.
    if filters["color"] in colors:
        products = products.filter(sku__endswith=f"-{filters['color']}")
    if filters["size"] in sizes:
        products = products.filter(sku__startswith=f"{filters['size']}-")
    if filters["micron"] in PAKET_MICRONS:
        products = products.filter(sku__contains=f"-{filters['micron']}-")

    active_filters = _filter_chips(request, [
        {"param": "color", "label": "Rang", "value": colors.get(filters["color"], "")},
        {"param": "size", "label": "O'lcham", "value": sizes.get(filters["size"], "")},
        {"param": "micron", "label": "Mikron",
         "value": filters["micron"] if filters["micron"] in PAKET_MICRONS else ""},
    ])

    page = Paginator(products, 25).get_page(request.GET.get("page"))
    return render(request, "crm/product_list.html", {
        "page": page,
        "q": filters["q"],
        "filters": filters,
        "active_filters": active_filters,
        "filter_count": len(active_filters),
        "has_filters": bool(active_filters),
        "filter_url": reverse("product_list"),
        "search_placeholder": "Nomi bo'yicha qidirish…",
        "paket_colors": PAKET_COLORS,
        "paket_sizes": PAKET_SIZES,
        "paket_microns": PAKET_MICRONS,
    })


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    recent_items = product.sale_items.select_related("sale", "sale__client").order_by(
        "-sale__date", "-sale__created_at"
    )
    # The warehouse is shared, so everyone sees the stock-movement log. Sellers
    # still see only their OWN recent sales of the product. Filter before slicing.
    entries = product.stock_entries.select_related("created_by")[:50]
    if not request.user.can_see_all_records:
        recent_items = recent_items.filter(sale__sales_rep=request.user)
    recent_items = recent_items[:10]
    context = {
        "product": product,
        "current_stock": product.current_stock,
        "total_received": product.total_received,
        "total_sold": product.total_sold,
        "entries": entries,
        "recent_items": recent_items,
    }
    return render(request, "crm/product_detail.html", context)


def product_create(request):
    form = ProductForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            product = form.save()
            messages.success(request, f"“{product.name}” mahsuloti qo'shildi.")
            return form_success(request, reverse("product_detail", args=[product.pk]))
        return form_response(request, form, "Yangi mahsulot", invalid=True)
    return form_response(request, form, "Yangi mahsulot")


def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    title = f"Tahrirlash: {product.name}"
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"“{product.name}” mahsuloti yangilandi.")
            return form_reload(request, reverse("product_detail", args=[product.pk]))
        return form_response(request, form, title, invalid=True)
    return form_response(request, form, title)


def product_delete(request, pk):
    """Remove a product from the shared catalogue. Blocked (ProtectedError) when
    it has any sales or returns — those records must keep pointing at a real
    product. A product with only stock entries deletes cleanly (entries cascade)."""
    product = get_object_or_404(Product, pk=pk)
    if request.method == "POST":
        name = product.name
        try:
            product.delete()
        except ProtectedError:
            messages.error(
                request,
                f"“{name}” mahsulotini o'chirib bo'lmaydi — sotuv yoki "
                f"qaytarishlarda ishlatilgan.",
            )
            return form_reload(request, reverse("product_list"))
        AuditLog.record(request.user, AuditLog.Action.DELETE, "Mahsulot", pk, name)
        messages.success(request, f"“{name}” mahsuloti o'chirildi.")
        return form_reload(request, reverse("product_list"))
    return render_confirm(
        request,
        "Mahsulotni o'chirish",
        f"“{product.name}” mahsuloti o'chiriladi. Bu amalni qaytarib bo'lmaydi.",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


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
        .real()  # opening-balance carry-overs live on the Qarzlar page, not here
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


def _apply_advance_to_open_sales(client, seller):
    """Spend a client's prepaid advance (seller-bound) on their open receipts, oldest
    first. Each slice becomes an ADVANCE_USED payment that settles part of a sale
    WITHOUT adding new till income — the cash already entered the till as the
    ADVANCE_IN deposit. Idempotent: a sale already covered contributes nothing, so it
    is safe to call after every sale AND after every fresh deposit. Only this seller's
    own sales to the client are touched (their till holds the money). Returns the
    total so'm applied."""
    balance = client_advance_balance(client, seller)
    if balance <= 0:
        return Decimal("0")
    sales = (
        Sale.objects.filter(client=client, sales_rep=seller)
        .with_balance()
        .filter(remaining__gt=0)
        .order_by("date", "created_at")
    )
    applied = Decimal("0")
    with transaction.atomic():
        for sale in sales:
            if balance <= 0:
                break
            due = sale.remaining or Decimal("0")
            use = min(balance, due)
            if use <= 0:
                continue
            Payment.objects.create(
                sale=sale,
                client=client,
                amount=use,
                amount_original=use,
                currency=Payment.Currency.UZS,
                method=Payment.Method.CASH,
                commission=Decimal("0"),
                commission_percent=Decimal("0"),
                note="Avansdan yechildi",
                kind=Payment.Kind.ADVANCE_USED,
                date=timezone.localdate(),
                created_by=seller,
            )
            balance -= use
            applied += use
    return applied


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


def _method_label(code):
    """The Uzbek display name for a payment-method code (naqd/karta/o'tkazma)."""
    return dict(Payment.Method.choices).get(code, code)


def _kg(value):
    """A kg amount without trailing decimal zeros — '23.000' → '23', '23.5' → '23.5'."""
    return ("{:,.3f}".format(value)).rstrip("0").rstrip(".")


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
                f"Mijoz {client.name} qarz to'lovi "
                f"({_method_label(form.cleaned_data['method'])}){_usd_note(form.cleaned_data)} "
                f"— {form.cleaned_data['amount']:,.0f} so'm",
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


def _render_client_advance(request, client, balance, form, invalid=False):
    context = {
        "form": form,
        "client": client,
        "advance_balance": balance,
        "title": f"Avans qabul qilish: {client.name}",
    }
    if is_ajax(request):
        return render(
            request, "crm/_client_advance_modal.html", context,
            status=422 if invalid else 200,
        )
    return render(request, "crm/_client_advance_page.html", context)


def client_advance_pay(request, pk):
    """Take an advance (oldindan to'lov) from a client into the seller's till.

    The cash enters the kassa now (ADVANCE_IN) — it is real income the moment it's
    received. It is then spent oldest-debt-first on the client's open receipts; any
    surplus stays as their advance balance to cover future sales (it is NOT refunded).
    Advance is seller-bound: it sits in the till of whoever took it."""
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    balance = client_advance_balance(client, request.user)
    if request.method == "POST":
        form = DebtPaymentForm(request.POST)  # no max_amount — an advance is uncapped
        if form.is_valid():
            cd = form.cleaned_data
            Payment.objects.create(
                client=client,
                sale=None,
                amount=cd["amount"],
                amount_original=cd["amount_original"],
                currency=cd["currency"],
                exchange_rate=cd["exchange_rate"],
                method=cd["method"],
                commission=cd["commission"],
                commission_percent=cd["commission_percent"],
                note=cd["note"],
                kind=Payment.Kind.ADVANCE_IN,
                date=timezone.localdate(),
                created_by=request.user,
            )
            applied = _apply_advance_to_open_sales(client, request.user)
            AuditLog.record(
                request.user, AuditLog.Action.PAYMENT, "To'lov", client.pk,
                f"Mijoz {client.name} avans to'lovi "
                f"({_method_label(cd['method'])}){_usd_note(cd)} "
                f"— {cd['amount']:,.0f} so'm",
            )
            left = client_advance_balance(client, request.user)
            msg = f"Avans qabul qilindi: {cd['amount']:,.0f} so'm."
            if applied > 0:
                msg += f" {applied:,.0f} so'm ochiq qarzga taqsimlandi."
            if left > 0:
                msg += f" Balansda: {left:,.0f} so'm."
            messages.success(request, msg)
            return form_reload(request, reverse("client_list"))
        return _render_client_advance(request, client, balance, form, invalid=True)
    form = DebtPaymentForm(initial={"method": Payment.Method.CASH})
    return _render_client_advance(request, client, balance, form)


def _advance_in_qs(user):
    """Advance deposits this user is allowed to touch — their own, or all for an
    admin/manager."""
    qs = Payment.objects.filter(kind=Payment.Kind.ADVANCE_IN).select_related("client")
    if not user.can_see_all_records:
        qs = qs.filter(created_by=user)
    return qs


def _reconcile_client_advance(client, seller):
    """Bring a client's advance (for one seller) back into balance after a deposit
    was changed or removed. If deposits have shrunk below what sales already drew
    (balance < 0), the newest ADVANCE_USED allocations are peeled back — reverting
    those sales to debt — until the pool is non-negative. Then any advance still left
    is re-applied to open receipts, oldest first. Idempotent."""
    with transaction.atomic():
        balance = client_advance_balance(client, seller)
        if balance < 0:
            used = Payment.objects.filter(
                client=client, created_by=seller, kind=Payment.Kind.ADVANCE_USED
            ).order_by("-date", "-created_at")
            for u in used:
                if balance >= 0:
                    break
                balance += u.net_amount  # freeing this returns its money to the pool
                u.delete()               # ...and the sale it covered owes again
        _apply_advance_to_open_sales(client, seller)


def advance_edit(request, pk):
    """Fix a mistaken advance deposit (amount / method / note). If the new amount is
    smaller than what sales already drew, the excess is clawed back automatically
    (those sales revert to debt) — see `_reconcile_client_advance`."""
    payment = get_object_or_404(_advance_in_qs(request.user), pk=pk)
    client, seller = payment.client, payment.created_by
    if request.method == "POST":
        form = DebtPaymentForm(request.POST)
        if form.is_valid():
            cd = form.cleaned_data
            payment.amount = cd["amount"]
            payment.amount_original = cd["amount_original"]
            payment.currency = cd["currency"]
            payment.exchange_rate = cd["exchange_rate"]
            payment.method = cd["method"]
            payment.commission = cd["commission"]
            payment.commission_percent = cd["commission_percent"]
            payment.note = cd["note"]
            payment.save()
            # Re-apply a bigger deposit, or claw back a smaller one, then settle debts.
            _reconcile_client_advance(client, seller)
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "To'lov", client.pk,
                f"Mijoz {client.name} avansi o'zgartirildi — {payment.amount:,.0f} so'm",
            )
            messages.success(request, "Avans yangilandi.")
            return form_reload(request, reverse("kassa"))
        return _render_advance_edit(request, payment, form, invalid=True)
    form = DebtPaymentForm(initial={
        "amount": _clean_amount(payment.original_amount),
        "method": payment.method,
        "currency": payment.currency,
        "exchange_rate": payment.exchange_rate or "",
        "commission_percent": payment.commission_percent or "",
        "note": payment.note,
    })
    return _render_advance_edit(request, payment, form)


def _render_advance_edit(request, payment, form, invalid=False):
    balance = client_advance_balance(payment.client, payment.created_by)
    context = {
        "form": form,
        "client": payment.client,
        "advance_balance": balance,
        "title": f"Avansni tahrirlash: {payment.client.name}",
    }
    if is_ajax(request):
        return render(
            request, "crm/_advance_edit_modal.html", context,
            status=422 if invalid else 200,
        )
    return render(request, "crm/_client_advance_page.html", context)


def advance_delete(request, pk):
    """Remove a mistaken advance deposit. If sales had already drawn on it, those
    allocations are peeled back and the sales revert to debt (see
    `_reconcile_client_advance`), so the money trail stays consistent."""
    payment = get_object_or_404(_advance_in_qs(request.user), pk=pk)
    client, seller = payment.client, payment.created_by
    spent = client_advance_balance(client, seller) < payment.net_amount
    if request.method == "POST":
        summary = f"{client.name} — avans {payment.amount:,.0f} so'm"
        payment.delete()
        _reconcile_client_advance(client, seller)
        AuditLog.record(request.user, AuditLog.Action.VOID, "To'lov", client.pk, summary)
        messages.success(request, "Avans o'chirildi.")
        return form_reload(request, reverse("kassa"))
    warn = (
        " Bu avans allaqachon sotuv(lar)ga ishlatilgan — o'chirilsa, o'sha sotuvlar "
        "qaytadan qarzga aylanadi."
        if spent else ""
    )
    return render_confirm(
        request,
        "Avansni o'chirish",
        f"“{client.name}” — {payment.amount:,.0f} so'm avans o'chiriladi.{warn} Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


def payment_delete(request, pk):
    """Void a mistaken payment by removing it. The debt it covered is restored
    automatically (remaining is derived). Admins/managers may void any payment;
    a seller may void only payments they took in themselves."""
    qs = Payment.objects.select_related("sale", "sale__client")
    if not request.user.can_see_all_records:
        qs = qs.filter(created_by=request.user)
    payment = get_object_or_404(qs, pk=pk)
    if payment.kind in (Payment.Kind.ADVANCE_IN, Payment.Kind.ADVANCE_USED):
        messages.error(request, "Avans to'lovi bu yerdan o'chirilmaydi.")
        return form_reload(request, reverse("kassa"))
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


def payment_edit(request, pk):
    """Fix a mistaken payment (Kirim) — amount, currency, method, commission, note.
    The sale's remaining debt re-derives from the new net automatically. Admins/
    managers may edit any payment; a seller may edit only payments they took in
    themselves. The net is capped so the sale can't become over-paid."""
    qs = Payment.objects.select_related("sale", "sale__client")
    if not request.user.can_see_all_records:
        qs = qs.filter(created_by=request.user)
    payment = get_object_or_404(qs, pk=pk)
    if payment.kind in (Payment.Kind.ADVANCE_IN, Payment.Kind.ADVANCE_USED):
        messages.error(request, "Avans to'lovi bu yerdan tahrirlanmaydi.")
        return form_reload(request, reverse("kassa"))
    # This receipt's ceiling: the sale's remaining already excludes this payment's
    # current net, so add it back to get how much this one may cover.
    max_amount = payment.sale.debt_remaining + payment.net_amount
    title = "To'lovni tahrirlash"
    form = PaymentEditForm(request.POST or None, instance=payment, max_amount=max_amount)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "To'lov", payment.sale_id,
                f"Mijoz {payment.sale.client.name} to'lovi "
                f"({payment.get_method_display()}){_usd_note(form.cleaned_data)} "
                f"— {payment.amount:,.0f} so'm",
            )
            messages.success(request, "To'lov yangilandi.")
            return form_success(request, reverse("kassa"))
        return form_response(
            request, form, title, invalid=True,
            modal_template="crm/_payment_edit_modal.html",
        )
    return form_response(
        request, form, title, modal_template="crm/_payment_edit_modal.html"
    )


def audit_list(request):
    """The money-action audit trail. Admins/managers see every action; a seller
    sees only their own. The trail only grows, so it is filterable by who acted,
    which action, a date window and free text — otherwise a single entry becomes
    unfindable past the first few pages."""
    logs = AuditLog.objects.select_related("user")
    if not request.user.can_see_all_records:
        logs = logs.filter(user=request.user)

    filters = {key: request.GET.get(key, "") for key in ("rep", "action", "dan", "gacha")}
    filters["q"] = request.GET.get("q", "").strip()

    if filters["q"]:
        logs = logs.filter(
            Q(summary__icontains=filters["q"]) | Q(target_type__icontains=filters["q"])
        )

    reps = rep_obj = None
    if request.user.can_see_all_records:
        reps = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
        if filters["rep"].isdigit():
            rep_obj = reps.filter(pk=filters["rep"]).first()
            if rep_obj:
                logs = logs.filter(user=rep_obj)

    actions = AuditLog.Action.choices
    action_label = dict(actions).get(filters["action"], "")
    if action_label:
        logs = logs.filter(action=filters["action"])

    # A full history, so dates only bite once the user actually sets them.
    date_from = _parse_date(filters["dan"])
    date_to = _parse_date(filters["gacha"])
    if date_from and date_to and date_to < date_from:
        date_from, date_to = date_to, date_from
        filters["dan"], filters["gacha"] = date_from.isoformat(), date_to.isoformat()
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)

    active_filters = _filter_chips(request, [
        {"param": "rep", "label": "Kim", "value": str(rep_obj) if rep_obj else ""},
        {"param": "action", "label": "Amal", "value": action_label},
        {"param": "dan", "label": "Sanadan",
         "value": date_from.strftime("%d.%m.%Y") if date_from else ""},
        {"param": "gacha", "label": "Sanagacha",
         "value": date_to.strftime("%d.%m.%Y") if date_to else ""},
    ])

    page = Paginator(logs, 50).get_page(request.GET.get("page"))
    return render(request, "crm/audit_list.html", {
        "page": page,
        "filters": filters,
        "reps": reps,
        "rep_label": "Kim",
        "actions": actions,
        "active_filters": active_filters,
        "filter_count": len(active_filters),
        "has_filters": bool(active_filters),
        "filter_url": reverse("audit_list"),
        "search_placeholder": "Tafsilot bo'yicha qidirish…",
    })


# --- Kassa (cash register) ----------------------------------------------------

def _currency_till(payments, expenses, refunds, date_from, date_to, *, som):
    """Income (by method), expense, refunds and the running balance for ONE currency
    drawer.

    The so'm drawer counts net so'm (amount − bank fee); the dollar drawer counts the
    physical dollars handed over (`amount_original`). The balance is cumulative:
    opening = everything strictly before date_from, closing = opening + in − out.

    Refunds are a third flow, kept apart from expenses: money handed back on an
    over-returned sale leaves the drawer just the same, but it is the client's money
    coming back, not a cost the business bore."""
    field = PAYMENT_NET if som else F("amount_original")
    exp_field = "amount" if som else "amount_original"

    def income(**flt):
        return payments.filter(**flt).aggregate(s=Sum(field))["s"] or Decimal("0")

    def outflow(**flt):
        return expenses.filter(**flt).aggregate(s=Sum(exp_field))["s"] or Decimal("0")

    def refund(**flt):
        return refunds.filter(**flt).aggregate(s=Sum(exp_field))["s"] or Decimal("0")

    window = {"date__gte": date_from, "date__lte": date_to}
    cash = income(method=Payment.Method.CASH, **window)
    card = income(method=Payment.Method.CARD, **window)
    bank = income(method=Payment.Method.TRANSFER, **window)
    total_in = cash + card + bank
    total_out = outflow(**window)
    total_refund = refund(**window)
    opening = (
        income(date__lt=date_from)
        - outflow(date__lt=date_from)
        - refund(date__lt=date_from)
    )
    return {
        "cash": cash, "card": card, "bank": bank, "income": total_in,
        "expense": total_out, "refund": total_refund, "opening": opening,
        "closing": opening + total_in - total_out - total_refund,
    }


def _kassa_supplier_cost(date_from, date_to, rep=None):
    """Total supplier cost (Tannarx / asl narx) of goods sold in the window — what
    the business owes suppliers for the goods it moved this period. Scoped to one
    employee when `rep` is given. Always so'm (cost prices are stored in so'm).
    `date_from=None` drops the lower bound, giving the cumulative (as-of date_to)
    figure a standing balance needs."""
    items = SaleItem.objects.filter(sale__date__lte=date_to)
    returns = Return.objects.filter(sale__date__lte=date_to, restock=True)
    if date_from is not None:
        items = items.filter(sale__date__gte=date_from)
        returns = returns.filter(sale__date__gte=date_from)
    if rep is not None:
        items = items.filter(sale__sales_rep=rep)
        returns = returns.filter(sale__sales_rep=rep)
    sold = items.aggregate(s=Sum(COST))["s"] or Decimal("0")
    # Restocked goods are back in the warehouse, so their tannarx is no longer owed.
    # Written-off returns stay in the figure — see `seller_production_debt`.
    given_back = returns.aggregate(s=Sum(RETURN_COST))["s"] or Decimal("0")
    return sold - given_back


def _kassa_remitted(date_from, date_to, rep=None):
    """Total cash handed back to production (Ishlab chiqarishga topshirilgan) in the
    window. Scoped to one seller when `rep` is given. Always so'm. `date_from=None`
    drops the lower bound for the cumulative (as-of date_to) total."""
    qs = ProductionRemittance.objects.filter(date__lte=date_to)
    if date_from is not None:
        qs = qs.filter(date__gte=date_from)
    if rep is not None:
        qs = qs.filter(seller=rep)
    return qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")


def _kassa_paid_profit(date_from, date_to, rep=None):
    """Total profit handed up to the boss (Foyda topshirilgan) in the window. Scoped
    to one seller when `rep` is given. Always so'm. `date_from=None` drops the lower
    bound for the cumulative (as-of date_to) total."""
    qs = ProfitPayout.objects.filter(date__lte=date_to)
    if date_from is not None:
        qs = qs.filter(date__gte=date_from)
    if rep is not None:
        qs = qs.filter(seller=rep)
    return qs.aggregate(s=Sum("amount"))["s"] or Decimal("0")


def _realized_profit_by_seller(date_from, date_to, rep=None):
    """Cost-first realized profit per seller, for sales dated in the window.

    Profit is recognised only as money is collected, and a sale's collections cover
    its tannarx FIRST — only the surplus above cost counts as profit. So an unpaid
    debt sale earns nothing yet, and a part-paid one earns only what's collected
    beyond its cost:  realized = max(0, min(paid, revenue) − cost).

    Returns {seller_pk: realized_profit}. Everything is measured AFTER returns:
    net_revenue/net_cost_total drop the goods that came back, and net_paid drops money
    already handed back to the client, so an over-returned sale can't keep earning
    profit on cash it no longer holds.
    `date_from=None` drops the lower bound for the cumulative (as-of date_to) total."""
    sales = Sale.objects.filter(date__lte=date_to)
    if date_from is not None:
        sales = sales.filter(date__gte=date_from)
    if rep is not None:
        sales = sales.filter(sales_rep=rep)
    by_seller = {}
    for s in sales.with_balance().values(
        "sales_rep", "net_revenue", "net_cost_total", "net_paid"
    ):
        realized = max(
            Decimal("0"), min(s["net_paid"], s["net_revenue"]) - s["net_cost_total"]
        )
        by_seller[s["sales_rep"]] = by_seller.get(s["sales_rep"], Decimal("0")) + realized
    return by_seller


def _kassa_profit(date_from, date_to, rep=None):
    """Total realized (cost-first) profit in the window. Scoped to one seller when
    `rep` is given. See `_realized_profit_by_seller` for how it's recognised."""
    return sum(
        _realized_profit_by_seller(date_from, date_to, rep).values(), Decimal("0")
    )


def _kassa_summary(date_from, date_to, rep=None):
    """Two side-by-side till drawers — so'm and dollar — each with its income by
    method, expense and running balance, plus the period's supplier cost. Also the
    production-debt view: cash on hand, tannarx sold, remitted, and remaining debt.
    Scoped to one employee when `rep` is given."""
    # till_income() drops ADVANCE_USED: an advance's cash already counted as income
    # when it was deposited (ADVANCE_IN), so its consumption must not count again.
    payments = Payment.objects.till_income()
    expenses = Expense.objects.all()
    # Cash handed back to clients on over-returned sales. till_income() already drops
    # these, so they have to be brought in separately as an outflow.
    refunds = Payment.objects.filter(kind=Payment.Kind.REFUND_OUT)
    if rep is not None:
        payments = payments.filter(created_by=rep)
        expenses = expenses.filter(created_by=rep)
        refunds = refunds.filter(created_by=rep)
    uzs, usd = Payment.Currency.UZS, Payment.Currency.USD
    som = _currency_till(
        payments.filter(currency=uzs), expenses.filter(currency=uzs),
        refunds.filter(currency=uzs), date_from, date_to, som=True,
    )
    cost = _kassa_supplier_cost(date_from, date_to, rep)          # period flow
    remitted = _kassa_remitted(date_from, date_to, rep)           # period flow
    paid_profit = _kassa_paid_profit(date_from, date_to, rep)     # period flow
    profit = _kassa_profit(date_from, date_to, rep)
    # Every expense's so'm value, both currencies — the same figure the per-seller
    # rows sum, so the Jami row equals the sum of its columns.
    expense_total = (
        expenses.filter(date__gte=date_from, date__lte=date_to)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    # Standing balances (as of date_to). Cash on hand and production debt don't reset
    # with the day filter — they carry every movement up to the window's end, the way
    # the till's closing balance already does. Only date_to bounds them.
    cost_cum = _kassa_supplier_cost(None, date_to, rep)
    remitted_cum = _kassa_remitted(None, date_to, rep)
    paid_profit_cum = _kassa_paid_profit(None, date_to, rep)
    # Cash on hand combines every method AND currency: Payment.amount is always the
    # so'm value (a dollar payment is converted at entry), so PAYMENT_NET nets a
    # dollar payment to its so'm too — no currency filter here.
    income_all_cum = (
        payments.filter(date__lte=date_to)
        .aggregate(s=Sum(PAYMENT_NET))["s"] or Decimal("0")
    )
    expense_cum = (
        expenses.filter(date__lte=date_to).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    refund_cum = (
        refunds.filter(date__lte=date_to).aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    refunded = (
        refunds.filter(date__gte=date_from, date__lte=date_to)
        .aggregate(s=Sum("amount"))["s"] or Decimal("0")
    )
    cash_on_hand = income_all_cum - refund_cum - expense_cum - remitted_cum - paid_profit_cum
    return {
        "som": som,
        "usd": _currency_till(
            payments.filter(currency=usd), expenses.filter(currency=usd),
            refunds.filter(currency=usd), date_from, date_to, som=False,
        ),
        "cost": cost,
        # Production-debt block (so'm). Cash on hand = so'm income net of fees, less
        # every expense, less what's already been handed to production, less profit
        # already handed to the boss — all cumulative.
        "remitted": remitted,
        "paid_profit": paid_profit,
        "production_debt": cost_cum - remitted_cum,
        "cash": cash_on_hand,
        # Profit still sitting in the till, free to hand up: cash beyond the debt.
        "withdrawable_profit": cash_on_hand - (cost_cum - remitted_cum),
        "profit": profit,
        "expense_total": expense_total,
        "refunded": refunded,
        "net_profit": profit - expense_total,
    }


def _per_employee_kassa(date_from, date_to, rep=None):
    """Per-seller kassa control for the window. Each row carries: money taken in
    (so'm net / dollars), money paid out, the profit their sales earned, the tannarx
    of what they sold (= their production debt before handovers), how much they've
    handed back to production, and two derived figures —

      cash            = so'm income − expenses − remitted   (naqd qo'lida)
      production_debt = sold tannarx − remitted             (ishlab chiqarishga qarz)
      net             = realized profit − expenses          (samaradorlik)

    `profit` is realized cost-first (see `_realized_profit_by_seller`): an unpaid
    debt sale earns nothing until its tannarx is collected. Scoped to one seller
    when `rep` is given (a seller sees only their own row).

    This is a standing control snapshot: every column is cumulative as of date_to
    (the day filter's lower bound is dropped), so cash on hand and production debt
    read as the true outstanding totals and each row reconciles
    (debt = sold − remitted, cash = income − expenses − remitted)."""
    window = {"date__lte": date_to}
    sale_window = {"sale__date__lte": date_to}
    # Exclude ADVANCE_USED — already counted as income at deposit time (ADVANCE_IN).
    payments = Payment.objects.till_income().filter(**window)
    expenses = Expense.objects.filter(**window)
    sale_items = SaleItem.objects.filter(**sale_window)
    restocked = Return.objects.filter(restock=True, **sale_window)
    refunds = Payment.objects.filter(kind=Payment.Kind.REFUND_OUT, **window)
    remittances = ProductionRemittance.objects.filter(**window)
    payouts = ProfitPayout.objects.filter(**window)
    if rep is not None:
        payments = payments.filter(created_by=rep)
        expenses = expenses.filter(created_by=rep)
        sale_items = sale_items.filter(sale__sales_rep=rep)
        restocked = restocked.filter(sale__sales_rep=rep)
        refunds = refunds.filter(created_by=rep)
        remittances = remittances.filter(seller=rep)
        payouts = payouts.filter(seller=rep)

    users = {u.pk: u for u in User.objects.all()}
    usd = Payment.Currency.USD

    def blank(uid):
        return {
            "uid": uid,
            "employee": str(users.get(uid)) if users.get(uid) else "—",
            "in_som": Decimal("0"), "in_usd": Decimal("0"),
            "out_som": Decimal("0"), "out_usd": Decimal("0"),
            "expense_total": Decimal("0"), "profit": Decimal("0"),
            "sold_cost": Decimal("0"), "remitted": Decimal("0"),
            "paid_profit": Decimal("0"), "refunded": Decimal("0"),
        }

    rows = {}

    def row(uid):
        return rows.setdefault(uid, blank(uid))

    for r in (
        payments.values("created_by", "currency")
        .annotate(som=Sum(PAYMENT_NET), usd_amt=Sum("amount_original"))
    ):
        rr = row(r["created_by"])
        # Cash combines currencies: PAYMENT_NET is the so'm value of every payment,
        # so a dollar payment counts toward in_som at its so'm value too.
        rr["in_som"] += r["som"] or Decimal("0")
        if r["currency"] == usd:
            rr["in_usd"] += r["usd_amt"] or Decimal("0")

    for r in (
        expenses.values("created_by", "currency")
        .annotate(som=Sum("amount"), usd_amt=Sum("amount_original"))
    ):
        rr = row(r["created_by"])
        rr["expense_total"] += r["som"] or Decimal("0")  # so'm value of every expense
        if r["currency"] == usd:
            rr["out_usd"] += r["usd_amt"] or Decimal("0")
        else:
            rr["out_som"] += r["som"] or Decimal("0")

    for r in sale_items.values("sale__sales_rep").annotate(cost=Sum(COST)):
        row(r["sale__sales_rep"])["sold_cost"] += r["cost"] or Decimal("0")  # tannarx = debt

    # Restocked goods went back to the warehouse, so their tannarx stops being owed.
    for r in restocked.values("sale__sales_rep").annotate(cost=Sum(RETURN_COST)):
        row(r["sale__sales_rep"])["sold_cost"] -= r["cost"] or Decimal("0")

    # Profit is realized cost-first (only collections above a sale's tannarx count),
    # so it can't be a flat SaleItem sum — pull the per-seller figure instead.
    for uid, realized in _realized_profit_by_seller(None, date_to, rep).items():
        row(uid)["profit"] += realized

    for r in remittances.values("seller").annotate(s=Sum("amount")):
        row(r["seller"])["remitted"] += r["s"] or Decimal("0")

    for r in payouts.values("seller").annotate(s=Sum("amount")):
        row(r["seller"])["paid_profit"] += r["s"] or Decimal("0")

    for r in refunds.values("created_by").annotate(s=Sum("amount")):
        row(r["created_by"])["refunded"] += r["s"] or Decimal("0")

    result = []
    for rr in rows.values():
        # Cash left: income, less money refunded to clients, less expenses, less handed
        # to production, less profit handed to the boss.
        rr["cash"] = (
            rr["in_som"] - rr["refunded"] - rr["expense_total"]
            - rr["remitted"] - rr["paid_profit"]
        )
        rr["production_debt"] = rr["sold_cost"] - rr["remitted"]
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


def _kassa_transactions(expenses, dates, filters, rep):
    """A unified chronological ledger for the window: every incoming payment (kirim)
    and every expense (chiqim), newest first. Shares the drawer filters (xodim, usul,
    valyuta). A `category` filter is expense-only, so when one is active the incoming
    payments are omitted — the list then reads as a pure expense view."""
    rows = []
    # A category filter is expense-only; when one is active the incoming payments and
    # production handovers are omitted, so the list reads as a pure expense view.
    if filters["category"] not in dict(Expense.Category.choices):
        # till_income() drops ADVANCE_USED: it's an internal transfer of already-
        # counted advance money, not a new kirim, so it must not show as income here.
        payments = Payment.objects.till_income().select_related(
            "sale", "sale__client", "client", "created_by"
        ).filter(date__gte=dates["date_from"], date__lte=dates["date_to"])
        if rep is not None:
            payments = payments.filter(created_by=rep)
        if filters["method"] in dict(Payment.Method.choices):
            payments = payments.filter(method=filters["method"])
        if filters["currency"] in dict(Payment.Currency.choices):
            payments = payments.filter(currency=filters["currency"])
        for p in payments:
            # An advance deposit has no sale — its client lives on `client` instead.
            client = p.sale.client if p.sale else p.client
            rows.append({
                "date": p.date, "created_at": p.created_at, "direction": "in",
                "title": client.name if client else "—", "subtitle": p.get_kind_display(),
                "method": p.get_method_display(), "method_code": p.method,
                "currency": p.currency,
                "amount_som": p.net_amount, "amount_original": p.original_amount,
                "exchange_rate": p.exchange_rate, "commission_percent": p.commission_percent,
                "created_by": p.created_by,
                "sale_pk": p.sale_id, "pk": p.pk, "kind": "payment",
                "is_advance": p.kind == Payment.Kind.ADVANCE_IN,
            })
        # Production handovers — so'm only, so a dollar-currency filter hides them.
        if filters["currency"] != Payment.Currency.USD:
            remittances = ProductionRemittance.objects.select_related(
                "seller", "created_by"
            ).filter(date__gte=dates["date_from"], date__lte=dates["date_to"])
            if rep is not None:
                remittances = remittances.filter(seller=rep)
            if filters["method"] in dict(Payment.Method.choices):
                remittances = remittances.filter(method=filters["method"])
            for m in remittances:
                rows.append({
                    "date": m.date, "created_at": m.created_at, "direction": "remit",
                    "title": str(m.seller), "subtitle": "Ishlab chiqarishga topshiruv",
                    "method": m.get_method_display(), "method_code": m.method,
                    "currency": Payment.Currency.UZS,
                    "amount_som": m.amount, "amount_original": m.amount,
                    "exchange_rate": Decimal("0"), "created_by": m.created_by,
                    "pk": m.pk, "kind": "remittance",
                })
        # Profit handovers to the boss — so'm only, so a dollar-currency filter hides them.
        if filters["currency"] != Payment.Currency.USD:
            payouts = ProfitPayout.objects.select_related(
                "seller", "created_by"
            ).filter(date__gte=dates["date_from"], date__lte=dates["date_to"])
            if rep is not None:
                payouts = payouts.filter(seller=rep)
            if filters["method"] in dict(Payment.Method.choices):
                payouts = payouts.filter(method=filters["method"])
            for pp in payouts:
                rows.append({
                    "date": pp.date, "created_at": pp.created_at, "direction": "profit",
                    "title": str(pp.seller), "subtitle": "Foyda topshiruvi",
                    "method": pp.get_method_display(), "method_code": pp.method,
                    "currency": Payment.Currency.UZS,
                    "amount_som": pp.amount, "amount_original": pp.amount,
                    "exchange_rate": Decimal("0"), "created_by": pp.created_by,
                    "pk": pp.pk, "kind": "profit",
                })
    # Cash refunded to clients on over-returned sales. Not an expense (the business
    # bore no cost — it is the client's own money going back), but it leaves the till
    # all the same, so it belongs in the outflow ledger or the drawer won't reconcile.
    refunds = Payment.objects.filter(
        kind=Payment.Kind.REFUND_OUT,
        date__gte=dates["date_from"], date__lte=dates["date_to"],
    ).select_related("client", "created_by").prefetch_related("settled_return")
    if rep is not None:
        refunds = refunds.filter(created_by=rep)
    if filters["method"] in dict(Payment.Method.choices):
        refunds = refunds.filter(method=filters["method"])
    if filters["currency"] in dict(Payment.Currency.choices):
        refunds = refunds.filter(currency=filters["currency"])
    for rf in refunds:
        # A refund is only ever corrected by undoing its return, so carry the return's
        # pk onto the row — the kassa "Bekor qilish" action links straight to it.
        linked = list(rf.settled_return.all())
        rows.append({
            "date": rf.date, "created_at": rf.created_at, "direction": "refund",
            "title": str(rf.client) if rf.client else "—",
            "subtitle": rf.note or "Qaytarish uchun naqd berildi",
            "method": rf.get_method_display(), "method_code": rf.method,
            "currency": rf.currency,
            "amount_som": rf.amount, "amount_original": rf.original_amount,
            "exchange_rate": rf.exchange_rate, "created_by": rf.created_by,
            "pk": rf.pk, "kind": "refund", "sale_pk": rf.sale_id,
            "return_pk": linked[0].pk if linked else None,
        })
    for e in expenses:
        rows.append({
            "date": e.date, "created_at": e.created_at, "direction": "out",
            "title": e.get_category_display(), "subtitle": e.note,
            "method": e.get_method_display(), "method_code": e.method,
            "currency": e.currency,
            "amount_som": e.amount, "amount_original": e.original_amount,
            "exchange_rate": e.exchange_rate, "created_by": e.created_by,
            "pk": e.pk, "kind": "expense",
        })
    rows.sort(key=lambda r: (r["date"], r["created_at"]), reverse=True)
    return rows


def kassa_view(request):
    """The cash register (Kassa): two till drawers (so'm + dollar) with income by
    method and running balance, per-employee kassa & performance, and the expense
    list. Visible to everyone — the shared company till. Any filter (employee, turkum,
    usul, valyuta) scopes the figures so a supervisor can drill into one employee."""
    expenses, dates, filters, rep, reps = _kassa_expenses(request)
    date_from, date_to = dates["date_from"], dates["date_to"]
    summary = _kassa_summary(date_from, date_to, rep=rep)
    transactions = _kassa_transactions(expenses, dates, filters, rep)
    # Two side-by-side ledgers: kirim (client payments) on the left, chiqim
    # (expenses + production handovers) on the right. Totals use amount_som so a
    # USD payment counts at its so'm value. Newest-first order is inherited.
    income_rows = [t for t in transactions if t["direction"] == "in"]
    outflow_rows = [
        t for t in transactions if t["direction"] in ("out", "remit", "profit", "refund")
    ]
    income_total = sum((t["amount_som"] for t in income_rows), Decimal("0"))
    outflow_total = sum((t["amount_som"] for t in outflow_rows), Decimal("0"))

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
    # Per-seller control rows. Admins/managers see everyone (or one, if they filtered
    # by a rep); a seller sees only their own row.
    seller_rows = _per_employee_kassa(date_from, date_to, rep=rep)
    # Column-wise totals for the Jami row — sums the cumulative rows so the footer
    # always equals the sum of what's shown, whatever the filter.
    seller_totals = {
        key: sum((r[key] for r in seller_rows), Decimal("0"))
        for key in ("cash", "production_debt", "sold_cost", "remitted", "paid_profit",
                    "expense_total", "net")
    }
    my_row = None
    if not request.user.can_see_all_records:
        my_row = seller_rows[0] if seller_rows else None

    export_qs = request.GET.urlencode()
    return render(request, "crm/kassa.html", {
        "summary": summary,
        "income_rows": income_rows,
        "outflow_rows": outflow_rows,
        "income_total": income_total,
        "outflow_total": outflow_total,
        "expenses": expenses,
        "per_employee": seller_rows if request.user.can_see_all_records else None,
        "seller_totals": seller_totals,
        "my_row": my_row,
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
            e.date.strftime("%d.%m.%Y"),
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
                f"{expense.get_category_display()} chiqimi "
                f"({expense.get_method_display()}){usd} "
                f"— {expense.amount:,.0f} so'm",
            )
            messages.success(request, f"Chiqim qo'shildi: {expense.amount:,.0f} so'm.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_expense_modal.html")
    return form_response(request, form, title, modal_template="crm/_expense_modal.html")


def expense_edit(request, pk):
    """Fix a mistaken expense. Admins/managers may edit any; a seller may edit
    only expenses they entered themselves."""
    qs = Expense.objects.all() if request.user.can_see_all_records \
        else Expense.objects.filter(created_by=request.user)
    expense = get_object_or_404(qs, pk=pk)
    title = "Chiqimni tahrirlash"
    form = ExpenseForm(request.POST or None, instance=expense)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Chiqim", expense.pk,
                f"{expense.get_category_display()} chiqimi — {expense.amount:,.0f} so'm",
            )
            messages.success(request, "Chiqim yangilandi.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_expense_modal.html")
    return form_response(request, form, title, modal_template="crm/_expense_modal.html")


def expense_delete(request, pk):
    """Remove a mistaken expense. Admins/managers may erase any; a seller may
    erase only expenses they entered themselves."""
    qs = Expense.objects.select_related("created_by")
    if not request.user.can_see_all_records:
        qs = qs.filter(created_by=request.user)
    expense = get_object_or_404(qs, pk=pk)
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


def _remit_summary(remit):
    return (
        f"Sotuvchi {remit.seller} topshirdi "
        f"({remit.get_method_display()}) — {remit.amount:,.0f} so'm"
    )


def remittance_create(request):
    """Record cash a seller hands back to production. A seller may only file their
    own; admins/managers may file on behalf of any seller (and can preselect one via
    ?seller= from the per-seller control table)."""
    initial = {}
    seller_pk = request.GET.get("seller", "")
    if request.method == "GET" and request.user.can_see_all_records and seller_pk.isdigit():
        initial["seller"] = seller_pk
    form = ProductionRemittanceForm(request.POST or None, user=request.user, initial=initial)
    title = "Ishlab chiqarishga topshirish"
    if request.method == "POST":
        if form.is_valid():
            remit = form.save(commit=False)
            # A seller cannot spoof the seller field (it's disabled, so absent from
            # POST) — pin it to themselves.
            if not request.user.can_see_all_records:
                remit.seller = request.user
            remit.created_by = request.user
            remit.save()
            AuditLog.record(
                request.user, AuditLog.Action.CREATE, "Topshiruv", remit.pk,
                _remit_summary(remit),
            )
            messages.success(request, f"Topshirildi: {remit.amount:,.0f} so'm.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_remittance_modal.html")
    return form_response(request, form, title, modal_template="crm/_remittance_modal.html")


def remittance_edit(request, pk):
    """Fix a mistaken handover. Admins/managers may edit any; a seller may edit only
    their own."""
    qs = ProductionRemittance.objects.all() if request.user.can_see_all_records \
        else ProductionRemittance.objects.filter(seller=request.user)
    remit = get_object_or_404(qs, pk=pk)
    title = "Topshiruvni tahrirlash"
    form = ProductionRemittanceForm(request.POST or None, instance=remit, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            remit = form.save(commit=False)
            if not request.user.can_see_all_records:
                remit.seller = request.user
            remit.save()
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Topshiruv", remit.pk,
                _remit_summary(remit),
            )
            messages.success(request, "Topshiruv yangilandi.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_remittance_modal.html")
    return form_response(request, form, title, modal_template="crm/_remittance_modal.html")


def remittance_delete(request, pk):
    """Remove a mistaken handover. Admins/managers may erase any; a seller may erase
    only their own."""
    qs = ProductionRemittance.objects.select_related("seller", "created_by")
    if not request.user.can_see_all_records:
        qs = qs.filter(seller=request.user)
    remit = get_object_or_404(qs, pk=pk)
    if request.method == "POST":
        summary = _remit_summary(remit)
        remit.delete()
        AuditLog.record(request.user, AuditLog.Action.DELETE, "Topshiruv", pk, summary)
        messages.success(request, "Topshiruv o'chirildi.")
        return form_reload(request, reverse("kassa"))
    return render_confirm(
        request,
        "Topshiruvni o'chirish",
        f"{remit.amount:,.0f} so'm topshiruv o'chiriladi. Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


def _profit_summary(payout):
    return (
        f"Sotuvchi {payout.seller} foyda topshirdi "
        f"({payout.get_method_display()}) — {payout.amount:,.0f} so'm"
    )


def profit_payout_create(request):
    """Record realized profit a seller hands up to the boss. A seller may file only
    their own; admins/managers may file for any seller (and can preselect one via
    ?seller= from the per-seller control table)."""
    initial = {}
    seller_pk = request.GET.get("seller", "")
    if request.method == "GET" and request.user.can_see_all_records and seller_pk.isdigit():
        initial["seller"] = seller_pk
    form = ProfitPayoutForm(request.POST or None, user=request.user, initial=initial)
    title = "Foyda topshirish"
    if request.method == "POST":
        if form.is_valid():
            payout = form.save(commit=False)
            if not request.user.can_see_all_records:
                payout.seller = request.user
            payout.created_by = request.user
            payout.save()
            AuditLog.record(
                request.user, AuditLog.Action.CREATE, "Foyda", payout.pk,
                _profit_summary(payout),
            )
            messages.success(request, f"Foyda topshirildi: {payout.amount:,.0f} so'm.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_profit_payout_modal.html")
    return form_response(request, form, title, modal_template="crm/_profit_payout_modal.html")


def profit_payout_edit(request, pk):
    """Fix a mistaken profit handover. Admins/managers may edit any; a seller may edit
    only their own."""
    qs = ProfitPayout.objects.all() if request.user.can_see_all_records \
        else ProfitPayout.objects.filter(seller=request.user)
    payout = get_object_or_404(qs, pk=pk)
    title = "Foyda topshiruvini tahrirlash"
    form = ProfitPayoutForm(request.POST or None, instance=payout, user=request.user)
    if request.method == "POST":
        if form.is_valid():
            payout = form.save(commit=False)
            if not request.user.can_see_all_records:
                payout.seller = request.user
            payout.save()
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Foyda", payout.pk,
                _profit_summary(payout),
            )
            messages.success(request, "Foyda topshiruvi yangilandi.")
            return form_success(request, reverse("kassa"))
        return form_response(request, form, title, invalid=True, modal_template="crm/_profit_payout_modal.html")
    return form_response(request, form, title, modal_template="crm/_profit_payout_modal.html")


def profit_payout_delete(request, pk):
    """Remove a mistaken profit handover. Admins/managers may erase any; a seller may
    erase only their own."""
    qs = ProfitPayout.objects.select_related("seller", "created_by")
    if not request.user.can_see_all_records:
        qs = qs.filter(seller=request.user)
    payout = get_object_or_404(qs, pk=pk)
    if request.method == "POST":
        summary = _profit_summary(payout)
        payout.delete()
        AuditLog.record(request.user, AuditLog.Action.DELETE, "Foyda", pk, summary)
        messages.success(request, "Foyda topshiruvi o'chirildi.")
        return form_reload(request, reverse("kassa"))
    return render_confirm(
        request,
        "Foyda topshiruvini o'chirish",
        f"{payout.amount:,.0f} so'm foyda topshiruvi o'chiriladi. Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


def _receipt_summary(receipt):
    lines = list(receipt.items.select_related("product"))
    total = sum((it.quantity_kg for it in lines), Decimal("0"))
    return f"Sotuvchi {receipt.seller}: {len(lines)} ta mahsulot, {_kg(total)} kg"


def _pending_zakaz_for_receipt(receipt):
    """Pending zakaz lines the receipt's stock could fulfil: the seller's own
    unfulfilled sale lines for any product on the receipt."""
    product_ids = list(receipt.items.values_list("product_id", flat=True))
    return (
        SaleItem.objects.filter(
            sale__sales_rep=receipt.seller,
            product_id__in=product_ids,
            fulfilled_at__isnull=True,
        )
        .select_related("sale", "sale__client", "product")
        .order_by("sale__date")
    )


def _auto_bind_receipt(receipt):
    """Automatically fulfil the seller's oldest pending zakaz for each received
    product, up to the quantity received. Strict FIFO and whole-order: the oldest
    unfilled order that the remaining stock can't cover stops that product's binding
    (it waits for more stock). Returns the number of orders bound."""
    bound = 0
    for ri in receipt.items.select_related("product"):
        remaining = ri.quantity_kg
        pending = (
            SaleItem.objects.filter(
                sale__sales_rep=receipt.seller,
                product=ri.product,
                fulfilled_at__isnull=True,
            )
            .select_related("sale")
            .order_by("sale__date", "pk")
        )
        for item in pending:
            need = item.weight_kg - item.fulfilled_kg
            if need <= 0:
                continue
            fill = min(need, remaining)
            item.fulfilled_kg += fill
            fields = ["fulfilled_kg"]
            if item.fulfilled_kg >= item.weight_kg:  # this order is now complete
                item.fulfilled_at = receipt.date
                item.fulfilled_by_receipt = receipt
                fields += ["fulfilled_at", "fulfilled_by_receipt"]
            item.save(update_fields=fields)
            remaining -= fill
            bound += 1
            if remaining <= 0:
                break
    return bound


def _render_receipt_form(request, form, formset, title, invalid=False):
    context = {"form": form, "formset": formset, "title": title}
    if is_ajax(request):
        return render(request, "crm/_receipt_modal.html", context, status=422 if invalid else 200)
    return render(request, "crm/receipt_form.html", context)


def receipt_create(request):
    """Log goods a seller received from production into their ombor. A seller files
    only their own; admins/managers may file for any seller (preselect via ?seller=)."""
    initial = {}
    seller_pk = request.GET.get("seller", "")
    if request.method == "GET" and request.user.can_see_all_records and seller_pk.isdigit():
        initial["seller"] = seller_pk
    form = ProductionReceiptForm(request.POST or None, user=request.user, initial=initial)
    formset = ProductionReceiptItemFormSet(
        request.POST or None, instance=ProductionReceipt(), prefix="items"
    )
    title = "Ishlab chiqarishdan qabul"
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            receipt = form.save(commit=False)
            if not request.user.can_see_all_records:
                receipt.seller = request.user
            receipt.created_by = request.user
            receipt.save()
            formset.instance = receipt
            formset.save()
            AuditLog.record(
                request.user, AuditLog.Action.CREATE, "Qabul", receipt.pk,
                _receipt_summary(receipt),
            )
            messages.success(request, "Qabul qilingan tovarlar qo'shildi.")
            # Automatically assign the arriving stock to waiting zakaz orders.
            bound = _auto_bind_receipt(receipt)
            if bound:
                AuditLog.record(
                    request.user, AuditLog.Action.UPDATE, "Zakaz", receipt.pk,
                    f"{bound} ta zakaz avtomatik biriktirildi",
                )
                messages.success(request, f"{bound} ta zakaz mijozga avtomatik biriktirildi.")
            return form_success(request, reverse("ombor"))
        return _render_receipt_form(request, form, formset, title, invalid=True)
    return _render_receipt_form(request, form, formset, title)


def receipt_bind(request, pk):
    """Bind a receipt's arriving stock to pending zakaz orders for the same
    products, marking those orders fulfilled (ready to hand over)."""
    qs = ProductionReceipt.objects.all() if request.user.can_see_all_records \
        else ProductionReceipt.objects.filter(seller=request.user)
    receipt = get_object_or_404(qs, pk=pk)
    pending = _pending_zakaz_for_receipt(receipt)
    if request.method == "POST":
        ids = request.POST.getlist("bind")
        n = pending.filter(pk__in=ids).update(
            fulfilled_at=receipt.date, fulfilled_by_receipt=receipt
        )
        if n:
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Zakaz", receipt.pk,
                f"{n} ta zakaz mijozga biriktirildi",
            )
            messages.success(request, f"{n} ta zakaz biriktirildi.")
        return redirect(reverse("ombor"))
    return render(request, "crm/receipt_bind.html", {"receipt": receipt, "pending": pending})


def receipt_edit(request, pk):
    """Fix a receipt. Admins/managers may edit any; a seller only their own."""
    qs = ProductionReceipt.objects.all() if request.user.can_see_all_records \
        else ProductionReceipt.objects.filter(seller=request.user)
    receipt = get_object_or_404(qs, pk=pk)
    form = ProductionReceiptForm(request.POST or None, instance=receipt, user=request.user)
    formset = ProductionReceiptItemFormSet(request.POST or None, instance=receipt, prefix="items")
    title = "Qabulni tahrirlash"
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            receipt = form.save(commit=False)
            if not request.user.can_see_all_records:
                receipt.seller = request.user
            receipt.save()
            formset.save()
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Qabul", receipt.pk,
                _receipt_summary(receipt),
            )
            messages.success(request, "Qabul yangilandi.")
            return form_reload(request, reverse("ombor"))
        return _render_receipt_form(request, form, formset, title, invalid=True)
    return _render_receipt_form(request, form, formset, title)


def receipt_delete(request, pk):
    """Remove a receipt. Admins/managers may erase any; a seller only their own."""
    qs = ProductionReceipt.objects.select_related("seller")
    if not request.user.can_see_all_records:
        qs = qs.filter(seller=request.user)
    receipt = get_object_or_404(qs, pk=pk)
    if request.method == "POST":
        summary = _receipt_summary(receipt)
        receipt.delete()
        AuditLog.record(request.user, AuditLog.Action.DELETE, "Qabul", pk, summary)
        messages.success(request, "Qabul o'chirildi.")
        return form_reload(request, reverse("ombor"))
    return render_confirm(
        request,
        "Qabulni o'chirish",
        "Bu qabul o'chiriladi va sotuvchi ombori shunga mos kamayadi. Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )


def _ombor_items(request, date_from, date_to):
    """Sale lines inside the window, scoped to the viewer and narrowed by the ombor
    filters (product search + seller). Shared by the page and its Excel export so
    the download always matches what is on screen.
    Returns (items, filters, reps, rep_obj)."""
    user = request.user
    filters = {"q": request.GET.get("q", "").strip(), "rep": request.GET.get("rep", "")}
    filters["dan"] = date_from.isoformat()
    filters["gacha"] = date_to.isoformat()

    items = SaleItem.objects.filter(sale__date__gte=date_from, sale__date__lte=date_to)
    if not user.can_see_all_records:
        items = items.filter(sale__sales_rep=user)

    reps = rep_obj = None
    if user.can_see_all_records:
        reps = User.objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
        if filters["rep"].isdigit():
            rep_obj = reps.filter(pk=filters["rep"]).first()
            if rep_obj:
                items = items.filter(sale__sales_rep=rep_obj)

    if filters["q"]:
        items = items.filter(
            Q(product__name__icontains=filters["q"]) | Q(product__sku__icontains=filters["q"])
        )
    return items, filters, reps, rep_obj


def _ombor_rows(items):
    """One row per product: kg sold, and how many receipts it appeared on."""
    return list(
        items.values("product", "product__name", "product__sku")
        .annotate(total_kg=Sum(ITEM_WEIGHT_KG), sales_count=Count("sale", distinct=True))
        .order_by("-total_kg")
    )


def ombor_view(request):
    """Ombor = sold-goods report, one row per product with the total kg sold in the
    selected date window. A seller sees only their own sales; admins/managers see
    every seller's combined total (and can filter to one seller). Click a product to
    drill into who bought it. Mirrors the debts page's group-then-detail shape."""
    dates = _date_range_context(request)
    date_from, date_to = dates["date_from"], dates["date_to"]

    items, filters, reps, rep_obj = _ombor_items(request, date_from, date_to)
    rows = _ombor_rows(items)
    total_kg = sum((r["total_kg"] or Decimal("0") for r in rows), Decimal("0"))

    active_filters = _filter_chips(request, [
        {"param": "rep", "label": "Sotuvchi", "value": str(rep_obj) if rep_obj else ""},
    ])
    # Carry the current window and filters into the download link, so the .xlsx is
    # exactly the table on screen (the month a sverka is being done for).
    query = request.GET.urlencode()

    return render(request, "crm/ombor.html", {
        "rows": rows,
        "total_kg": total_kg,
        "product_count": len(rows),
        "q": filters["q"],
        "filters": filters,
        "reps": reps,
        "rep_label": "Sotuvchi",
        "is_admin_view": request.user.can_see_all_records,
        "active_filters": active_filters,
        "filter_count": len(active_filters),
        "has_filters": bool(active_filters),
        "filter_url": reverse("ombor"),
        "catalog_url": reverse("product_list"),
        "export_url": reverse("ombor_export") + (f"?{query}" if query else ""),
        "search_placeholder": "Mahsulot nomi…",
        "show_daterange_picker": True,
        "keep_daterange": True,
        **dates,
    })


def ombor_export(request):
    """The sold-goods report as .xlsx — same window, search and seller filter as the
    page. This is the sheet a monthly production-vs-sold sverka is built from."""
    dates = _date_range_context(request)
    items, _, _, _ = _ombor_items(request, dates["date_from"], dates["date_to"])
    rows = _ombor_rows(items)

    headers = ["Mahsulot", "SKU", "Sotuvlar soni", "Sotilgan (kg)"]
    data = [
        [
            r["product__name"],
            r["product__sku"],
            r["sales_count"],
            float(r["total_kg"] or 0),
        ]
        for r in rows
    ]
    return _xlsx_response("ombor.xlsx", "Ombor", headers, data, {4: "0.000"})


def _filter_ombor_items(request, items):
    """Narrow one product's sale lines by client / seller / date, so a particular chek
    can be found in a long history.

    Unlike the sales list there is NO default date window: this page IS the product's
    full history, so dates only bite once the user actually sets them. Returns
    (queryset, filters, has_filters)."""
    filters = {key: request.GET.get(key, "") for key in ("client", "rep", "dan", "gacha")}
    filters["q"] = request.GET.get("q", "").strip()
    can_scope_rep = request.user.can_see_all_records

    if filters["q"]:
        items = items.filter(_client_search_q(filters["q"], "sale__client"))
    if filters["client"].isdigit():
        items = items.filter(sale__client_id=filters["client"])
    if filters["rep"].isdigit() and can_scope_rep:
        items = items.filter(sale__sales_rep_id=filters["rep"])

    date_from = _parse_date(filters["dan"])
    date_to = _parse_date(filters["gacha"])
    if date_from and date_to and date_to < date_from:
        date_from, date_to = date_to, date_from
        filters["dan"], filters["gacha"] = date_from.isoformat(), date_to.isoformat()
    if date_from:
        items = items.filter(sale__date__gte=date_from)
    if date_to:
        items = items.filter(sale__date__lte=date_to)

    has_filters = bool(
        filters["q"]
        or filters["client"].isdigit()
        or (filters["rep"].isdigit() and can_scope_rep)
        or date_from
        or date_to
    )
    return items, filters, has_filters


def ombor_product(request, pk):
    """Drill-down for one product: every sale of it, newest first, filterable by
    client / seller / date so one chek can be tracked down. A seller sees only their
    own sales; an admin also gets a per-seller summary (which seller sold how much)
    above the transaction list."""
    product = get_object_or_404(Product, pk=pk)
    user = request.user
    qs = SaleItem.objects.filter(product=product).select_related(
        "sale", "sale__client", "sale__sales_rep"
    )
    if not user.can_see_all_records:
        qs = qs.filter(sale__sales_rep=user)
    # Dropdown options come from this product's own history — offering clients who
    # never bought it would just be noise.
    scoped = qs
    clients = Client.objects.filter(sales__items__in=scoped).distinct().order_by("name")
    reps = (
        User.objects.filter(sales__items__in=scoped).distinct().order_by("first_name", "username")
        if user.can_see_all_records
        else None
    )
    qs, filters, has_filters = _filter_ombor_items(request, qs)
    items = list(qs)
    items.sort(key=lambda it: (it.sale.date, it.sale.created_at), reverse=True)
    total_kg = sum((it.weight_kg for it in items), Decimal("0"))

    by_seller = None
    if user.can_see_all_records:
        acc = {}
        for it in items:
            rep = it.sale.sales_rep
            row = acc.setdefault(rep.pk, {"seller": rep, "kg": Decimal("0"), "count": 0})
            row["kg"] += it.weight_kg
            row["count"] += 1
        by_seller = sorted(acc.values(), key=lambda r: r["kg"], reverse=True)

    chip_client = clients.filter(pk=filters["client"]).first() if filters["client"].isdigit() else None
    chip_rep = reps.filter(pk=filters["rep"]).first() if reps and filters["rep"].isdigit() else None
    active_filters = _filter_chips(request, [
        {"param": "client", "label": "Mijoz", "value": chip_client.name if chip_client else ""},
        {"param": "rep", "label": "Sotuvchi", "value": str(chip_rep) if chip_rep else ""},
        {"param": "dan", "label": "Sanadan", "value": filters["dan"]},
        {"param": "gacha", "label": "Sanagacha", "value": filters["gacha"]},
    ])
    return render(request, "crm/ombor_product.html", {
        "product": product,
        "items": items,
        "total_kg": total_kg,
        "by_seller": by_seller,
        "is_admin_view": user.can_see_all_records,
        "filters": filters,
        "has_filters": has_filters,
        "active_filters": active_filters,
        "filter_count": len(active_filters),
        "clients": clients,
        "reps": reps,
        "filter_url": reverse("ombor_product", args=[product.pk]),
    })


def sale_export(request):
    base = (
        Sale.objects.visible_to(request.user)
        .real()  # matches the sales list — opening carry-overs are not sales
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
        deadline = s.debt_deadline.strftime("%d.%m.%Y") if s.debt_deadline else ""
        status = "Qarz" if s.remaining > 0 else "To'langan"
        for item in s.items.all():
            rows.append([
                s.date.strftime("%d.%m.%Y"),
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
    rows = sale.payments.select_related("created_by").order_by("-date", "-created_at")
    # Money the client put IN and money handed back to them are two different stories,
    # and mixing them would make the payments table stop adding up to `paid`.
    payments = [p for p in rows if p.kind in PAYING_KINDS]
    settlements = [p for p in rows if p.kind not in PAYING_KINDS]
    returns = sale.returns.select_related("product", "created_by")
    return render(
        request,
        "crm/sale_detail.html",
        {
            "sale": sale,
            "items": sale.items.all(),
            "payments": payments,
            "settlements": settlements,
            "returns": returns,
            "returned": sale.returned_amount,
            "settled": sale.settled_amount,
            "paid": sale.paid_amount,
            "remaining": sale.debt_remaining,
        },
    )


def _render_sale_form(request, form, formset, title, invalid=False, zakaz_shortfall=None):
    context = {
        "form": form,
        "formset": formset,
        "title": title,
        "products_json": _product_price_map(),
        "client_advance_json": _client_advance_map(request.user),
        "zakaz_shortfall": zakaz_shortfall,
    }
    keep_open = invalid or bool(zakaz_shortfall)
    if is_ajax(request):
        return render(request, "crm/_sale_modal.html", context, status=422 if keep_open else 200)
    return render(request, "crm/sale_form.html", context)


def _client_advance_map(user):
    """{client_pk: balance} for clients this user (as seller) is holding an advance
    for — so the sale form can flag "this client has X prepaid" the moment they're
    picked. Scoped to `user` because that's the seller whose advance a new sale would
    actually consume. Only positive balances are included."""
    rows = (
        Payment.objects.filter(
            created_by=user,
            kind__in=[Payment.Kind.ADVANCE_IN, Payment.Kind.ADVANCE_USED],
        )
        .values("client")
        .annotate(
            deposited=Sum(PAYMENT_NET, filter=Q(kind=Payment.Kind.ADVANCE_IN)),
            used=Sum(PAYMENT_NET, filter=Q(kind=Payment.Kind.ADVANCE_USED)),
        )
    )
    result = {}
    for r in rows:
        balance = (r["deposited"] or Decimal("0")) - (r["used"] or Decimal("0"))
        if r["client"] and balance > 0:
            result[str(r["client"])] = float(balance)
    return result


def _product_price_map():
    """Per-kg price/cost for each active product, so the form can auto-fill a row —
    plus whether the product offers the Razmer / Mikron dropdowns, so the JS can show
    or hide them when the product is picked."""
    return {
        str(p.pk): {
            "price": str(p.price),
            "cost": str(p.cost_price),
            "has_size": p.has_size,
            "has_micron": p.has_micron,
        }
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
            # No warehouse stock to check against — every line is fulfilled on sale.
            _mark_fulfilment(sale, [])
            # If the client has prepaid this seller, spend that advance on the new
            # receipt (oldest first) — the sale opens already part/fully paid.
            applied = _apply_advance_to_open_sales(sale.client, request.user)
            AuditLog.record(
                request.user, AuditLog.Action.CREATE, "Sotuv", sale.pk,
                f"Mijoz {sale.client.name}, {sale.items.count()} ta mahsulot "
                f"— {sale.total_price:,.0f} so'm",
            )
            if applied > 0:
                messages.success(
                    request,
                    f"Sotuv qo'shildi. Avansdan {applied:,.0f} so'm yechildi.",
                )
            else:
                messages.success(request, "Sotuv qo'shildi (qarz sifatida).")
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


def _return_conflict(formset):
    """Reject edits that would strand a return.

    `Return.sale_item` cascades, so deleting a line that has returns would silently
    take the returns with it while the settlement payment stayed behind — the client's
    credit would survive with nothing backing it. Shrinking a line below what has
    already come back is the same problem in miniature. Returns the error message, or
    None when the edit is safe."""
    for f in formset.forms:
        cleaned = getattr(f, "cleaned_data", None)
        item = cleaned.get("id") if cleaned else None
        if not cleaned or item is None or item.pk is None:
            continue
        returned = sum((r.weight for r in item.returns.all()), Decimal("0"))
        if not returned:
            continue
        name = item.product.name
        if cleaned.get("DELETE"):
            return (
                f"«{name}» qatorini o'chirib bo'lmaydi — undan {returned:g} "
                f"{item.dimension} qaytarilgan. Avval qaytarishni bekor qiling."
            )
        weight = cleaned.get("weight")
        if weight is not None and weight < returned:
            return (
                f"«{name}» miqdorini {weight:g} ga tushirib bo'lmaydi — undan allaqachon "
                f"{returned:g} {item.dimension} qaytarilgan."
            )
    return None


def sale_edit(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    form = SaleForm(request.POST or None, instance=sale, user=request.user)
    formset = SaleItemFormSet(request.POST or None, instance=sale, prefix="items")
    if request.method == "POST":
        if form.is_valid() and formset.is_valid():
            conflict = _return_conflict(formset)
            if conflict:
                form.add_error(None, conflict)
                return _render_sale_form(
                    request, form, formset, "Sotuvni tahrirlash", invalid=True
                )
            # An edit must not drop the total below what the client has effectively
            # paid and still holds goods for, or the sale would read as over-paid (a
            # negative balance) with no refund. Returned goods, and money already
            # handed back for them, are both out of that comparison.
            net_paid = sale.paid_amount - sale.settled_amount
            returned = sale.returned_amount
            new_total = _formset_total(formset)
            if new_total - returned < net_paid:
                form.add_error(
                    None,
                    f"Jami summa ({new_total:,.0f} so'm) juda kam: qaytarilgan tovar "
                    f"({returned:,.0f} so'm) hisobga olinganda ham mijoz "
                    f"{net_paid:,.0f} so'm to'lagan.",
                )
                return _render_sale_form(request, form, formset, "Sotuvni tahrirlash", invalid=True)
            sale = form.save()
            formset.save()
            _mark_fulfilment(sale, [], only_unset=True)
            AuditLog.record(
                request.user, AuditLog.Action.UPDATE, "Sotuv", sale.pk,
                f"Mijoz {sale.client.name}, {sale.items.count()} ta mahsulot "
                f"— {sale.total_price:,.0f} so'm",
            )
            messages.success(request, "Sotuv yangilandi.")
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
                f"Mijoz {sale.client.name} to'liq to'ladi (Naqd) — {remaining:,.0f} so'm",
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
                f"Mijoz {sale.client.name} to'lovi "
                f"({_method_label(form.cleaned_data['method'])}){_usd_note(form.cleaned_data)} "
                f"— {form.cleaned_data['amount']:,.0f} so'm",
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


def _render_return_form(request, sale, form, invalid=False, title=None):
    open_debt = max(Decimal("0"), sale.debt_remaining)
    net_paid = sale.paid_amount - sale.settled_amount
    context = {
        "form": form,
        "sale": sale,
        "title": title or f"Qaytarish: {sale.client.name}",
        # The seller's first question is "has this client paid yet?", because that is
        # what decides whether goods coming back cost the till anything.
        "open_debt": open_debt,
        "net_paid": net_paid,
        "is_unpaid": open_debt > 0 and net_paid <= 0,
        "is_partly_paid": open_debt > 0 and net_paid > 0,
        "is_settled": open_debt <= 0,
        # False when no return, however large, could exceed the debt — the settlement
        # choice is then dropped from the form (see ReturnForm.can_overpay).
        "can_overpay": ReturnForm.can_overpay(sale),
        "cash_on_hand": seller_cash_on_hand(request.user),
    }
    if is_ajax(request):
        return render(request, "crm/_return_modal.html", context, status=422 if invalid else 200)
    return render(request, "crm/_return_page.html", context)


@transaction.atomic
def sale_return(request, pk):
    """Take goods back on a sale and settle the money in one step.

    A return cancels the sale's open debt first. Anything beyond that is value the
    client had already paid for, so it is handed back — either parked as advance
    credit (which then flows onto their other open receipts) or paid out in cash.
    Without that settlement the receipt would sit at a permanent negative balance and
    the money owed to the client would be invisible."""
    sale = get_object_or_404(
        Sale.objects.visible_to(request.user).prefetch_related("items__product", "returns"),
        pk=pk,
    )
    if request.method == "POST":
        form = ReturnForm(request.POST, sale=sale, user=request.user)
        if form.is_valid():
            ret = form.save(commit=False)
            ret.created_by = request.user
            ret.save()
            to_debt, excess, refunded = _settle_return(ret, form, request.user)
            AuditLog.record(
                request.user, AuditLog.Action.RETURN, "Qaytarish", sale.pk,
                f"Mijoz {sale.client.name} qaytardi ({ret.product.name}) — "
                f"{ret.amount:,.0f} so'm; qarzdan {to_debt:,.0f}, "
                f"ortiqcha {excess:,.0f} "
                f"({'naqd berildi' if refunded else 'avansga'})",
            )
            messages.success(request, _return_message(ret.amount, to_debt, excess, refunded))
            return form_reload(request, reverse("sale_detail", args=[sale.pk]))
        return _render_return_form(request, sale, form, invalid=True)
    form = ReturnForm(sale=sale, user=request.user, initial={"restock": True})
    return _render_return_form(request, sale, form)


def _settle_return(ret, form, user):
    """Post the money side of a just-saved return and link it back to the return.

    The return's value cancels open debt first; any excess is money the client had
    already paid and is owed back — parked as advance credit (default) or handed out
    as cash. Kept in one place so `sale_return` and `return_edit` settle identically.
    Returns (to_debt, excess, refunded)."""
    excess = form.excess
    to_debt = form.credited_to_debt
    refunded = form.cleaned_data.get("settlement") == ReturnForm.SETTLE_REFUND
    if excess > 0:
        settlement = Payment.objects.create(
            sale=ret.sale,
            client=ret.sale.client,
            date=ret.date,
            amount=excess,
            method=Payment.Method.CASH,
            kind=(
                Payment.Kind.REFUND_OUT if refunded
                else Payment.Kind.RETURN_CREDIT
            ),
            note=f"Qaytarish: {ret.product.name}",
            created_by=user,
        )
        # Link the payment back to the return so it can be voided/edited as one unit.
        ret.settlement = settlement
        ret.save(update_fields=["settlement"])
        if not refunded:
            # Spend the fresh credit on whatever else the client still owes.
            _apply_advance_to_open_sales(ret.sale.client, user)
    return to_debt, excess, refunded


def _reverse_return(ret):
    """Roll back a return's money side and delete the return itself. The sale's debt,
    the warehouse figures and every till total re-derive to their pre-return state; a
    spent advance credit is peeled back so the pool can't go negative. Shared by
    `return_delete` (final) and `return_edit` (before re-applying the new values)."""
    settlement = ret.settlement
    is_credit = settlement is not None and settlement.kind == Payment.Kind.RETURN_CREDIT
    client = ret.sale.client
    seller = settlement.created_by if settlement else None
    if settlement is not None:
        settlement.delete()
    ret.delete()
    if is_credit and seller is not None:
        _reconcile_client_advance(client, seller)


def return_edit(request, pk):
    """Correct a mistaken return — change the line, quantity, restock flag or how the
    excess was settled. The old return is rolled back in full and the new values are
    applied as a fresh return, so debt, till and advance stay perfectly in sync (a
    return can't be safely edited in place — its settlement is derived from it). A
    seller may edit only their own returns; admins/managers any."""
    qs = Return.objects.select_related(
        "sale", "sale__client", "product", "sale_item", "settlement"
    )
    if not request.user.can_see_all_records:
        qs = qs.filter(created_by=request.user)
    ret = get_object_or_404(qs, pk=pk)
    sale = ret.sale
    acceptor, orig_date = ret.created_by, ret.date
    title = "Qaytarishni tahrirlash"
    if request.method == "POST":
        with transaction.atomic():
            _reverse_return(ret)
            # Validate against the restored state, so the quantity cap and the debt
            # split both see the sale as if this return had never happened.
            sale.refresh_from_db()
            form = ReturnForm(request.POST, sale=sale, user=request.user)
            if form.is_valid():
                new = form.save(commit=False)
                new.created_by = acceptor
                new.date = orig_date
                new.save()
                to_debt, excess, refunded = _settle_return(new, form, request.user)
                AuditLog.record(
                    request.user, AuditLog.Action.UPDATE, "Qaytarish", sale.pk,
                    f"Mijoz {sale.client.name} qaytarishi o'zgartirildi "
                    f"({new.product.name}) — {new.amount:,.0f} so'm",
                )
                messages.success(request, "Qaytarish yangilandi.")
                return form_reload(request, reverse("sale_detail", args=[sale.pk]))
            # Invalid: undo the tentative reversal, leaving the return untouched.
            transaction.set_rollback(True)
        return _render_return_form(request, sale, form, invalid=True, title=title)
    settlement_initial = (
        ReturnForm.SETTLE_REFUND
        if ret.settlement and ret.settlement.kind == Payment.Kind.REFUND_OUT
        else ReturnForm.SETTLE_ADVANCE
    )
    form = ReturnForm(
        sale=sale, user=request.user,
        initial={
            "sale_item": ret.sale_item_id,
            "weight": ret.weight,
            "restock": ret.restock,
            "note": ret.note,
            "settlement": settlement_initial,
        },
    )
    return _render_return_form(request, sale, form, title=title)


def return_delete(request, pk):
    """Undo a return in full. Voids the settlement it generated (the cash refund or
    the advance credit) and removes the return itself, so the sale's open debt, the
    warehouse figures and every till total re-derive to exactly their pre-return state.
    A seller may undo only their own returns; admins/managers any."""
    qs = Return.objects.select_related("sale", "sale__client", "product", "settlement")
    if not request.user.can_see_all_records:
        qs = qs.filter(created_by=request.user)
    ret = get_object_or_404(qs, pk=pk)
    settlement = ret.settlement
    is_refund = settlement is not None and settlement.kind == Payment.Kind.REFUND_OUT
    is_credit = settlement is not None and settlement.kind == Payment.Kind.RETURN_CREDIT
    if request.method == "POST":
        sale_pk = ret.sale_id
        client = ret.sale.client
        summary = (
            f"Mijoz {client.name} qaytarishi bekor qilindi "
            f"({ret.product.name}) — {ret.amount:,.0f} so'm"
        )
        with transaction.atomic():
            _reverse_return(ret)
        AuditLog.record(request.user, AuditLog.Action.VOID, "Qaytarish", sale_pk, summary)
        messages.success(request, "Qaytarish bekor qilindi — qarz va kassa qayta hisoblandi.")
        return form_reload(request, reverse("sale_detail", args=[sale_pk]))
    if is_refund:
        extra = " Naqd qaytarilgan pul kassaga qaytadi."
    elif is_credit:
        extra = (
            " Mijoz avansiga o'tgan summa bekor qilinadi — agar u boshqa "
            "sotuvlarga ishlatilgan bo'lsa, o'sha sotuvlar qayta qarzga aylanadi."
        )
    else:
        extra = ""
    return render_confirm(
        request,
        "Qaytarishni bekor qilish",
        f"“{ret.product.name}” — {ret.amount:,.0f} so'm qaytarish bekor qilinadi "
        f"va sotuv qarzi qayta tiklanadi.{extra} Davom etasizmi?",
        "Ha, bekor qilish",
        confirm_class="btn-danger",
    )


def _return_message(total, to_debt, excess, refunded):
    """Spell out where the returned value went — sellers need to see that the money
    side was handled, not just that goods came back."""
    parts = [f"Qaytarish qabul qilindi: {total:,.0f} so'm."]
    if to_debt > 0:
        parts.append(f"Qarzdan {to_debt:,.0f} so'm yopildi.")
    if excess > 0:
        parts.append(
            f"Ortiqcha {excess:,.0f} so'm "
            + ("kassadan naqd qaytarildi." if refunded else "mijoz avansiga o'tdi.")
        )
    return " ".join(parts)


def sale_delete(request, pk):
    """Delete a sale outright. Any payments and returns booked against it are reversed
    with it — they cascade — and the client's advance is reconciled so freed credit
    settles onto other open receipts (or an orphaned credit is peeled back). The till,
    debt and profit all re-derive. Because this removes money records, the confirm
    dialog spells out exactly what will go, mirroring how payment/advance voids work."""
    sale = get_object_or_404(
        Sale.objects.visible_to(request.user).select_related("client"), pk=pk
    )
    client, seller = sale.client, sale.sales_rep
    paid = sale.paid_amount
    return_count = sale.returns.count()
    if request.method == "POST":
        summary = f"Mijoz {sale.client.name} sotuvi — {sale.total_price:,.0f} so'm"
        sale_pk = sale.pk
        with transaction.atomic():
            sale.delete()  # items, payments and returns cascade with it
            # Freed or now-orphaned advance allocations settle back into balance.
            _reconcile_client_advance(client, seller)
        AuditLog.record(request.user, AuditLog.Action.DELETE, "Sotuv", sale_pk, summary)
        messages.success(request, "Sotuv o'chirildi.")
        return form_reload(request, reverse("sale_list"))
    extra = []
    if paid > 0:
        extra.append(f"{paid:,.0f} so'm to'lov")
    if return_count:
        extra.append(f"{return_count} ta qaytarish")
    warn = (
        f" Unga bog'liq {' va '.join(extra)} ham o'chiriladi, kassa va qarz qayta hisoblanadi."
        if extra else ""
    )
    return render_confirm(
        request,
        "Sotuvni o'chirish",
        f"Bu sotuv butunlay o'chiriladi.{warn} Davom etasizmi?",
        "Ha, o'chirish",
        confirm_class="btn-danger",
    )
