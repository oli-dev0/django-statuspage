from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.sites import get_status_host_for_host, is_status_host, normalize_host, require_site_for_host

from .selectors import build_status_page_context, get_visible_status_page_for_site


def status_page_detail(request, slug=None):
    site = getattr(request, 'site_definition', None) or require_site_for_host(request.get_host())
    if _should_redirect_to_status_host(request) and slug is None:
        status_host = get_status_host_for_host(request.get_host())
        if status_host:
            return redirect(_status_host_url(request, status_host), permanent=True)

    status_page = get_visible_status_page_for_site(site.slug, slug=slug)
    context = build_status_page_context(status_page, request=request)
    context.update(
        {
            'status_site': site,
            'canonical_url': _get_canonical_url(request, status_page),
            'status_meta_description': _get_meta_description(status_page),
            'og_image_url': _absolute_logo_url(request, status_page.logo_url),
            'favicon': None,
        }
    )
    return render(request, 'status/detail.html', context)


def _get_meta_description(status_page):
    if status_page.description:
        return status_page.description
    return _('Current status for %(title)s.') % {'title': status_page.title}


def _get_canonical_url(request, status_page):
    if is_status_host(request.get_host()) and status_page.slug == 'main':
        return request.build_absolute_uri(f'/{request.LANGUAGE_CODE}/')
    if status_page.slug == 'main':
        return request.build_absolute_uri(reverse('status:detail'))
    return request.build_absolute_uri(reverse('status:detail-by-slug', kwargs={'slug': status_page.slug}))


def status_page_redirect(request):
    if not _should_redirect_to_status_host(request):
        return redirect('/en/status/', permanent=True)

    status_host = get_status_host_for_host(request.get_host())
    if status_host is None:
        return redirect('/en/status/', permanent=True)
    return redirect(_status_host_url(request, status_host), permanent=True)


def _should_redirect_to_status_host(request):
    if is_status_host(request.get_host()):
        return False
    return normalize_host(request.get_host()) not in {'localhost', '127.0.0.1', 'testserver'}


def _status_host_url(request, status_host):
    scheme = 'https' if request.is_secure() else request.scheme
    language_code = getattr(request, 'LANGUAGE_CODE', 'en')
    return f'{scheme}://{status_host}/{language_code}/'


def _absolute_logo_url(request, logo_url):
    if not logo_url:
        return ''
    if logo_url.startswith(('http://', 'https://')):
        return logo_url
    return request.build_absolute_uri(logo_url)
