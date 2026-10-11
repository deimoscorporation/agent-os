from django.conf import settings
from django.db import migrations


def create_personal_workspaces(apps, schema_editor):
    """Contas criadas antes dos workspaces ganham o seu workspace pessoal."""
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Workspace = apps.get_model("workspaces", "Workspace")
    Membership = apps.get_model("workspaces", "Membership")
    for user in User.objects.all():
        workspace, created = Workspace.objects.get_or_create(
            owner=user, defaults={"name": "Pessoal", "kind": "personal"},
        )
        if created:
            Membership.objects.create(workspace=workspace, user=user, role="admin")


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_personal_workspaces, migrations.RunPython.noop),
    ]
