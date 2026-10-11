from django.apps import AppConfig


class WorkspacesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'workspaces'

    def ready(self):
        from django.conf import settings
        from django.contrib.auth.signals import user_logged_in
        from django.db.models.signals import post_save

        from .services import accept_invitations, personal_workspace

        def on_user_saved(sender, instance, created, raw=False, **kwargs):
            if created and not raw:
                personal_workspace(instance)
                accept_invitations(instance)

        def on_login(sender, user, **kwargs):
            accept_invitations(user)

        post_save.connect(on_user_saved, sender=settings.AUTH_USER_MODEL, weak=False)
        user_logged_in.connect(on_login, weak=False)
