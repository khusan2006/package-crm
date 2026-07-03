from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_not_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator

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
    users = User.objects.order_by("username")
    return render(request, "accounts/user_list.html", {"users": users})


@role_required(User.Role.ADMIN)
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"“{user.username}” foydalanuvchisi yaratildi.")
        return redirect("user_list")
    return render(
        request, "accounts/user_form.html", {"form": form, "title": "Yangi foydalanuvchi"}
    )


@role_required(User.Role.ADMIN)
def user_edit(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = UserEditForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"“{user.username}” foydalanuvchisi yangilandi.")
        return redirect("user_list")
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "title": f"Tahrirlash: {user.username}"},
    )
