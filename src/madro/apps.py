from django.apps import AppConfig


class MadroConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    # The 'name' must match the full python path used in INSTALLED_APPS
    name = 'madro'
    # The 'verbose_name' is how it appears in the Django Admin
    verbose_name = 'MADRO Orchestrator'

    def ready(self):
        """
        Since you are bypassing migrations and managing the DB manually,
        you can use this method to perform any startup checks,
        such as verifying the connection to your custom PostgreSQL schemas.
        """
        pass
