from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator

from crm.models import AuditLog, seller_production_debt
from crm.utils import form_changes, form_response, form_success

from .decorators import role_required
from .forms import LoginForm, UserCreateForm, UserEditForm
from .models import User


@method_decorator(login_not_required, name="dispatch")
class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True


@role_required(User.Role.ADMIN)
def user_list(request):
    """The staff list, with each seller's standing production debt alongside them.

    The figure is per-user rather than batched: this list is a handful of staff, and
    `seller_production_debt` is the one definition of that number — recomputing it
    here in SQL would be a second copy to keep in step."""
    users = list(User.objects.order_by("username"))
    for account in users:
        account.production_debt = (
            seller_production_debt(account)
            if account.role == User.Role.SALES
            else None
        )
    return render(request, "accounts/user_list.html", {"users": users})


@role_required(User.Role.ADMIN)
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            user = form.save()
            # Who may sign in and with what powers belongs in the same trail as the
            # money: an account appearing out of nowhere is exactly what it is for.
            AuditLog.record(
                request.user, AuditLog.Action.CREATE, "Foydalanuvchi", user.pk,
                f"{user.username} yaratildi — {user.get_role_display()}",
            )
            messages.success(request, f"“{user.username}” foydalanuvchisi yaratildi.")
            return form_success(request, reverse("user_list"))
        return form_response(request, form, "Yangi foydalanuvchi", invalid=True)
    return form_response(request, form, "Yangi foydalanuvchi")


@role_required(User.Role.ADMIN)
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = UserEditForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        # A changed role or a deactivated account is the point of the record, so the
        # changed fields are named rather than summed up as "yangilandi".
        changes = form_changes(form)
        form.save()
        summary = f"{user.username} yangilandi"
        if changes:
            summary += f" — {changes}"
        AuditLog.record(
            request.user, AuditLog.Action.UPDATE, "Foydalanuvchi", user.pk, summary[:255]
        )
        messages.success(request, f"“{user.username}” foydalanuvchisi yangilandi.")
        return redirect("user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "title": f"Tahrirlash: {user.username}"},
    )
