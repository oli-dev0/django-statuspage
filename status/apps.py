from django.apps import AppConfig


class StatusConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    # This showcase is intended to be copied into a host project's ``apps`` package.
    name = 'apps.status'
    verbose_name = 'Status pages'
