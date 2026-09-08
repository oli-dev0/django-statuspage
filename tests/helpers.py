from apps.status.models import KumaMonitor, StatusPage, StatusPageService


DEFAULT_SITE = 'example'


def create_status_page(
    *,
    site_slug=DEFAULT_SITE,
    slug='main',
    title='Product Status',
    description='Current product availability.',
    is_visible=True,
    **kwargs,
):
    return StatusPage.objects.create(
        site_slug=site_slug,
        slug=slug,
        title=title,
        description=description,
        is_visible=is_visible,
        **kwargs,
    )


def create_monitor(
    *,
    name='Web app',
    kuma_monitor_id='web-app',
    service_key='web-app',
    is_available=True,
    **kwargs,
):
    return KumaMonitor.objects.create(
        name=name,
        kuma_monitor_id=kuma_monitor_id,
        service_key=service_key,
        is_available=is_available,
        **kwargs,
    )


def create_page_service(
    status_page,
    *,
    monitor=None,
    display_name='',
    position=0,
    is_visible=True,
):
    monitor = monitor or create_monitor()
    return StatusPageService.objects.create(
        status_page=status_page,
        monitor=monitor,
        display_name=display_name,
        position=position,
        is_visible=is_visible,
    )
