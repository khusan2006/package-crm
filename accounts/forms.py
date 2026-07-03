from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User

USER_LABELS = {
    "username": "Login",
    "first_name": "Ismi",
    "last_name": "Familiyasi",
    "email": "Email",
    "is_active": "Faol",
}


class LoginForm(AuthenticationForm):
    error_messages = {
        "invalid_login": "Login yoki parol noto'g'ri. Qayta urinib ko'ring.",
        "inactive": "Bu hisob faol emas.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Login"
        self.fields["password"].label = "Parol"


class UserCreateForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone", "role"]
        labels = USER_LABELS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password1"].label = "Parol"
        self.fields["password2"].label = "Parolni tasdiqlang"


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone", "role", "is_active"]
        labels = USER_LABELS
