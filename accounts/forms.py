from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.utils.safestring import mark_safe

from .models import User

USERNAME_HELP = (
    "Majburiy. 150 ta belgidan oshmasligi kerak. "
    "Faqat harflar, raqamlar va @/./+/-/_ belgilaridan foydalaning."
)

PASSWORD_HELP = mark_safe(
    "<ul>"
    "<li>Parolingiz boshqa shaxsiy ma'lumotlaringizga juda o'xshash bo'lmasligi kerak.</li>"
    "<li>Parolingiz kamida 8 ta belgidan iborat bo'lishi kerak.</li>"
    "<li>Parolingiz keng tarqalgan parol bo'lmasligi kerak.</li>"
    "<li>Parolingiz faqat raqamlardan iborat bo'lmasligi kerak.</li>"
    "</ul>"
)

PASSWORD_CONFIRM_HELP = "Tasdiqlash uchun avvalgi parolni qayta kiriting."

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
        fields = ["username", "first_name", "last_name", "phone", "role"]
        labels = USER_LABELS
        widgets = {"phone": forms.TextInput(attrs={"data-phone": ""})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].help_text = USERNAME_HELP
        self.fields["password1"].label = "Parol"
        self.fields["password1"].help_text = PASSWORD_HELP
        self.fields["password2"].label = "Parolni tasdiqlang"
        self.fields["password2"].help_text = PASSWORD_CONFIRM_HELP


class UserEditForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "phone", "role", "is_active"]
        labels = USER_LABELS
        widgets = {"phone": forms.TextInput(attrs={"data-phone": ""})}
