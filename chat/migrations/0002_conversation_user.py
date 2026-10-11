import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def purge_anonymous_conversations(apps, schema_editor):
    # Conversas antigas eram ligadas à sessão do navegador e não têm dono.
    apps.get_model("chat", "Conversation").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(purge_anonymous_conversations, migrations.RunPython.noop),
        migrations.RemoveField(model_name="conversation", name="owner_key"),
        migrations.AddField(
            model_name="conversation",
            name="user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="conversations",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
