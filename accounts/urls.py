from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("entrar/", views.login_view, name="login"),
    path("cadastro/", views.register_view, name="register"),
    path("sair/", views.logout_view, name="logout"),
    path("conta/", views.account_view, name="account"),
]
