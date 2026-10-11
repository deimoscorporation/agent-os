from django import forms
from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ValidationError

from .models import User


def _clean_full_name(value):
    name = " ".join(value.split())
    if len(name.split(" ")) < 2:
        raise ValidationError("Informe nome e sobrenome.")
    return name


class LoginForm(forms.Form):
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={
        "autocomplete": "email", "placeholder": "voce@empresa.com", "autofocus": True,
    }))
    password = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput(attrs={
        "autocomplete": "current-password", "placeholder": "Sua senha",
    }))
    remember = forms.BooleanField(label="Manter conectado", required=False, initial=True)

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user = None
        super().__init__(*args, **kwargs)

    def clean(self):
        data = super().clean()
        email, password = data.get("email"), data.get("password")
        if email and password:
            self.user = authenticate(self.request, username=email.lower(), password=password)
            if self.user is None:
                raise ValidationError("E-mail ou senha incorretos.")
        return data


class RegisterForm(forms.Form):
    full_name = forms.CharField(label="Nome completo", max_length=150, widget=forms.TextInput(attrs={
        "autocomplete": "name", "placeholder": "Maria Silva", "autofocus": True,
    }))
    email = forms.EmailField(label="E-mail", widget=forms.EmailInput(attrs={
        "autocomplete": "email", "placeholder": "voce@empresa.com",
    }))
    password1 = forms.CharField(label="Senha", strip=False, widget=forms.PasswordInput(attrs={
        "autocomplete": "new-password", "placeholder": "Mínimo de 8 caracteres",
    }))
    password2 = forms.CharField(label="Confirmar senha", strip=False, widget=forms.PasswordInput(attrs={
        "autocomplete": "new-password", "placeholder": "Repita a senha",
    }))

    def clean_full_name(self):
        return _clean_full_name(self.cleaned_data["full_name"])

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise ValidationError("Já existe uma conta com este e-mail.")
        return email

    def clean(self):
        data = super().clean()
        password1, password2 = data.get("password1"), data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", "As senhas não coincidem.")
        if password1:
            candidate = User(email=data.get("email", ""), full_name=data.get("full_name", ""))
            try:
                password_validation.validate_password(password1, candidate)
            except ValidationError as exc:
                self.add_error("password1", exc)
        return data

    def save(self):
        return User.objects.create_user(
            email=self.cleaned_data["email"],
            password=self.cleaned_data["password1"],
            full_name=self.cleaned_data["full_name"],
        )


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["full_name"]
        widgets = {"full_name": forms.TextInput(attrs={"autocomplete": "name"})}

    def clean_full_name(self):
        return _clean_full_name(self.cleaned_data["full_name"])
