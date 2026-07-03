from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, F, ProtectedError, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import User

from .forms import ClientForm, ProductForm, SaleForm
from .models import COST, PROFIT, REVENUE, Client, Product, Sale


def _visible_clients(user):
    qs = Client.objects.select_related("owner")
    return qs if user.can_see_all_records else qs.filter(owner=user)


def _sale_totals(qs):
    return qs.aggregate(revenue=Sum(REVENUE), cost=Sum(COST), profit=Sum(PROFIT))


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

    context = {
        "month": month,
        "all_time": all_time,
        "month_count": month_sales.count(),
        "top_clients": top_clients,
        "recent_sales": recent_sales,
        "client_count": _visible_clients(request.user).count(),
        "debt_total": debt_total,
        "overdue_count": overdue_count,
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
    if request.method == "POST" and form.is_valid():
        client = form.save(commit=False)
        client.owner = request.user
        client.save()
        messages.success(request, f"“{client.name}” mijozi qo'shildi.")
        return redirect("client_list")
    return render(request, "crm/form.html", {"form": form, "title": "Yangi mijoz"})


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
    products = Product.objects.all()
    q = request.GET.get("q", "").strip()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q))
    page = Paginator(products, 25).get_page(request.GET.get("page"))
    return render(request, "crm/product_list.html", {"page": page, "q": q})


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def product_create(request):
    form = ProductForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        product = form.save()
        messages.success(request, f"“{product.name}” mahsuloti qo'shildi.")
        return redirect("product_list")
    return render(request, "crm/form.html", {"form": form, "title": "Yangi mahsulot"})


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"“{product.name}” mahsuloti yangilandi.")
        return redirect("product_list")
    return render(
        request, "crm/form.html", {"form": form, "title": f"Tahrirlash: {product.name}"}
    )


# --- Sales --------------------------------------------------------------------

def sale_list(request):
    sales = (
        Sale.objects.visible_to(request.user)
        .select_related("client", "product", "sales_rep")
        .with_totals()
        .order_by("-date", "-created_at")
    )
    date_from = _parse_date(request.GET.get("dan"))
    date_to = _parse_date(request.GET.get("gacha"))
    if date_from:
        sales = sales.filter(date__gte=date_from)
    if date_to:
        sales = sales.filter(date__lte=date_to)

    totals = _sale_totals(sales)
    totals["debt"] = sales.filter(is_debt=True).aggregate(v=Sum(REVENUE))["v"]
    page = Paginator(sales, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "crm/sale_list.html",
        {
            "page": page,
            "totals": totals,
            "date_from": date_from,
            "date_to": date_to,
        },
    )


def sale_create(request):
    form = SaleForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        sale = form.save(commit=False)
        sale.sales_rep = request.user
        sale.save()
        messages.success(request, "Sotuv qo'shildi.")
        return redirect("sale_list")
    return render(request, "crm/form.html", {"form": form, "title": "Yangi sotuv"})


def sale_edit(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    form = SaleForm(request.POST or None, instance=sale, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Sotuv yangilandi.")
        return redirect("sale_list")
    return render(request, "crm/form.html", {"form": form, "title": "Sotuvni tahrirlash"})


def sale_delete(request, pk):
    sale = get_object_or_404(Sale.objects.visible_to(request.user), pk=pk)
    if request.method == "POST":
        sale.delete()
        messages.success(request, "Sotuv o'chirildi.")
        return redirect("sale_list")
    return render(request, "crm/confirm_delete.html", {"object": sale, "back": "sale_list"})
