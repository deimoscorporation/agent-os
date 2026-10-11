from django.contrib import admin

from .models import Installation, Item, ItemVersion, PublicationRequest, Review


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "category", "source", "workspace", "visibility", "featured", "version",
                    "approved_version", "install_count", "rating_avg"]
    list_filter = ["kind", "category", "source", "visibility", "featured"]
    list_editable = ["featured"]
    search_fields = ["name", "slug", "summary"]


@admin.register(Installation)
class InstallationAdmin(admin.ModelAdmin):
    list_display = ["item", "workspace", "installed_version", "enabled", "installed_at"]
    list_filter = ["enabled", "item__kind"]


@admin.register(PublicationRequest)
class PublicationRequestAdmin(admin.ModelAdmin):
    list_display = ["item", "version", "status", "requested_by", "reviewer", "created_at", "decided_at"]
    list_filter = ["status"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["item", "user", "rating", "updated_at"]
    list_filter = ["rating"]


@admin.register(ItemVersion)
class ItemVersionAdmin(admin.ModelAdmin):
    list_display = ["item", "version", "changelog", "created_at"]
