import uuid

from django.conf import settings
from django.db import models


class Workspace(models.Model):
    """Onde vivem itens instalados, itens criados e segredos.

    Todo usuário tem um workspace pessoal; organizações são workspaces
    compartilhados entre vários membros.
    """

    class Kind(models.TextChoices):
        PERSONAL = "personal", "Pessoal"
        ORGANIZATION = "organization", "Organização"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    # Preenchido apenas nos workspaces pessoais.
    owner = models.OneToOneField(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="personal_workspace",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["kind", "name"]  # "organization" < "personal": invertido em user_workspaces()

    def __str__(self):
        return self.name

    @property
    def is_personal(self):
        return self.kind == self.Kind.PERSONAL

    @property
    def is_organization(self):
        return self.kind == self.Kind.ORGANIZATION

    @property
    def initials(self):
        parts = self.name.split()
        return (parts[0][0] + (parts[1][0] if len(parts) > 1 else "")).upper() if parts else "?"


class Membership(models.Model):
    class Role(models.TextChoices):
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Membro"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["role", "joined_at"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "user"], name="unique_membership"),
        ]

    def __str__(self):
        return f"{self.user} em {self.workspace} ({self.role})"

    @property
    def is_admin(self):
        return self.role == self.Role.ADMIN


class Invitation(models.Model):
    """Convite por e-mail para quem ainda não tem conta; vira Membership no cadastro/login."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=Membership.Role.choices, default=Membership.Role.MEMBER)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "email"], name="unique_invitation"),
        ]

    def __str__(self):
        return f"{self.email} -> {self.workspace}"


class Secret(models.Model):
    """Segredo do cofre. O valor é guardado criptografado e nunca é devolvido pela API."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="secrets")
    name = models.CharField(max_length=64)
    description = models.CharField(max_length=160, blank=True)
    ciphertext = models.TextField()
    hint = models.CharField(max_length=4, blank=True)  # últimos caracteres, só para identificação
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "name"], name="unique_secret_name"),
        ]

    def __str__(self):
        return f"{self.name} @ {self.workspace}"
