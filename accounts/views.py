from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render, resolve_url
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from core import llm
from workspaces.services import secret_names

from .forms import LoginForm, ProfileForm, RegisterForm
from .models import OpenAICredential

TWO_WEEKS = 60 * 60 * 24 * 14


def _next_url(request):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return target
    return resolve_url(settings.LOGIN_REDIRECT_URL)


@login_not_required
@sensitive_post_parameters("password")
@never_cache
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_next_url(request))
    form = LoginForm(request, request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.user)
        # Sem "manter conectado", a sessão termina ao fechar o navegador.
        request.session.set_expiry(TWO_WEEKS if form.cleaned_data["remember"] else 0)
        return redirect(_next_url(request))
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@login_not_required
@sensitive_post_parameters("password1", "password2")
@never_cache
def register_view(request):
    if request.user.is_authenticated:
        return redirect(_next_url(request))
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect(_next_url(request))
    return render(request, "accounts/register.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("accounts:login")


@sensitive_post_parameters("old_password", "new_password1", "new_password2", "api_key")
def account_view(request):
    profile_form = ProfileForm(instance=request.user, prefix="profile")
    password_form = PasswordChangeForm(request.user, prefix="password")
    openai_error = ""

    if request.method == "POST":
        if request.POST.get("form") == "openai":
            openai_error = _save_openai_key(request)
            if not openai_error:
                return redirect("accounts:account")
        elif request.POST.get("form") == "openai-remove":
            OpenAICredential.objects.filter(user=request.user).delete()
            messages.success(request, "Chave da OpenAI removida.")
            return redirect("accounts:account")
        elif request.POST.get("form") == "profile":
            profile_form = ProfileForm(request.POST, instance=request.user, prefix="profile")
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, "Dados atualizados.")
                return redirect("accounts:account")
        elif request.POST.get("form") == "password":
            password_form = PasswordChangeForm(request.user, request.POST, prefix="password")
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, password_form.user)
                messages.success(request, "Senha alterada.")
                return redirect("accounts:account")

    return render(request, "accounts/account.html", {
        "section": "account",
        "profile_form": profile_form,
        "password_form": password_form,
        "openai": OpenAICredential.objects.filter(user=request.user).first(),
        "openai_error": openai_error,
        "workspace_key": llm.WORKSPACE_SECRET in secret_names(request.workspace),
        "instance_key": bool(settings.OPENAI_API_KEY),
    })


def _save_openai_key(request):
    """Valida a chave na OpenAI e guarda criptografada. Devolve a mensagem de erro, se houver."""
    api_key = request.POST.get("api_key", "").strip()
    if len(api_key) < 20 or len(api_key) > 300 or any(c.isspace() for c in api_key):
        return "Cole a chave completa, sem espaços (ela começa com “sk-”)."
    try:
        models = llm.list_models(api_key)
    except llm.LLMError as exc:
        if exc.status in (401, 403):
            return str(exc)
        # A OpenAI não respondeu: guarda mesmo assim e valida no primeiro uso.
        OpenAICredential.store(request.user, api_key)
        messages.warning(request, "Chave salva, mas não foi possível validá-la agora.")
        return ""
    if not models:
        return "A chave é válida, mas não dá acesso a nenhum modelo de conversa."
    OpenAICredential.store(request.user, api_key, models)
    messages.success(request, f"Chave validada: {len(models)} modelos disponíveis.")
    return ""
