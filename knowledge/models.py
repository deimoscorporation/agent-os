import uuid

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver

from workspaces.models import Workspace

from .extract import extension


def _upload_path(instance, filename):
    # Nome aleatório: o nome original fica só no banco.
    return f"knowledge/{uuid.uuid4().hex}.{extension(filename)}"


class Document(models.Model):
    """Documento da base de conhecimento de um workspace."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="documents")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=200)
    file = models.FileField(upload_to=_upload_path)
    size = models.PositiveIntegerField(default=0)
    char_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    @property
    def extension(self):
        return extension(self.name)


class Chunk(models.Model):
    """Trecho de um documento, a unidade recuperada pela busca."""

    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    position = models.PositiveIntegerField()
    text = models.TextField()

    class Meta:
        ordering = ["document", "position"]

    def __str__(self):
        return f"{self.document.name} #{self.position}"


@receiver(post_delete, sender=Document)
def _delete_document_file(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
