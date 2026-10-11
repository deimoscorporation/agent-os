from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_catalog(sender, apps, **kwargs):
    from .catalog import sync_official_items

    try:
        Item = apps.get_model("marketplace", "Item")
        ItemVersion = apps.get_model("marketplace", "ItemVersion")
    except LookupError:
        return
    sync_official_items(Item, ItemVersion)


class MarketplaceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'marketplace'

    def ready(self):
        post_migrate.connect(_sync_catalog, sender=self)
