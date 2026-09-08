import json
import logging
from dataclasses import dataclass
from datetime import date as dt_date
from datetime import datetime, time, timedelta, timezone as dt_timezone
from hashlib import sha256
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.template.defaultfilters import slugify
from django.utils.dateparse import parse_date, parse_datetime
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import KumaMonitor, KumaMonitorDailyUptime, ServiceState, StatusPageService


logger = logging.getLogger(__name__)

DEFAULT_BAR_COUNT = 60
DEFAULT_CACHE_SECONDS = 300
CACHE_KEY_PREFIX = 'status:kuma-monitor'
DAILY_DEGRADED_DOWN_STREAK = 2
DEGRADED_PERFORMANCE_TOOLTIP = _('Degraded performance')


@dataclass(frozen=True)
class UptimeBar:
    label: str
    state: str
    date: dt_date | None = None
    value: float | None = None
    source: str = 'kuma'
    severity: str = ''
    tooltip_lines: tuple[str, ...] = ()

    @property
    def tooltip_body(self):
        return '\n'.join(str(line) for line in self.tooltip_lines if line)


@dataclass(frozen=True)
class ServiceStatus:
    page_service: StatusPageService
    state: str
    uptime_bars: tuple[UptimeBar, ...]
    updated_at: object | None = None

    @property
    def label(self):
        return self.page_service.display_label

    @property
    def state_label(self):
        return ServiceState(self.state).label

    @property
    def uptime_summary(self):
        counts = {
            ServiceState.OPERATIONAL: 0,
            ServiceState.DEGRADED: 0,
            ServiceState.DOWN: 0,
            ServiceState.UNKNOWN: 0,
            'minor': 0,
            'major': 0,
        }
        for bar in self.uptime_bars:
            counts[bar.state] = counts.get(bar.state, 0) + 1

        return _(
            'Recent daily uptime for %(service)s: %(operational)d operational, %(degraded)d degraded, '
            '%(down)d unavailable, %(minor)d minor incident, %(major)d major incident, %(unknown)d unknown.'
        ) % {
            'service': self.label,
            'operational': counts[ServiceState.OPERATIONAL],
            'degraded': counts[ServiceState.DEGRADED],
            'down': counts[ServiceState.DOWN],
            'minor': counts['minor'],
            'major': counts['major'],
            'unknown': counts[ServiceState.UNKNOWN],
        }


@dataclass(frozen=True)
class KumaStatusResult:
    services: tuple[ServiceStatus, ...]
    is_available: bool
    checked_at: object | None


@dataclass(frozen=True)
class KumaMonitorData:
    kuma_monitor_id: str
    name: str
    service_key: str
    kuma_monitor_type: str = ''
    description: str = ''


@dataclass(frozen=True)
class KumaMonitorSyncResult:
    fetched: int
    added: int
    updated: int
    deleted: int


@dataclass(frozen=True)
class KumaUptimeSyncResult:
    monitors_seen: int
    monitors_matched: int
    heartbeats_seen: int
    heartbeats_ingested: int
    daily_rows_updated: int
    warnings: tuple[str, ...] = ()


def get_service_statuses(page_services):
    page_services = tuple(page_services)
    if not page_services:
        return KumaStatusResult(services=(), is_available=True, checked_at=timezone.now())

    client = KumaClient.from_settings()
    if not client.is_configured:
        return _fallback_result(page_services)

    try:
        heartbeat_payload = _get_cached_status_page_heartbeats(client)
    except KumaClientError:
        return _fallback_result(page_services)

    statuses = [
        _service_status_from_status_page_payload(page_service, heartbeat_payload)
        for page_service in page_services
    ]
    checked_at = timezone.now()

    return KumaStatusResult(
        services=tuple(statuses),
        is_available=True,
        checked_at=checked_at,
    )


def sync_kuma_daily_uptime_from_status_page():
    client = KumaClient.from_settings()
    if not client.is_configured:
        raise KumaClientError('Kuma base URL is not configured.')

    payload = client.get_status_page_heartbeats()
    return ingest_kuma_daily_uptime_payload(payload)


def ingest_kuma_daily_uptime_payload(payload):
    if not isinstance(payload, dict):
        raise KumaClientError('Kuma heartbeat payload is invalid.')

    heartbeat_list = payload.get('heartbeatList')
    if not isinstance(heartbeat_list, dict):
        raise KumaClientError('Kuma heartbeat payload is missing heartbeatList.')

    monitors_by_kuma_id = {
        monitor.kuma_monitor_id: monitor
        for monitor in KumaMonitor.objects.filter(is_available=True)
    }
    monitor_ids = set(heartbeat_list)
    latest_by_monitor = _latest_daily_uptime_by_monitor(monitors_by_kuma_id.values())
    heartbeats_seen = 0
    heartbeats_ingested = 0
    updated_keys = set()
    warnings = []

    with transaction.atomic():
        for kuma_monitor_id, raw_heartbeats in heartbeat_list.items():
            if not isinstance(raw_heartbeats, list):
                continue

            heartbeats_seen += len(raw_heartbeats)
            monitor = monitors_by_kuma_id.get(str(kuma_monitor_id))
            if monitor is None:
                continue

            latest = latest_by_monitor.get(monitor.pk)
            known_cutoff = latest.last_heartbeat_at if latest is not None else None
            sorted_heartbeats = sorted(raw_heartbeats, key=lambda item: _heartbeat_datetime(item) or timezone.now())

            if known_cutoff and sorted_heartbeats:
                oldest_payload_at = _heartbeat_datetime(sorted_heartbeats[0])
                if oldest_payload_at and oldest_payload_at > known_cutoff:
                    warnings.append(
                        f'Kuma heartbeat history for monitor {monitor.kuma_monitor_id} has a gap before '
                        f'{oldest_payload_at.isoformat()}.'
                    )

            for heartbeat in sorted_heartbeats:
                heartbeat_at = _heartbeat_datetime(heartbeat)
                if heartbeat_at is None:
                    continue
                if known_cutoff and heartbeat_at <= known_cutoff:
                    continue

                row = _update_daily_uptime_from_heartbeat(monitor, heartbeat, heartbeat_at)
                latest_by_monitor[monitor.pk] = row
                known_cutoff = row.last_heartbeat_at
                heartbeats_ingested += 1
                updated_keys.add((monitor.pk, row.day))

    return KumaUptimeSyncResult(
        monitors_seen=len(monitor_ids),
        monitors_matched=len(monitor_ids & set(monitors_by_kuma_id)),
        heartbeats_seen=heartbeats_seen,
        heartbeats_ingested=heartbeats_ingested,
        daily_rows_updated=len(updated_keys),
        warnings=tuple(warnings),
    )


def sync_kuma_monitors_from_status_page():
    client = KumaClient.from_settings()
    if not client.is_configured:
        raise KumaClientError('Kuma base URL is not configured.')

    payload = client.get_status_page()
    monitor_data = tuple(_extract_status_page_monitors(payload))
    source_ids = {monitor.kuma_monitor_id for monitor in monitor_data}

    with transaction.atomic():
        existing_monitors = {
            monitor.kuma_monitor_id: monitor
            for monitor in KumaMonitor.objects.select_for_update()
        }
        stale_monitors = KumaMonitor.objects.exclude(kuma_monitor_id__in=source_ids)
        deleted = stale_monitors.count()
        if deleted:
            StatusPageService.objects.filter(monitor__in=stale_monitors).delete()
            stale_monitors.delete()

        added = 0
        updated = 0
        for monitor in monitor_data:
            existing = existing_monitors.get(monitor.kuma_monitor_id)
            if existing is None:
                KumaMonitor.objects.create(
                    name=monitor.name,
                    kuma_monitor_id=monitor.kuma_monitor_id,
                    kuma_monitor_type=monitor.kuma_monitor_type,
                    service_key=monitor.service_key,
                    description=monitor.description,
                    is_available=True,
                )
                added += 1
                continue

            changed_fields = []
            if existing.name != monitor.name:
                existing.name = monitor.name
                changed_fields.append('name')
            if existing.service_key != monitor.service_key:
                existing.service_key = monitor.service_key
                changed_fields.append('service_key')
            if existing.kuma_monitor_type != monitor.kuma_monitor_type:
                existing.kuma_monitor_type = monitor.kuma_monitor_type
                changed_fields.append('kuma_monitor_type')
            if not existing.is_available:
                existing.is_available = True
                changed_fields.append('is_available')

            if changed_fields:
                changed_fields.append('updated_at')
                existing.save(update_fields=changed_fields)
                updated += 1

    cache.delete(_status_page_cache_key(client.status_page_slug, 'metadata'))
    cache.delete(_status_page_cache_key(client.status_page_slug, 'heartbeats'))
    return KumaMonitorSyncResult(
        fetched=len(monitor_data),
        added=added,
        updated=updated,
        deleted=deleted,
    )


class KumaClientError(Exception):
    pass


class KumaClient:
    def __init__(self, base_url, timeout=3, status_page_slug='main'):
        self.base_url = base_url.rstrip('/') + '/' if base_url else ''
        self.timeout = timeout
        self.status_page_slug = status_page_slug or 'main'

    @classmethod
    def from_settings(cls):
        return cls(
            base_url=getattr(settings, 'STATUS_KUMA_BASE_URL', ''),
            timeout=getattr(settings, 'STATUS_KUMA_TIMEOUT_SECONDS', 3),
            status_page_slug=getattr(settings, 'STATUS_KUMA_STATUS_PAGE_SLUG', 'main'),
        )

    @property
    def is_configured(self):
        return bool(self.base_url)

    def get_status_page(self):
        return self._get_json(f'/api/status-page/{self.status_page_slug}')

    def get_status_page_heartbeats(self):
        return self._get_json(f'/api/status-page/heartbeat/{self.status_page_slug}')

    def _get_json(self, path):
        url = urljoin(self.base_url, path.lstrip('/'))
        headers = {'Accept': 'application/json'}
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            logger.warning('Kuma status request failed for configured endpoint: %s', exc.__class__.__name__)
            raise KumaClientError(
                'Kuma status endpoint could not be reached. Check STATUS_KUMA_BASE_URL and network access.'
            ) from exc

        try:
            return json.loads(payload.decode('utf-8'))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning('Kuma status endpoint returned unreadable JSON.')
            raise KumaClientError('Kuma status endpoint returned unreadable JSON.') from exc


def normalize_service_state(payload):
    raw_value = _find_first_value(payload, ('state', 'status', 'monitor_status'))
    if raw_value is None and isinstance(payload, dict):
        heartbeat = payload.get('heartbeat') or payload.get('latest_heartbeat')
        if isinstance(heartbeat, dict):
            raw_value = heartbeat.get('status')
            if raw_value is None:
                raw_value = heartbeat.get('state')

    value = str(raw_value).strip().lower()
    if value in {'1', 'up', 'ok', 'online', 'operational', 'healthy'}:
        return ServiceState.OPERATIONAL
    if value in {'2', 'degraded', 'pending', 'warning'}:
        return ServiceState.DEGRADED
    if value in {'0', 'down', 'offline', 'failed', 'error', 'unavailable'}:
        return ServiceState.DOWN
    return ServiceState.UNKNOWN


def normalize_uptime_bars(payload, *, count=DEFAULT_BAR_COUNT):
    raw_items = _extract_uptime_items(payload)
    return _build_daily_uptime_bars(raw_items, count=count)


def _get_cached_status_page_heartbeats(client):
    cache_key = _status_page_cache_key(client.status_page_slug, 'heartbeats')
    payload = cache.get(cache_key)
    if payload is not None:
        return payload

    payload = client.get_status_page_heartbeats()
    cache.set(cache_key, payload, _get_cache_timeout())
    return payload


def _service_status_from_status_page_payload(page_service, payload):
    monitor_id = page_service.monitor.kuma_monitor_id
    heartbeat_list = _get_monitor_heartbeats(payload, monitor_id)
    latest_heartbeat = heartbeat_list[-1] if heartbeat_list else {}
    uptime_bars = _daily_uptime_bars_from_storage(page_service.monitor)

    if not latest_heartbeat:
        return _unknown_service_status(page_service, uptime_bars=uptime_bars)

    return ServiceStatus(
        page_service=page_service,
        state=normalize_service_state(latest_heartbeat),
        uptime_bars=uptime_bars,
        updated_at=timezone.now(),
    )


def _get_monitor_heartbeats(payload, monitor_id):
    if not isinstance(payload, dict):
        return ()
    heartbeat_list = payload.get('heartbeatList')
    if not isinstance(heartbeat_list, dict):
        return ()
    monitor_heartbeats = heartbeat_list.get(str(monitor_id))
    if not isinstance(monitor_heartbeats, list):
        return ()
    return monitor_heartbeats


def _status_page_cache_key(status_page_slug, payload_type):
    slug_hash = sha256(str(status_page_slug).encode('utf-8')).hexdigest()
    return f'{CACHE_KEY_PREFIX}:status-page:{payload_type}:{slug_hash}'


def _get_cache_timeout():
    return getattr(settings, 'STATUS_KUMA_CACHE_SECONDS', DEFAULT_CACHE_SECONDS)


def _fallback_result(page_services):
    # A missing or unavailable Kuma integration should not hide manual incidents or leak internals.
    return KumaStatusResult(
        services=tuple(_unknown_service_status(page_service) for page_service in page_services),
        is_available=False,
        checked_at=None,
    )


def _unknown_service_status(page_service, *, uptime_bars=None):
    return ServiceStatus(
        page_service=page_service,
        state=ServiceState.UNKNOWN,
        uptime_bars=uptime_bars if uptime_bars is not None else _daily_uptime_bars_from_storage(page_service.monitor),
        updated_at=None,
    )


def _unknown_bars():
    return tuple(
        UptimeBar(label=_label_for_day(day), state=ServiceState.UNKNOWN, date=day, source='unknown')
        for day in _daily_bar_dates(DEFAULT_BAR_COUNT)
    )


def _extract_uptime_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return ()

    for key in ('bars', 'uptime', 'history', 'days', 'data'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return list(value.values())
    return ()


def _build_daily_uptime_bars(raw_items, *, count):
    buckets = {day: [] for day in _daily_bar_dates(count)}

    for item in raw_items:
        item_datetime = _heartbeat_datetime(item)
        if item_datetime is None:
            continue
        item_day = timezone.localtime(item_datetime).date()
        if item_day in buckets:
            buckets[item_day].append(item)

    bars = []
    for day, day_items in buckets.items():
        day_items = sorted(day_items, key=lambda item: _heartbeat_datetime(item) or timezone.now())
        state = _daily_state_from_heartbeats(day_items)
        bars.append(
            UptimeBar(
                label=_label_for_day(day),
                state=state,
                date=day,
                value=_daily_uptime_value(day_items),
                source='kuma' if day_items else 'unknown',
                tooltip_lines=_daily_tooltip_lines(state),
            )
        )
    return tuple(bars)


def _daily_uptime_bars_from_storage(monitor, *, count=DEFAULT_BAR_COUNT):
    rows = {
        row.day: row
        for row in KumaMonitorDailyUptime.objects.filter(
            monitor=monitor,
            day__in=_daily_bar_dates(count),
        )
    }
    bars = []
    for day in _daily_bar_dates(count):
        row = rows.get(day)
        if row is None or row.samples_count == 0:
            bars.append(UptimeBar(label=_label_for_day(day), state=ServiceState.UNKNOWN, date=day, source='unknown'))
            continue

        state = _daily_state_from_daily_uptime(row)
        bars.append(
            UptimeBar(
                label=_label_for_day(day),
                state=state,
                date=day,
                value=_daily_uptime_value_from_daily_uptime(row),
                source='kuma',
                tooltip_lines=_daily_tooltip_lines(state),
            )
        )
    return tuple(bars)


def _daily_state_from_daily_uptime(row):
    if row.max_consecutive_down >= DAILY_DEGRADED_DOWN_STREAK:
        return ServiceState.DEGRADED
    # Pending or unreadable samples are diagnostic only when confirmed checks exist.
    if row.up_count or row.down_count:
        return ServiceState.OPERATIONAL
    return ServiceState.UNKNOWN


def _daily_uptime_value_from_daily_uptime(row):
    known_count = row.up_count + row.down_count
    if known_count == 0:
        return None
    return row.up_count / known_count


def _daily_state_from_heartbeats(day_items):
    if not day_items:
        return ServiceState.UNKNOWN

    down_streak = 0
    has_known_state = False
    for item in day_items:
        state = normalize_service_state(item)
        if state == ServiceState.DOWN:
            has_known_state = True
            down_streak += 1
            if down_streak >= DAILY_DEGRADED_DOWN_STREAK:
                # Product rule: two consecutive failed Kuma heartbeats mark the whole day degraded.
                return ServiceState.DEGRADED
            continue
        down_streak = 0
        if state == ServiceState.OPERATIONAL:
            has_known_state = True

    if has_known_state:
        return ServiceState.OPERATIONAL
    return ServiceState.UNKNOWN


def _daily_tooltip_lines(state):
    if state == ServiceState.DEGRADED:
        return (DEGRADED_PERFORMANCE_TOOLTIP,)
    return ()


def _daily_uptime_value(day_items):
    values = [
        _float_or_none(_find_first_value(item, ('uptime', 'ratio', 'value')))
        for item in day_items
        if isinstance(item, dict)
    ]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return min(values)


def _daily_bar_dates(count):
    today = timezone.localdate()
    start = today - timedelta(days=count - 1)
    return tuple(start + timedelta(days=offset) for offset in range(count))


def _label_for_day(day):
    return day.strftime('%Y-%m-%d')


def _heartbeat_datetime(item):
    raw_value = _heartbeat_time_value(item)
    if raw_value in (None, ''):
        return None

    parsed = None
    if isinstance(raw_value, datetime):
        parsed = raw_value
    elif isinstance(raw_value, dt_date):
        parsed = datetime.combine(raw_value, time.min)
    elif isinstance(raw_value, (int, float)):
        parsed = datetime.fromtimestamp(raw_value, tz=timezone.get_current_timezone())
    else:
        value = str(raw_value).strip()
        parsed = parse_datetime(value)
        if parsed is None:
            parsed_date = parse_date(value)
            if parsed_date is not None:
                parsed = datetime.combine(parsed_date, time.min)

    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        # Kuma returns heartbeat timestamps without an offset in UTC. Convert them
        # to the configured Django timezone only when assigning the daily bucket.
        return timezone.make_aware(parsed, dt_timezone.utc)
    return parsed


def _heartbeat_time_value(item):
    if not isinstance(item, dict):
        return None
    return item.get('time') or item.get('datetime') or item.get('date') or item.get('created_at')


def _latest_daily_uptime_by_monitor(monitors):
    monitor_ids = [monitor.pk for monitor in monitors]
    if not monitor_ids:
        return {}

    rows = (
        KumaMonitorDailyUptime.objects
        .filter(monitor_id__in=monitor_ids, last_heartbeat_at__isnull=False)
        .order_by('monitor_id', '-last_heartbeat_at', '-day')
    )
    latest = {}
    for row in rows:
        latest.setdefault(row.monitor_id, row)
    return latest


def _update_daily_uptime_from_heartbeat(monitor, heartbeat, heartbeat_at):
    heartbeat_at = heartbeat_at.astimezone(timezone.get_current_timezone())
    day = heartbeat_at.date()
    row, _ = KumaMonitorDailyUptime.objects.select_for_update().get_or_create(monitor=monitor, day=day)
    state = normalize_service_state(heartbeat)

    row.samples_count += 1
    if state == ServiceState.OPERATIONAL:
        row.up_count += 1
        row.current_consecutive_down = 0
    elif state == ServiceState.DOWN:
        row.down_count += 1
        row.current_consecutive_down += 1
        row.max_consecutive_down = max(row.max_consecutive_down, row.current_consecutive_down)
    else:
        row.unknown_count += 1
        row.current_consecutive_down = 0

    row.last_status = state
    row.last_heartbeat_at = heartbeat_at
    row.save(
        update_fields=[
            'samples_count',
            'up_count',
            'down_count',
            'unknown_count',
            'max_consecutive_down',
            'current_consecutive_down',
            'last_heartbeat_at',
            'last_status',
            'updated_at',
        ]
    )
    return row


def _find_first_value(payload, keys):
    if not isinstance(payload, dict):
        return None
    for key in keys:
        if key in payload:
            return payload[key]
    monitor = payload.get('monitor')
    if isinstance(monitor, dict):
        return _find_first_value(monitor, keys)
    return None


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def _extract_status_page_monitors(payload):
    if not isinstance(payload, dict):
        raise KumaClientError('Kuma status page returned an invalid payload.')

    public_groups = payload.get('publicGroupList')
    if not isinstance(public_groups, list):
        raise KumaClientError('Kuma status page payload is missing publicGroupList.')

    raw_monitors = []
    for group in public_groups:
        if not isinstance(group, dict):
            continue
        monitors = group.get('monitorList')
        if not isinstance(monitors, list):
            continue
        raw_monitors.extend(monitor for monitor in monitors if isinstance(monitor, dict))

    seen_monitor_ids = set()
    for raw_monitor in raw_monitors:
        raw_id = raw_monitor.get('id')
        raw_name = raw_monitor.get('name')
        if raw_id in (None, '') or not raw_name:
            continue

        monitor_id = str(raw_id)
        if monitor_id in seen_monitor_ids:
            continue
        seen_monitor_ids.add(monitor_id)

        name = str(raw_name).strip()
        monitor_type = raw_monitor.get('type')

        yield KumaMonitorData(
            kuma_monitor_id=monitor_id,
            name=name,
            service_key=_service_key_from_monitor_id(monitor_id),
            kuma_monitor_type=str(monitor_type).strip()[:40] if monitor_type else '',
            description=f'Kuma {monitor_id}',
        )


def _service_key_from_monitor_id(monitor_id):
    monitor_id_slug = slugify(monitor_id).replace('-', '_')
    if monitor_id_slug and len(monitor_id_slug) <= 95:
        return f'kuma_{monitor_id_slug}'[:100]

    monitor_id_hash = sha256(monitor_id.encode()).hexdigest()[:12]
    if not monitor_id_slug:
        return f'kuma_{monitor_id_hash}'

    return f'kuma_{monitor_id_slug[:82]}_{monitor_id_hash}'
