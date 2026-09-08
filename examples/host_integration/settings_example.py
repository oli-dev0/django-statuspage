"""Safe optional Kuma settings for the host project's settings module."""

INSTALLED_APPS += [  # noqa: F821
    'apps.status.apps.StatusConfig',
]

STATUS_KUMA_BASE_URL = 'http://uptime-kuma:3001'
STATUS_KUMA_STATUS_PAGE_SLUG = 'main'
STATUS_KUMA_TIMEOUT_SECONDS = 3
STATUS_KUMA_CACHE_SECONDS = 300
