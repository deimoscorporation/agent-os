import uuid

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from knowledge.extract import IMAGE_EXTENSIONS, extension


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=120, default="Nova conversa")
    # Agente do marketplace que responde nesta conversa; vazio = assistente padrão.
    agent = models.ForeignKey(
        "marketplace.Item", null=True, blank=True, on_delete=models.SET_NULL, related_name="conversations",
    )
    # Modelo escolhido quando não há agente (o agente traz o seu próprio).
    model = models.CharField(max_length=64, blank=True)
    # Consultar a base de conhecimento do workspace a cada pergunta.
    use_knowledge = models.BooleanField(default=False)
    # Preenchido enquanto existir um link público de leitura.
    share_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title

    def as_dict(self):
        return {
            "id": str(self.id),
            "title": self.title,
            "updated_at": self.updated_at.isoformat(),
            "agent": {"id": str(self.agent_id), "name": self.agent.name} if self.agent_id else None,
            "model": self.model,
            "use_knowledge": self.use_knowledge,
            "shared": bool(self.share_token),
        }


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "Usuário"
        ASSISTANT = "assistant", "Assistente"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField(blank=True)
    # Trechos da base de conhecimento usados na resposta: [{"document_id", "document", "excerpt"}]
    sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.role}: {self.content[:40]}"

    def as_dict(self):
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "sources": self.sources,
            "attachments": [a.as_dict() for a in self.attachments.all()],
            "created_at": self.created_at.isoformat(),
        }


def _upload_path(instance, filename):
    # Nome aleatório: o nome original fica só no banco.
    return f"attachments/{uuid.uuid4().hex}.{extension(filename)}"


class Attachment(models.Model):
    """Arquivo ou imagem enviado no chat. Fica sem mensagem até o envio ser concluído."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(Message, null=True, blank=True, on_delete=models.CASCADE, related_name="attachments")
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to=_upload_path)
    size = models.PositiveIntegerField(default=0)
    # Texto extraído (TXT, PDF, DOCX…) para o modelo ler; vazio em imagens.
    text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return self.name

    @property
    def is_image(self):
        return extension(self.name) in IMAGE_EXTENSIONS

    def as_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "size": self.size,
            "kind": "image" if self.is_image else "file",
            "url": f"/attachments/{self.id}/",
        }


@receiver(post_delete, sender=Attachment)
def _delete_attachment_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
