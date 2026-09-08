from unittest.mock import patch
from urllib.error import URLError

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.status.kuma import KumaStatusResult, ServiceStatus
from apps.status.models import Incident, IncidentPhase, IncidentSeverity, IncidentUpdate, ServiceState

from .helpers import create_monitor, create_page_service, create_status_page


DEFAULT_SITE = 'example'
SECOND_SITE = 'another-example'
THIRD_SITE = 'third-example'


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


@override_settings(STORAGES=TEST_STORAGES)
class StatusPageViewTests(TestCase):
    def test_public_status_page_renders_noindex_metadata_services_and_empty_history(self):
        page = create_status_page(
            title='Example Status',
            description='Status for Example services.',
            logo_url='https://example.com/logo.png',
            back_link_label='Back to Example',
            back_link_url='https://example.com/',
        )
        visible_service = create_page_service(page, display_name='Website')
        hidden_service = create_page_service(
            page,
            monitor=create_monitor(name='Hidden', kuma_monitor_id='hidden', service_key='hidden'),
            display_name='Internal Tool',
            is_visible=False,
        )
        kuma_result = KumaStatusResult(
            services=(
                ServiceStatus(
                    page_service=visible_service,
                    state=ServiceState.OPERATIONAL,
                    uptime_bars=(),
                    updated_at=timezone.now(),
                ),
            ),
            is_available=True,
            checked_at=timezone.now(),
        )

        with patch('apps.status.kuma.KumaClient.from_settings') as from_settings:
            from_settings.return_value.is_configured = False
            response = self.client.get('/en/status/')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'status/detail.html')
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow">', html=True)
        self.assertContains(response, '<link rel="canonical" href="http://testserver/en/status/">', html=True)
        self.assertContains(response, '<meta property="og:image" content="https://example.com/logo.png">', html=True)
        self.assertContains(response, 'Example Status')
        self.assertContains(response, '<h1>Example Status</h1>', html=True)
        self.assertContains(response, '<h2 id="status-title">Status</h2>', html=True)
        self.assertContains(response, '<h2 id="service-overview-title">Service overview</h2>', html=True)
        self.assertNotContains(response, 'Current status')
        self.assertNotContains(response, 'Appearance')
        self.assertNotContains(response, 'System')
        self.assertContains(response, 'Light')
        self.assertContains(response, 'Dark')
        self.assertContains(response, 'No incidents have been reported yet.')
        self.assertNotContains(response, 'class="status-timestamp"')
        self.assertNotContains(response, 'Status data checked at')
        self.assertContains(response, 'Website')
        self.assertContains(response, 'Recent daily uptime for Website')
        self.assertContains(response, '60 unknown')
        self.assertContains(response, '60 days ago')
        self.assertContains(response, 'today')
        self.assertContains(response, 'data-local-format="date-title"')
        self.assertNotContains(response, 'Internal Tool')
        self.assertNotContains(response, '/admin/')
        self.assertEqual(hidden_service.status_page, page)
        self.assertEqual(len(kuma_result.services), 1)

    @override_settings(STATUS_KUMA_BASE_URL='')
    def test_status_host_language_root_serves_main_status_page(self):
        create_status_page(site_slug=SECOND_SITE, title='Another Example Status')

        response = self.client.get('/en/', HTTP_HOST='status.example.test')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'status/detail.html')
        self.assertContains(response, 'Another Example Status')
        self.assertContains(response, '<link rel="canonical" href="http://status.example.test/en/">', html=True)

    def test_public_status_paths_redirect_to_status_host_language_root(self):
        root_redirects = (
            ('status.example.test', '/en/'),
            ('status.another-example.test', '/en/'),
            ('status.third-example.test', '/en/'),
        )
        for host, expected_url in root_redirects:
            with self.subTest(host=host, path='/'):
                response = self.client.get('/', HTTP_HOST=host)

                self.assertEqual(response.status_code, 302)
                self.assertEqual(response['Location'], expected_url)

        permanent_redirects = (
            ('example.test', '/status/', 'http://status.example.test/en/'),
            ('example.test', '/en/status/', 'http://status.example.test/en/'),
            ('another-example.test', '/status/', 'http://status.another-example.test/en/'),
            ('another-example.test', '/en/status/', 'http://status.another-example.test/en/'),
            ('third-example.test', '/status/', 'http://status.third-example.test/en/'),
            ('third-example.test', '/en/status/', 'http://status.third-example.test/en/'),
        )

        for host, path, expected_url in permanent_redirects:
            with self.subTest(host=host, path=path):
                response = self.client.get(path, HTTP_HOST=host)

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], expected_url)

    def test_local_status_path_redirects_to_local_language_status_path(self):
        for host in ('localhost', '127.0.0.1', 'testserver'):
            with self.subTest(host=host):
                response = self.client.get('/status/', HTTP_HOST=host)

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], '/en/status/')

    def test_missing_invisible_and_wrong_host_status_pages_return_404(self):
        create_status_page(site_slug=DEFAULT_SITE, is_visible=False)
        create_status_page(site_slug=SECOND_SITE, title='Another Example Status')

        self.assertEqual(self.client.get('/en/status/').status_code, 404)
        self.assertEqual(self.client.get('/en/', HTTP_HOST='status.third-example.test').status_code, 404)

    @override_settings(STATUS_KUMA_BASE_URL='')
    def test_status_page_with_no_services_shows_empty_service_state(self):
        create_status_page(title='Empty Status')

        response = self.client.get('/en/status/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2 id="service-overview-title">Service overview</h2>', html=True)
        self.assertContains(response, 'No services are configured for this status page yet.')

    @override_settings(STATUS_KUMA_BASE_URL='')
    def test_slugged_status_page_uses_slugged_canonical(self):
        create_status_page(slug='api', title='API Status')

        response = self.client.get('/en/status/api/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<link rel="canonical" href="http://testserver/en/status/api/">', html=True)

    @override_settings(STATUS_KUMA_BASE_URL='')
    def test_active_and_resolved_incidents_render_public_timeline(self):
        page = create_status_page(title='Incident Status')
        service = create_page_service(page, display_name='Website')
        active_incident = Incident.objects.create(
            status_page=page,
            title='Current outage',
            severity=IncidentSeverity.CRITICAL,
            phase=IncidentPhase.INVESTIGATING,
        )
        active_incident.affected_services.add(service)
        IncidentUpdate.objects.create(
            incident=active_incident,
            phase=IncidentPhase.INVESTIGATING,
            message='We are investigating the outage.',
        )
        resolved_incident = Incident.objects.create(
            status_page=page,
            title='Resolved slowdown',
            severity=IncidentSeverity.MINOR,
            phase=IncidentPhase.RESOLVED,
            summary='The slowdown was resolved.',
        )
        resolved_incident.affected_services.add(service)

        response = self.client.get('/en/status/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Some systems are unavailable')
        self.assertContains(response, 'Current outage')
        self.assertContains(response, 'Critical')
        self.assertContains(response, 'Investigating')
        self.assertContains(response, 'data-local-format="datetime"')
        self.assertContains(response, 'Website')
        self.assertContains(response, 'We are investigating the outage.')
        self.assertContains(response, 'Resolved slowdown')
        self.assertContains(response, 'The slowdown was resolved.')

    @override_settings(STATUS_KUMA_BASE_URL='http://internal-kuma.local')
    def test_kuma_failure_does_not_expose_internal_url_or_raw_error(self):
        page = create_status_page(title='Fallback Status')
        create_page_service(page, display_name='Website')

        with self.assertLogs('apps.status.kuma', level='WARNING'), patch(
            'apps.status.kuma.urlopen',
            side_effect=URLError('internal host hidden'),
        ):
            response = self.client.get('/en/status/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Status information is temporarily unavailable.')
        self.assertContains(response, 'Website')
        self.assertNotContains(response, 'internal-kuma.local')

    @override_settings(STATUS_KUMA_BASE_URL='')
    def test_status_page_is_not_in_sitemap(self):
        create_status_page(title='Noindex Status')

        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '/status/')
