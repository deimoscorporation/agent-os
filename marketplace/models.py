import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Avg, Count

from workspaces.models import Workspace


class Item(models.Model):
    """Qualquer coisa instalável: ferramenta, agente, workflow ou skill.

    O conteúdo específico de cada tipo fica em `manifest`:
      ferramenta: {"input_schema", "code", "entrypoint", "runtime", "permissions", "secrets"}
      agente:     {"model", "system_prompt", "tools": [slugs]}
      workflow:   {"trigger", "steps": [{"name", "uses": "tipo:slug"}]}
      skill:      {"instructions"}
    """

    class Kind(models.TextChoices):
        TOOL = "tool", "Ferramenta"
        AGENT = "agent", "Agente"
        WORKFLOW = "workflow", "Workflow"
        SKILL = "skill", "Skill"

    class Category(models.TextChoices):
        SECURITY = "security", "Segurança"
        DEV = "dev", "Desenvolvimento"
        DATA = "data", "Dados"
        PRODUCTIVITY = "productivity", "Produtividade"
        COMMUNICATION = "communication", "Comunicação"
        OTHER = "other", "Outros"

    class Visibility(models.TextChoices):
        PRIVATE = "private", "Privado"
        # Marketplace privado: visível para os membros da organização dona do item.
        ORGANIZATION = "organization", "Organização"
        # Marketplace público: só depois de aprovado na fila de revisão.
        PUBLIC = "public", "Público"

    class Source(models.TextChoices):
        OFFICIAL = "official", "Deimos Corp"
        USER = "user", "Usuário"

    DEFAULT_ICONS = {
        Kind.TOOL: "i-wrench",
        Kind.AGENT: "i-bot",
        Kind.WORKFLOW: "i-workflow",
        Kind.SKILL: "i-book",
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)
    slug = models.CharField(max_length=64)
    name = models.CharField(max_length=80)
    summary = models.CharField(max_length=160, blank=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=32, blank=True)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    tags = models.JSONField(default=list, blank=True)
    version = models.CharField(max_length=20, default="1.0.0")
    # Última versão aprovada para o marketplace público.
    approved_version = models.CharField(max_length=20, blank=True)
    manifest = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.USER)
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE)
    # Workspace dono do item. Vazio nos itens oficiais da Deimos.
    workspace = models.ForeignKey(Workspace, null=True, blank=True, on_delete=models.CASCADE, related_name="items")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_items",
    )
    author = models.CharField(max_length=150, default="Deimos Corp")
    featured = models.BooleanField(default=False)
    install_count = models.PositiveIntegerField(default=0)
    rating_avg = models.FloatField(default=0)
    rating_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-featured", "-install_count", "name"]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.name}"

    @property
    def icon_id(self):
        return self.icon or self.DEFAULT_ICONS.get(self.kind, "i-grid")

    @property
    def is_official(self):
        return self.source == self.Source.OFFICIAL

    @property
    def is_public(self):
        return self.visibility == self.Visibility.PUBLIC

    @property
    def is_org_shared(self):
        return self.visibility == self.Visibility.ORGANIZATION

    def is_owned_by(self, workspace):
        return self.workspace_id is not None and self.workspace_id == workspace.id

    def can_edit(self, membership):
        """Admins editam tudo do workspace; membros, só o que criaram."""
        if not self.is_owned_by(membership.workspace):
            return False
        return membership.is_admin or self.created_by_id == membership.user_id

    def available_version(self, workspace):
        """Versão que este workspace recebe ao instalar/atualizar.

        Dentro do workspace dono (ou da organização que compartilha o item) vale a
        versão mais recente; para o público, apenas a última aprovada.
        """
        if self.is_owned_by(workspace) or self.is_org_shared or not self.approved_version:
            return self.version
        return self.approved_version

    def manifest_at(self, version):
        if version and version != self.version:
            snapshot = self.versions.filter(version=version).first()
            if snapshot:
                return snapshot.manifest
        return self.manifest

    def tool_definition(self, version=None):
        """Formato de tool use aceito pelos agentes (name, description, input_schema)."""
        manifest = self.manifest_at(version)
        return {
            "name": self.slug,
            "description": self.description,
            "input_schema": manifest.get("input_schema", {"type": "object", "properties": {}}),
        }

    def bump_version(self):
        try:
            major, minor, patch = (int(p) for p in self.version.split("."))
        except ValueError:
            major, minor, patch = 1, 0, 0
        self.version = f"{major}.{minor}.{patch + 1}"

    def record_version(self, changelog):
        return ItemVersion.objects.create(
            item=self, version=self.version, changelog=changelog, manifest=self.manifest,
        )

    def refresh_rating(self):
        stats = self.reviews.aggregate(avg=Avg("rating"), count=Count("id"))
        self.rating_avg = round(stats["avg"] or 0, 2)
        self.rating_count = stats["count"]
        self.save(update_fields=["rating_avg", "rating_count"])

    def pending_request(self):
        return self.publication_requests.filter(status=PublicationRequest.Status.PENDING).first()

    def sharing_status(self):
        """Resumo legível de onde o item está visível."""
        pending = self.pending_request()
        if self.is_public:
            label = f"Público (v{self.approved_version} aprovada)"
            if pending:
                label += f" · v{pending.version} em revisão"
            return label
        if pending:
            return "Em revisão para o marketplace"
        if self.is_org_shared:
            return "Visível na organização"
        return "Privado"


class Installation(models.Model):
    """Um item instalado em um workspace, fixado em uma versão."""

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="installations")
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="installations")
    installed_version = models.CharField(max_length=20, default="1.0.0")
    installed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    enabled = models.BooleanField(default=True)
    installed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-installed_at"]
        constraints = [
            models.UniqueConstraint(fields=["workspace", "item"], name="unique_installation_per_workspace"),
        ]

    def __str__(self):
        return f"{self.item} @ {self.workspace}"

    @property
    def available_version(self):
        return self.item.available_version(self.workspace)

    @property
    def update_available(self):
        return self.available_version != self.installed_version

    @property
    def manifest(self):
        return self.item.manifest_at(self.installed_version)


class ItemVersion(models.Model):
    """Fotografia do manifesto a cada versão salva."""

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="versions")
    version = models.CharField(max_length=20)
    changelog = models.CharField(max_length=300, blank=True)
    manifest = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.item.slug} v{self.version}"


class PublicationRequest(models.Model):
    """Pedido para publicar uma versão no marketplace público, aguardando revisão da equipe."""

    class Status(models.TextChoices):
        PENDING = "pending", "Em revisão"
        APPROVED = "approved", "Aprovado"
        REJECTED = "rejected", "Rejeitado"
        CANCELED = "canceled", "Cancelado"

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="publication_requests")
    version = models.CharField(max_length=20)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="publication_requests",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.item.slug} v{self.version} ({self.status})"


class Review(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True, max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "item"], name="unique_review_per_user"),
        ]

    def __str__(self):
        return f"{self.item.slug}: {self.rating}★ por {self.user}"
