from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, ProtectedError, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.decorators import role_required
from accounts.models import User

from .forms import ClientForm, OrderForm, OrderItemFormSet, OrderStatusForm, ProductForm
from .models import Client, Order, Product


def _visible_clients(user):
    qs = Client.objects.select_related("owner")
    return qs if user.can_see_all_records else qs.filter(owner=user)


def _get_visible_order(user, pk):
    return get_object_or_404(
        Order.objects.visible_to(user).select_related("client", "sales_rep"), pk=pk
    )


# --- Dashboard ---------------------------------------------------------------

def dashboard(request):
    orders = Order.objects.visible_to(request.user)
    sales_orders = orders.filter(status__in=Order.SALES_STATUSES)

    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    revenue = F("items__quantity") * F("items__unit_price")

    month_sales = sales_orders.filter(created_at__gte=month_start).aggregate(
        total=Sum(revenue)
    )["total"] or 0
    all_time_sales = sales_orders.aggregate(total=Sum(revenue))["total"] or 0

    counts_by_status = dict(
        orders.values_list("status").annotate(n=Count("id")).order_by()
    )
    status_summary = [
        (status.value, status.label, counts_by_status.get(status.value, 0))
        for status in Order.Status
    ]

    top_clients = (
        _visible_clients(request.user)
        .filter(orders__status__in=Order.SALES_STATUSES)
        .annotate(total=Sum(F("orders__items__quantity") * F("orders__items__unit_price")))
        .order_by("-total")[:5]
    )

    recent_orders = orders.select_related("client", "sales_rep").with_totals()[:8]

    context = {
        "month_sales": month_sales,
        "all_time_sales": all_time_sales,
        "status_summary": status_summary,
        "top_clients": top_clients,
        "recent_orders": recent_orders,
        "client_count": _visible_clients(request.user).count(),
        "order_count": orders.count(),
    }
    return render(request, "crm/dashboard.html", context)


# --- Clients ------------------------------------------------------------------

def client_list(request):
    clients = (
        _visible_clients(request.user)
        .annotate(order_count=Count("orders"))
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
        messages.success(request, f"Client “{client.name}” created.")
        return redirect("client_list")
    return render(request, "crm/form.html", {"form": form, "title": "New client"})


def client_edit(request, pk):
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Client “{client.name}” updated.")
        return redirect("client_list")
    return render(request, "crm/form.html", {"form": form, "title": f"Edit {client.name}"})


def client_delete(request, pk):
    client = get_object_or_404(_visible_clients(request.user), pk=pk)
    if request.method == "POST":
        try:
            client.delete()
            messages.success(request, f"Client “{client.name}” deleted.")
        except ProtectedError:
            messages.error(
                request, f"Cannot delete “{client.name}” — it has orders. Cancel them first."
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
        messages.success(request, f"Product “{product.name}” created.")
        return redirect("product_list")
    return render(request, "crm/form.html", {"form": form, "title": "New product"})


@role_required(User.Role.ADMIN, User.Role.MANAGER)
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Product “{product.name}” updated.")
        return redirect("product_list")
    return render(request, "crm/form.html", {"form": form, "title": f"Edit {product.name}"})


# --- Orders -------------------------------------------------------------------

def order_list(request):
    orders = (
        Order.objects.visible_to(request.user)
        .select_related("client", "sales_rep")
        .with_totals()
        .order_by("-created_at")
    )
    status = request.GET.get("status", "")
    if status:
        orders = orders.filter(status=status)
    page = Paginator(orders, 25).get_page(request.GET.get("page"))
    return render(
        request,
        "crm/order_list.html",
        {"page": page, "status": status, "statuses": Order.Status},
    )


def order_create(request):
    form = OrderForm(request.POST or None, user=request.user)
    formset = OrderItemFormSet(request.POST or None)
    if request.method == "POST" and form.is_valid() and formset.is_valid():
        order = form.save(commit=False)
        order.sales_rep = request.user
        order.save()
        formset.instance = order
        formset.save()
        messages.success(request, f"Order {order.number} created.")
        return redirect("order_detail", pk=order.pk)
    return render(
        request,
        "crm/order_form.html",
        {"form": form, "formset": formset, "title": "New order"},
    )


def order_detail(request, pk):
    order = _get_visible_order(request.user, pk)
    status_form = OrderStatusForm(instance=order)
    return render(request, "crm/order_detail.html", {"order": order, "status_form": status_form})


def order_set_status(request, pk):
    order = _get_visible_order(request.user, pk)
    if request.method != "POST":
        return redirect("order_detail", pk=pk)
    form = OrderStatusForm(request.POST, instance=order)
    if form.is_valid():
        form.save()
        messages.success(request, f"Order {order.number} marked {order.get_status_display()}.")
    return redirect("order_detail", pk=pk)


def order_delete(request, pk):
    order = _get_visible_order(request.user, pk)
    if not request.user.can_see_all_records and order.status != Order.Status.DRAFT:
        raise PermissionDenied
    if request.method == "POST":
        number = order.number
        order.delete()
        messages.success(request, f"Order {number} deleted.")
        return redirect("order_list")
    return render(request, "crm/confirm_delete.html", {"object": order, "back": "order_list"})
