from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["name", "workspace", "uploaded_by", "size", "chunk_count", "created_at"]
    search_fields = ["name"]
    readonly_fields = ["file", "size", "char_count", "chunk_count"]
