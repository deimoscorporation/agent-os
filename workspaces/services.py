import re

from core import crypto

from .models import Invitation, Membership, Secret, Workspace

SESSION_KEY = "workspace_id"
SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
MAX_SECRET_LENGTH = 8192


# --------------------------------------------------------------------------
# Workspaces
# --------------------------------------------------------------------------

def personal_workspace(user):
    workspace, created = Workspace.objects.get_or_create(
        owner=user, defaults={"name": "Pessoal", "kind": Workspace.Kind.PERSONAL},
    )
    if created:
        Membership.objects.create(workspace=workspace, user=user, role=Membership.Role.ADMIN)
    return workspace


def user_workspaces(user):
    """Workspaces do usuário, com o pessoal primeiro."""
    personal_workspace(user)
    workspaces = Workspace.objects.filter(memberships__user=user)
    return sorted(workspaces, key=lambda w: (not w.is_personal, w.name.lower()))


def accept_invitations(user):
    for invitation in Invitation.objects.filter(email=user.email):
        Membership.objects.get_or_create(
            workspace=invitation.workspace, user=user, defaults={"role": invitation.role},
        )
        invitation.delete()


def active_membership(request):
    """Membership do workspace escolhido na sessão; cai no pessoal se ele não for mais válido."""
    workspace_id = request.session.get(SESSION_KEY)
    if workspace_id:
        membership = (
            Membership.objects.filter(user=request.user, workspace_id=workspace_id).select_related("workspace").first()
        )
        if membership:
            return membership
    workspace = personal_workspace(request.user)
    return Membership.objects.select_related("workspace").get(user=request.user, workspace=workspace)


def create_organization(user, name):
    workspace = Workspace.objects.create(name=name, kind=Workspace.Kind.ORGANIZATION)
    Membership.objects.create(workspace=workspace, user=user, role=Membership.Role.ADMIN)
    return workspace


# --------------------------------------------------------------------------
# Cofre de segredos
# --------------------------------------------------------------------------

def set_secret(workspace, name, value, user, description=""):
    secret, _ = Secret.objects.update_or_create(
        workspace=workspace,
        name=name,
        defaults={
            "ciphertext": crypto.encrypt(value),
            "hint": crypto.hint(value),
            "description": description,
            "updated_by": user,
        },
    )
    return secret


def reveal_secret(secret):
    """Uso exclusivo do runtime que executa ferramentas — nunca exponha em views."""
    return crypto.decrypt(secret.ciphertext)


def secrets_for(workspace, names):
    """Segredos pedidos por uma ferramenta: {nome: valor}. Para o runtime de execução."""
    found = Secret.objects.filter(workspace=workspace, name__in=names)
    return {s.name: reveal_secret(s) for s in found}


def secret_names(workspace):
    return set(Secret.objects.filter(workspace=workspace).values_list("name", flat=True))
