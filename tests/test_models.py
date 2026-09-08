from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.status.models import Incident, IncidentPhase, StatusPage, StatusPageService

from .helpers import create_monitor, create_page_service, create_status_page


DEFAULT_SITE = 'example'
SECOND_SITE = 'another-example'


class StatusModelTests(TestCase):
    def test_status_page_requires_configured_site_slug(self):
        page = StatusPage(site_slug='typo_site', slug='main', title='Typo Status', is_visible=True)

        with self.assertRaises(ValidationError) as error:
            page.full_clean()

        self.assertIn('site_slug', error.exception.message_dict)

    def test_status_page_slug_is_unique_per_site(self):
        create_status_page(site_slug=DEFAULT_SITE, slug='main')

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create_status_page(site_slug=DEFAULT_SITE, slug='main', title='Duplicate Status')

        page = create_status_page(site_slug=SECOND_SITE, slug='main', title='Another Example Status')

        self.assertEqual(page.site_slug, SECOND_SITE)

    def test_page_service_uses_display_override_or_monitor_name(self):
        page = create_status_page()
        monitor = create_monitor(name='Public Web', kuma_monitor_id='public-web', service_key='public-web')
        default_service = StatusPageService(status_page=page, monitor=monitor)
        overridden_service = StatusPageService(status_page=page, monitor=monitor, display_name='Website')

        self.assertEqual(default_service.display_label, 'Public Web')
        self.assertEqual(overridden_service.display_label, 'Website')

    def test_page_service_rejects_unavailable_monitor(self):
        page = create_status_page()
        monitor = create_monitor(is_available=False)
        service = StatusPageService(status_page=page, monitor=monitor)

        with self.assertRaises(ValidationError) as error:
            service.full_clean()

        self.assertIn('monitor', error.exception.message_dict)

    def test_page_service_monitor_is_unique_per_status_page(self):
        page = create_status_page()
        monitor = create_monitor()
        create_page_service(page, monitor=monitor)

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create_page_service(page, monitor=monitor, position=2)

    def test_unresolved_incident_cannot_have_resolved_time(self):
        incident = Incident(
            status_page=create_status_page(),
            title='Investigating outage',
            phase=IncidentPhase.INVESTIGATING,
            resolved_at=timezone.now(),
        )

        with self.assertRaises(ValidationError) as error:
            incident.full_clean()

        self.assertIn('resolved_at', error.exception.message_dict)

    def test_resolved_incident_gets_resolved_time_when_missing(self):
        incident = Incident.objects.create(
            status_page=create_status_page(),
            title='Resolved outage',
            phase=IncidentPhase.RESOLVED,
        )

        self.assertTrue(incident.is_resolved)
        self.assertIsNotNone(incident.resolved_at)
        self.assertEqual(str(incident), 'Resolved outage')
