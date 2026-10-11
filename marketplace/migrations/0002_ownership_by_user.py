import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def purge_session_data(apps, schema_editor):
    # Instalações e itens de usuário eram ligados à sessão do navegador e não têm dono.
    # Os itens oficiais são recriados automaticamente após o migrate.
    apps.get_model("marketplace", "Installation").objects.all().delete()
    apps.get_model("marketplace", "Item").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("marketplace", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(purge_session_data, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="installation", name="unique_installation_per_workspace"),
        migrations.RemoveField(model_name="installation", name="owner_key"),
        migrations.RemoveField(model_name="item", name="owner_key"),
        migrations.AddField(
            model_name="installation",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="installations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="item",
            name="owner",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="items",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name="installation",
            constraint=models.UniqueConstraint(fields=("user", "item"), name="unique_installation_per_user"),
        ),
    ]
