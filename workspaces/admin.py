from django.contrib import admin

from .models import Invitation, Membership, Secret, Workspace


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ["name", "kind", "owner", "created_at"]
    list_filter = ["kind"]
    search_fields = ["name"]
    inlines = [MembershipInline]


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ["email", "workspace", "role", "invited_by", "created_at"]


@admin.register(Secret)
class SecretAdmin(admin.ModelAdmin):
    """O valor fica fora do admin: só nome e metadados."""

    list_display = ["name", "workspace", "updated_by", "updated_at"]
    fields = ["workspace", "name", "description", "updated_by"]
    readonly_fields = ["workspace", "name", "updated_by"]

    def has_add_permission(self, request):
        return False
