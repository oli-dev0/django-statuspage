from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta

from django.db.models import Prefetch, Q
from django.http import Http404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .kuma import UptimeBar, get_service_statuses
from .models import (
    Incident,
    IncidentPhase,
    IncidentSeverity,
    IncidentUpdate,
    OverallStatus,
    ServiceState,
    StatusPage,
    StatusPageService,
)


RESOLVED_INCIDENT_LIMIT = 20
SEVERITY_RANK = {
    IncidentSeverity.CRITICAL: 3,
    IncidentSeverity.MAJOR: 2,
    IncidentSeverity.MINOR: 1,
}
INCIDENT_SEVERITY_BAR_STATE = {
    IncidentSeverity.MINOR: 'minor',
    IncidentSeverity.MAJOR: 'major',
    IncidentSeverity.CRITICAL: ServiceState.DOWN,
}
INCIDENT_TOOLTIP_TITLE_LENGTH = 44


@dataclass(frozen=True)
class OverallStatusSummary:
    state: str
    label: str
    detail: str


def get_visible_status_page_for_site(site_slug, slug=None):
    page_slug = slug or 'main'
    try:
        return StatusPage.objects.get(site_slug=site_slug, slug=page_slug, is_visible=True)
    except StatusPage.DoesNotExist as exc:
        raise Http404('Status page not found.') from exc


def get_status_page_services(status_page):
    return (
        StatusPageService.objects
        .filter(status_page=status_page, is_visible=True, monitor__is_available=True)
        .select_related('status_page', 'monitor')
        .order_by('position', 'monitor__name')
    )


def get_active_incidents(status_page):
    incidents = _incident_queryset(status_page).exclude(phase=IncidentPhase.RESOLVED)
    return sorted(
        incidents,
        key=lambda incident: (SEVERITY_RANK.get(incident.severity, 0), incident.started_at),
        reverse=True,
    )


def get_resolved_incidents(status_page, limit=RESOLVED_INCIDENT_LIMIT):
    return list(
        _incident_queryset(status_page)
        .filter(phase=IncidentPhase.RESOLVED)
        .order_by('-resolved_at', '-started_at')[:limit]
    )


def build_status_page_context(status_page, request=None):
    page_services = tuple(get_status_page_services(status_page))
    kuma_result = get_service_statuses(page_services)
    service_statuses = apply_incident_uptime_overlays(status_page, kuma_result.services)
    active_incidents = get_active_incidents(status_page)
    resolved_incidents = get_resolved_incidents(status_page)

    return {
        'status_page': status_page,
        'service_statuses': service_statuses,
        'kuma_available': kuma_result.is_available,
        'status_checked_at': kuma_result.checked_at,
        'overall_status': get_overall_status(active_incidents, service_statuses),
        'active_incidents': active_incidents,
        'resolved_incidents': resolved_incidents,
    }


def get_overall_status(active_incidents, service_statuses):
    if not service_statuses:
        return OverallStatusSummary(
            state=OverallStatus.EMPTY,
            label=OverallStatus.EMPTY.label,
            detail=_('No services are configured for this status page yet.'),
        )

    if active_incidents:
        # Business precedence: manual active incidents are operator-authored and must outrank
        # transient Kuma health, with critical incidents taking the strongest headline.
        strongest = max((incident.severity for incident in active_incidents), key=lambda severity: SEVERITY_RANK[severity])
        if strongest == IncidentSeverity.CRITICAL:
            return OverallStatusSummary(OverallStatus.DOWN, OverallStatus.DOWN.label, _('A critical incident is active.'))
        return OverallStatusSummary(OverallStatus.DEGRADED, OverallStatus.DEGRADED.label, _('An incident is active.'))

    states = {service.state for service in service_statuses}
    if ServiceState.DOWN in states:
        return OverallStatusSummary(OverallStatus.DOWN, OverallStatus.DOWN.label, _('At least one service is unavailable.'))
    if ServiceState.DEGRADED in states:
        return OverallStatusSummary(OverallStatus.DEGRADED, OverallStatus.DEGRADED.label, _('At least one service is degraded.'))
    if ServiceState.UNKNOWN in states:
        return OverallStatusSummary(OverallStatus.UNKNOWN, OverallStatus.UNKNOWN.label, _('Live service data could not be loaded.'))
    return OverallStatusSummary(OverallStatus.OPERATIONAL, OverallStatus.OPERATIONAL.label, _('All selected services look healthy.'))


def apply_incident_uptime_overlays(status_page, service_statuses):
    service_statuses = tuple(service_statuses)
    if not service_statuses:
        return service_statuses

    bar_dates = _service_bar_dates(service_statuses)
    if not bar_dates:
        return service_statuses

    service_ids = {service.page_service.pk for service in service_statuses}
    incident_details = _incident_details_by_service_and_day(status_page, service_ids, bar_dates)
    if not incident_details:
        return service_statuses

    overlaid_statuses = []
    for service in service_statuses:
        overlaid_bars = []
        for bar in service.uptime_bars:
            incident_detail = incident_details.get((service.page_service.pk, bar.date))
            if incident_detail is None:
                overlaid_bars.append(bar)
                continue
            severity = incident_detail['severity']
            overlaid_bars.append(
                UptimeBar(
                    label=bar.label,
                    state=INCIDENT_SEVERITY_BAR_STATE[severity],
                    date=bar.date,
                    value=bar.value,
                    source='incident',
                    severity=severity,
                    tooltip_lines=bar.tooltip_lines + tuple(incident_detail['titles']),
                )
            )
        overlaid_statuses.append(replace(service, uptime_bars=tuple(overlaid_bars)))
    return tuple(overlaid_statuses)


def _incident_queryset(status_page):
    update_queryset = IncidentUpdate.objects.order_by('published_at', 'created_at')
    affected_services_queryset = (
        StatusPageService.objects
        .filter(status_page=status_page, is_visible=True, monitor__is_available=True)
        .select_related('monitor')
        .order_by('position', 'monitor__name')
    )
    return (
        Incident.objects
        .filter(status_page=status_page)
        .prefetch_related(
            Prefetch('affected_services', queryset=affected_services_queryset),
            Prefetch('updates', queryset=update_queryset),
        )
    )


def _service_bar_dates(service_statuses):
    return {
        bar.date
        for service in service_statuses
        for bar in service.uptime_bars
        if bar.date is not None
    }


def _incident_details_by_service_and_day(status_page, service_ids, bar_dates):
    first_day = min(bar_dates)
    last_day = max(bar_dates)
    window_start = timezone.make_aware(datetime.combine(first_day, time.min), timezone.get_current_timezone())
    window_end = timezone.make_aware(
        datetime.combine(last_day + timedelta(days=1), time.min),
        timezone.get_current_timezone(),
    )
    affected_services_queryset = StatusPageService.objects.filter(status_page=status_page, is_visible=True)
    incidents = (
        Incident.objects
        .filter(status_page=status_page, started_at__lt=window_end)
        .filter(Q(resolved_at__isnull=True) | Q(resolved_at__gte=window_start))
        .prefetch_related(Prefetch('affected_services', queryset=affected_services_queryset))
        .order_by('started_at', 'pk')
    )

    details = {}
    for incident in incidents:
        affected_service_ids = {service.pk for service in incident.affected_services.all()}
        if not affected_service_ids:
            affected_service_ids = service_ids
        affected_service_ids &= service_ids
        if not affected_service_ids:
            continue

        for day in _incident_days(incident, first_day, last_day):
            for service_id in affected_service_ids:
                key = (service_id, day)
                detail = details.setdefault(key, {'severity': incident.severity, 'titles': []})
                if SEVERITY_RANK[incident.severity] > SEVERITY_RANK[detail['severity']]:
                    detail['severity'] = incident.severity
                detail['titles'].append(_truncate_incident_title(incident.title))
    return details


def _truncate_incident_title(title):
    title = str(title).strip()
    if len(title) <= INCIDENT_TOOLTIP_TITLE_LENGTH:
        return title
    truncated = title[:INCIDENT_TOOLTIP_TITLE_LENGTH - 3].rstrip()
    word_boundary = truncated.rfind(' ')
    if word_boundary > 0:
        truncated = truncated[:word_boundary]
    return f'{truncated}...'


def _incident_days(incident, first_day, last_day):
    started_day = timezone.localtime(incident.started_at).date()
    resolved_day = timezone.localtime(incident.resolved_at).date() if incident.resolved_at else last_day
    start = max(started_day, first_day)
    end = min(resolved_day, last_day)
    if start > end:
        return ()
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))
