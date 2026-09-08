from dataclasses import dataclass
from unittest.mock import patch

from django.http import Http404
from django.test import TestCase
from django.utils import timezone

from apps.status.kuma import KumaStatusResult, ServiceStatus, UptimeBar
from apps.status.models import Incident, IncidentPhase, IncidentSeverity, IncidentUpdate, OverallStatus, ServiceState
from apps.status.selectors import (
    apply_incident_uptime_overlays,
    build_status_page_context,
    get_active_incidents,
    get_overall_status,
    get_resolved_incidents,
    get_status_page_services,
    get_visible_status_page_for_site,
)

from .helpers import create_monitor, create_page_service, create_status_page


DEFAULT_SITE = 'example'
SECOND_SITE = 'another-example'


@dataclass(frozen=True)
class ServiceStateStub:
    state: str


class StatusSelectorTests(TestCase):
    def test_visible_status_page_resolution_is_scoped_by_site_and_slug(self):
        page = create_status_page(site_slug=DEFAULT_SITE, slug='main')
        create_status_page(site_slug=SECOND_SITE, slug='main', title='Another Example Status')

        self.assertEqual(get_visible_status_page_for_site(DEFAULT_SITE), page)

        with self.assertRaises(Http404):
            get_visible_status_page_for_site(DEFAULT_SITE, slug='missing')

    def test_invisible_status_page_is_not_publicly_resolved(self):
        create_status_page(is_visible=False)

        with self.assertRaises(Http404):
            get_visible_status_page_for_site(DEFAULT_SITE)

    def test_status_page_services_are_visible_available_and_ordered(self):
        page = create_status_page()
        third = create_page_service(
            page,
            monitor=create_monitor(name='Third', kuma_monitor_id='third', service_key='third'),
            position=30,
        )
        first = create_page_service(
            page,
            monitor=create_monitor(name='First', kuma_monitor_id='first', service_key='first'),
            position=10,
        )
        hidden = create_page_service(
            page,
            monitor=create_monitor(name='Hidden', kuma_monitor_id='hidden', service_key='hidden'),
            position=20,
            is_visible=False,
        )
        unavailable = create_page_service(
            page,
            monitor=create_monitor(
                name='Unavailable',
                kuma_monitor_id='unavailable',
                service_key='unavailable',
                is_available=False,
            ),
            position=5,
        )

        services = list(get_status_page_services(page))

        self.assertEqual(services, [first, third])
        self.assertNotIn(hidden, services)
        self.assertNotIn(unavailable, services)

    def test_active_incidents_order_by_severity_then_newest(self):
        page = create_status_page()
        older_critical = Incident.objects.create(
            status_page=page,
            title='Older critical',
            severity=IncidentSeverity.CRITICAL,
            phase=IncidentPhase.INVESTIGATING,
            started_at=timezone.now() - timezone.timedelta(hours=2),
        )
        newer_critical = Incident.objects.create(
            status_page=page,
            title='Newer critical',
            severity=IncidentSeverity.CRITICAL,
            phase=IncidentPhase.IDENTIFIED,
            started_at=timezone.now(),
        )
        major = Incident.objects.create(
            status_page=page,
            title='Major',
            severity=IncidentSeverity.MAJOR,
            phase=IncidentPhase.MONITORING,
        )
        Incident.objects.create(
            status_page=page,
            title='Resolved',
            severity=IncidentSeverity.CRITICAL,
            phase=IncidentPhase.RESOLVED,
        )

        incidents = get_active_incidents(page)

        self.assertEqual(incidents, [newer_critical, older_critical, major])

    def test_resolved_incidents_are_newest_resolved_first_and_limited(self):
        page = create_status_page()
        older = Incident.objects.create(
            status_page=page,
            title='Older resolved',
            phase=IncidentPhase.RESOLVED,
            resolved_at=timezone.now() - timezone.timedelta(days=2),
        )
        newer = Incident.objects.create(
            status_page=page,
            title='Newer resolved',
            phase=IncidentPhase.RESOLVED,
            resolved_at=timezone.now(),
        )

        incidents = get_resolved_incidents(page, limit=1)

        self.assertEqual(incidents, [newer])
        self.assertNotIn(older, incidents)

    def test_overall_status_precedence_uses_incidents_before_service_health(self):
        active_major = Incident(
            status_page=create_status_page(),
            title='Major incident',
            severity=IncidentSeverity.MAJOR,
            phase=IncidentPhase.INVESTIGATING,
        )
        active_critical = Incident(
            status_page=create_status_page(slug='critical', title='Critical Status'),
            title='Critical incident',
            severity=IncidentSeverity.CRITICAL,
            phase=IncidentPhase.INVESTIGATING,
        )

        self.assertEqual(get_overall_status([], []).state, OverallStatus.EMPTY)
        self.assertEqual(
            get_overall_status([], [ServiceStateStub(ServiceState.DOWN)]).state,
            OverallStatus.DOWN,
        )
        self.assertEqual(
            get_overall_status([active_major], [ServiceStateStub(ServiceState.OPERATIONAL)]).state,
            OverallStatus.DEGRADED,
        )
        self.assertEqual(
            get_overall_status([active_critical], [ServiceStateStub(ServiceState.OPERATIONAL)]).state,
            OverallStatus.DOWN,
        )

    def test_build_status_page_context_filters_incident_affected_services_for_public_display(self):
        page = create_status_page()
        visible_service = create_page_service(page, display_name='Visible Website')
        hidden_service = create_page_service(
            page,
            monitor=create_monitor(name='Hidden', kuma_monitor_id='hidden', service_key='hidden'),
            display_name='Hidden Internal Service',
            is_visible=False,
        )
        incident = Incident.objects.create(
            status_page=page,
            title='Visible incident',
            severity=IncidentSeverity.MINOR,
            phase=IncidentPhase.INVESTIGATING,
        )
        incident.affected_services.add(visible_service, hidden_service)
        IncidentUpdate.objects.create(incident=incident, phase=IncidentPhase.INVESTIGATING, message='Investigating.')
        kuma_result = KumaStatusResult(
            services=(
                ServiceStatus(
                    page_service=visible_service,
                    state=ServiceState.OPERATIONAL,
                    uptime_bars=(),
                ),
            ),
            is_available=True,
            checked_at=timezone.now(),
        )

        with patch('apps.status.selectors.get_service_statuses', return_value=kuma_result):
            context = build_status_page_context(page)

        public_incident = context['active_incidents'][0]
        self.assertEqual(list(public_incident.affected_services.all()), [visible_service])

    def test_incident_uptime_overlay_colors_only_affected_service_day(self):
        page = create_status_page()
        affected_service = create_page_service(page, display_name='Affected')
        unaffected_service = create_page_service(
            page,
            monitor=create_monitor(name='Unaffected', kuma_monitor_id='unaffected', service_key='unaffected'),
            display_name='Unaffected',
        )
        today = timezone.localdate()
        incident = Incident.objects.create(
            status_page=page,
            title='Major incident with a deliberately long title that needs truncating',
            severity=IncidentSeverity.MAJOR,
            phase=IncidentPhase.RESOLVED,
            started_at=timezone.now(),
            resolved_at=timezone.now(),
        )
        incident.affected_services.add(affected_service)
        services = (
            ServiceStatus(
                page_service=affected_service,
                state=ServiceState.OPERATIONAL,
                uptime_bars=(UptimeBar(label=today.isoformat(), state=ServiceState.OPERATIONAL, date=today),),
            ),
            ServiceStatus(
                page_service=unaffected_service,
                state=ServiceState.OPERATIONAL,
                uptime_bars=(UptimeBar(label=today.isoformat(), state=ServiceState.OPERATIONAL, date=today),),
            ),
        )

        overlaid = apply_incident_uptime_overlays(page, services)

        self.assertEqual(overlaid[0].uptime_bars[0].state, 'major')
        self.assertEqual(overlaid[0].uptime_bars[0].source, 'incident')
        self.assertEqual(
            overlaid[0].uptime_bars[0].tooltip_lines,
            ('Major incident with a deliberately long...',),
        )
        self.assertEqual(overlaid[1].uptime_bars[0].state, ServiceState.OPERATIONAL)

    def test_incident_uptime_overlay_treats_incidents_without_services_as_page_wide(self):
        page = create_status_page()
        first_service = create_page_service(page, display_name='First')
        second_service = create_page_service(
            page,
            monitor=create_monitor(name='Second', kuma_monitor_id='second', service_key='second'),
            display_name='Second',
        )
        today = timezone.localdate()
        Incident.objects.create(
            status_page=page,
            title='Minor incident',
            severity=IncidentSeverity.MINOR,
            phase=IncidentPhase.RESOLVED,
            started_at=timezone.now(),
            resolved_at=timezone.now(),
        )
        services = (
            ServiceStatus(
                page_service=first_service,
                state=ServiceState.OPERATIONAL,
                uptime_bars=(UptimeBar(label=today.isoformat(), state=ServiceState.OPERATIONAL, date=today),),
            ),
            ServiceStatus(
                page_service=second_service,
                state=ServiceState.OPERATIONAL,
                uptime_bars=(UptimeBar(label=today.isoformat(), state=ServiceState.OPERATIONAL, date=today),),
            ),
        )

        overlaid = apply_incident_uptime_overlays(page, services)

        self.assertEqual(overlaid[0].uptime_bars[0].state, 'minor')
        self.assertEqual(overlaid[1].uptime_bars[0].state, 'minor')

    def test_incident_uptime_overlay_uses_highest_severity_for_day(self):
        page = create_status_page()
        service = create_page_service(page, display_name='Website')
        today = timezone.localdate()
        minor = Incident.objects.create(
            status_page=page,
            title='Minor incident',
            severity=IncidentSeverity.MINOR,
            phase=IncidentPhase.RESOLVED,
            started_at=timezone.now(),
            resolved_at=timezone.now(),
        )
        critical = Incident.objects.create(
            status_page=page,
            title='Critical incident',
            severity=IncidentSeverity.CRITICAL,
            phase=IncidentPhase.RESOLVED,
            started_at=timezone.now(),
            resolved_at=timezone.now(),
        )
        minor.affected_services.add(service)
        critical.affected_services.add(service)
        services = (
            ServiceStatus(
                page_service=service,
                state=ServiceState.OPERATIONAL,
                uptime_bars=(UptimeBar(label=today.isoformat(), state=ServiceState.DEGRADED, date=today),),
            ),
        )

        overlaid = apply_incident_uptime_overlays(page, services)

        self.assertEqual(overlaid[0].uptime_bars[0].state, ServiceState.DOWN)
        self.assertEqual(overlaid[0].uptime_bars[0].severity, IncidentSeverity.CRITICAL)
        self.assertEqual(
            overlaid[0].uptime_bars[0].tooltip_lines,
            ('Minor incident', 'Critical incident'),
        )
