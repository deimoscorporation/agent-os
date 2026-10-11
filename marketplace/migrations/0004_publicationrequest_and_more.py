# Gerada pelo Django 5.2 e reordenada à mão: os campos novos entram primeiro,
# os dados são copiados (dono -> workspace pessoal) e só então os campos antigos saem.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def move_ownership_to_workspaces(apps, schema_editor):
    Workspace = apps.get_model("workspaces", "Workspace")
    Item = apps.get_model("marketplace", "Item")
    Installation = apps.get_model("marketplace", "Installation")
    personal = {w.owner_id: w for w in Workspace.objects.filter(kind="personal")}

    for item in Item.objects.all():
        if item.owner_id:
            item.workspace = personal[item.owner_id]
            item.created_by_id = item.owner_id
        if item.source == "official" or item.visibility == "public":
            item.approved_version = item.version
        item.save(update_fields=["workspace", "created_by", "approved_version"])

    for installation in Installation.objects.select_related("item"):
        installation.workspace = personal[installation.user_id]
        installation.installed_by_id = installation.user_id
        installation.installed_version = installation.item.version
        installation.save(update_fields=["workspace", "installed_by", "installed_version"])


class Migration(migrations.Migration):

    dependencies = [
        ('marketplace', '0003_alter_item_options_item_category_item_featured_and_more'),
        ('workspaces', '0002_personal_workspaces'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PublicationRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.CharField(max_length=20)),
                ('status', models.CharField(choices=[('pending', 'Em revisão'), ('approved', 'Aprovado'), ('rejected', 'Rejeitado'), ('canceled', 'Cancelado')], db_index=True, default='pending', max_length=16)),
                ('note', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('decided_at', models.DateTimeField(blank=True, null=True)),
                ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='publication_requests', to='marketplace.item')),
                ('requested_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='publication_requests', to=settings.AUTH_USER_MODEL)),
                ('reviewer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.AddField(
            model_name='installation',
            name='installed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='installation',
            name='installed_version',
            field=models.CharField(default='1.0.0', max_length=20),
        ),
        migrations.AddField(
            model_name='installation',
            name='workspace',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='installations', to='workspaces.workspace'),
        ),
        migrations.AddField(
            model_name='item',
            name='approved_version',
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AddField(
            model_name='item',
            name='created_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_items', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='item',
            name='workspace',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='items', to='workspaces.workspace'),
        ),
        migrations.AlterField(
            model_name='item',
            name='visibility',
            field=models.CharField(choices=[('private', 'Privado'), ('organization', 'Organização'), ('public', 'Público')], default='private', max_length=16),
        ),

        migrations.RunPython(move_ownership_to_workspaces, migrations.RunPython.noop),

        migrations.RemoveConstraint(
            model_name='installation',
            name='unique_installation_per_user',
        ),
        migrations.RemoveField(
            model_name='installation',
            name='user',
        ),
        migrations.RemoveField(
            model_name='item',
            name='owner',
        ),
        migrations.AlterField(
            model_name='installation',
            name='workspace',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='installations', to='workspaces.workspace'),
        ),
        migrations.AddConstraint(
            model_name='installation',
            constraint=models.UniqueConstraint(fields=('workspace', 'item'), name='unique_installation_per_workspace'),
        ),
    ]
