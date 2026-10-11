from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core.api import ApiError, api_view, json_body, require_admin

from .models import Invitation, Membership, Secret, Workspace
from .services import (
    MAX_SECRET_LENGTH,
    SECRET_NAME_RE,
    SESSION_KEY,
    create_organization,
    personal_workspace,
    set_secret,
)

User = get_user_model()


def _clean_name(value):
    name = " ".join(str(value or "").split())
    if not 2 <= len(name) <= 80:
        raise ApiError("O nome deve ter entre 2 e 80 caracteres.")
    return name


def _clean_role(value):
    if value not in Membership.Role.values:
        raise ApiError("Papel inválido.")
    return value


def _require_organization(request):
    if not request.workspace.is_organization:
        raise ApiError("Disponível apenas em organizações.")


def _ensure_another_admin(workspace, membership):
    """Impede que a organização fique sem nenhum admin."""
    if membership.is_admin and not workspace.memberships.filter(
        role=Membership.Role.ADMIN,
    ).exclude(pk=membership.pk).exists():
        raise ApiError("A organização precisa de pelo menos um admin. Promova outra pessoa antes.")


# --------------------------------------------------------------------------
# Troca de workspace e equipe
# --------------------------------------------------------------------------

@require_POST
def switch(request):
    membership = get_object_or_404(Membership, user=request.user, workspace_id=request.POST.get("workspace_id"))
    request.session[SESSION_KEY] = str(membership.workspace_id)
    target = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        target = "marketplace:workspace"
    return redirect(target)


def team(request):
    workspace = request.workspace
    context = {"section": "team"}
    if workspace.is_organization:
        context.update(
            members=workspace.memberships.select_related("user"),
            invitations=workspace.invitations.all(),
            roles=Membership.Role.choices,
        )
    return render(request, "workspaces/team.html", context)


@require_POST
@api_view
def organization_create(request):
    workspace = create_organization(request.user, _clean_name(json_body(request).get("name")))
    request.session[SESSION_KEY] = str(workspace.id)
    return JsonResponse({"id": str(workspace.id), "name": workspace.name}, status=201)


@require_http_methods(["PATCH", "DELETE"])
@api_view
def organization(request):
    _require_organization(request)
    require_admin(request)
    workspace = request.workspace
    if request.method == "DELETE":
        workspace.delete()
        request.session[SESSION_KEY] = str(personal_workspace(request.user).id)
        return JsonResponse({"deleted": True})
    workspace.name = _clean_name(json_body(request).get("name"))
    workspace.save(update_fields=["name"])
    return JsonResponse({"name": workspace.name})


@require_POST
@api_view
def member_add(request):
    _require_organization(request)
    require_admin(request)
    data = json_body(request)
    email = str(data.get("email") or "").strip().lower()
    role = _clean_role(data.get("role", Membership.Role.MEMBER))
    try:
        validate_email(email)
    except ValidationError:
        raise ApiError("Informe um e-mail válido.")

    workspace = request.workspace
    user = User.objects.filter(email=email).first()
    if user is None:
        _, created = Invitation.objects.update_or_create(
            workspace=workspace, email=email, defaults={"role": role, "invited_by": request.user},
        )
        return JsonResponse({"invited": True, "created": created}, status=201)
    if workspace.memberships.filter(user=user).exists():
        raise ApiError("Essa pessoa já faz parte da organização.")
    Membership.objects.create(workspace=workspace, user=user, role=role)
    return JsonResponse({"invited": False}, status=201)


@require_http_methods(["PATCH", "DELETE"])
@api_view
def member(request, pk):
    _require_organization(request)
    workspace = request.workspace
    target = get_object_or_404(Membership, pk=pk, workspace=workspace)
    is_self = target.user_id == request.user.id

    if request.method == "DELETE":
        if not is_self:
            require_admin(request)
        _ensure_another_admin(workspace, target)
        target.delete()
        if is_self:
            request.session[SESSION_KEY] = str(personal_workspace(request.user).id)
        return JsonResponse({"removed": True, "left": is_self})

    require_admin(request)
    role = _clean_role(json_body(request).get("role"))
    if role != Membership.Role.ADMIN:
        _ensure_another_admin(workspace, target)
    target.role = role
    target.save(update_fields=["role"])
    return JsonResponse({"role": target.role})


@require_http_methods(["DELETE"])
@api_view
def invitation(request, pk):
    _require_organization(request)
    require_admin(request)
    get_object_or_404(Invitation, pk=pk, workspace=request.workspace).delete()
    return JsonResponse({"removed": True})


# --------------------------------------------------------------------------
# Cofre de segredos
# --------------------------------------------------------------------------

def _secret_usage(workspace):
    """{nome do segredo: [ferramentas instaladas que o usam]}"""
    from marketplace.models import Installation, Item

    usage = {}
    installs = Installation.objects.filter(workspace=workspace, item__kind=Item.Kind.TOOL).select_related("item")
    for inst in installs:
        for name in inst.item.manifest_at(inst.installed_version).get("secrets", []):
            usage.setdefault(name, []).append(inst.item.name)
    return usage


def secrets(request):
    usage = _secret_usage(request.workspace)
    stored = list(request.workspace.secrets.select_related("updated_by"))
    for secret in stored:
        secret.used_by = usage.get(secret.name, [])
    stored_names = {s.name for s in stored}
    missing = [{"name": name, "used_by": tools} for name, tools in sorted(usage.items()) if name not in stored_names]
    return render(request, "workspaces/secrets.html", {"section": "secrets", "secrets": stored, "missing": missing})


@require_POST
@api_view
def secret_set(request):
    require_admin(request)
    data = json_body(request)
    name = str(data.get("name") or "").strip()
    value = data.get("value")
    description = str(data.get("description") or "").strip()[:160]
    if not SECRET_NAME_RE.match(name):
        raise ApiError("Nome inválido: use MAIÚSCULAS, números e _ (ex.: OPENAI_API_KEY).")
    if not isinstance(value, str) or not value:
        raise ApiError("Informe o valor do segredo.")
    if len(value) > MAX_SECRET_LENGTH:
        raise ApiError("O valor é grande demais.")
    secret = set_secret(request.workspace, name, value, request.user, description)
    # O valor nunca é devolvido.
    return JsonResponse({"name": secret.name, "hint": secret.hint}, status=201)


@require_http_methods(["DELETE"])
@api_view
def secret_delete(request, name):
    require_admin(request)
    get_object_or_404(Secret, workspace=request.workspace, name=name).delete()
    return JsonResponse({"deleted": True})
