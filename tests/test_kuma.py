from io import StringIO
from datetime import datetime, time
from unittest.mock import Mock, patch
from urllib.error import URLError

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.status.kuma import (
    DEFAULT_BAR_COUNT,
    KumaClient,
    KumaClientError,
    get_service_statuses,
    ingest_kuma_daily_uptime_payload,
    normalize_service_state,
    normalize_uptime_bars,
    sync_kuma_daily_uptime_from_status_page,
    sync_kuma_monitors_from_status_page,
)
from apps.status.models import KumaMonitor, KumaMonitorDailyUptime, ServiceState, StatusPageService

from .helpers import create_monitor, create_page_service, create_status_page


def kuma_response(payload):
    response = Mock()
    response.read.return_value = payload
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=None)
    return response


class KumaClientTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_normalize_service_state_accepts_common_kuma_status_shapes(self):
        self.assertEqual(normalize_service_state({'status': 1}), ServiceState.OPERATIONAL)
        self.assertEqual(normalize_service_state({'status': 2}), ServiceState.DEGRADED)
        self.assertEqual(normalize_service_state({'monitor': {'status': 'degraded'}}), ServiceState.DEGRADED)
        self.assertEqual(normalize_service_state({'heartbeat': {'status': 0}}), ServiceState.DOWN)
        self.assertEqual(normalize_service_state({'status': 'not-a-known-state'}), ServiceState.UNKNOWN)

    def test_normalize_uptime_bars_handles_lists_and_missing_data(self):
        today = timezone.localdate()
        bars = normalize_uptime_bars(
            [
                {'time': f'{today.isoformat()} 12:00:00', 'status': 1, 'uptime': 1},
            ]
        )
        fallback_bars = normalize_uptime_bars({})

        self.assertEqual(len(bars), DEFAULT_BAR_COUNT)
        self.assertEqual(bars[-1].date, today)
        self.assertEqual(bars[-1].state, ServiceState.OPERATIONAL)
        self.assertEqual(bars[0].state, ServiceState.UNKNOWN)
        self.assertEqual(fallback_bars[0].state, ServiceState.UNKNOWN)
        self.assertEqual(len(fallback_bars), DEFAULT_BAR_COUNT)

    def test_normalize_uptime_bars_ignores_transient_states_when_known_samples_exist(self):
        today = timezone.localdate()

        bars = normalize_uptime_bars(
            [
                {'time': f'{today.isoformat()} 10:00:00', 'status': 1},
                {'time': f'{today.isoformat()} 10:05:00', 'status': 2},
                {'time': f'{today.isoformat()} 10:10:00', 'status': 'unexpected'},
            ]
        )
        pending_only_bars = normalize_uptime_bars(
            [
                {'time': f'{today.isoformat()} 10:00:00', 'status': 2},
            ]
        )
        unknown_only_bars = normalize_uptime_bars(
            [
                {'time': f'{today.isoformat()} 10:00:00', 'status': 'unexpected'},
            ]
        )

        self.assertEqual(bars[-1].state, ServiceState.OPERATIONAL)
        self.assertEqual(pending_only_bars[-1].state, ServiceState.UNKNOWN)
        self.assertEqual(unknown_only_bars[-1].state, ServiceState.UNKNOWN)

    def test_normalize_uptime_bars_requires_consecutive_down_heartbeats_for_degraded_day(self):
        today = timezone.localdate()

        non_consecutive_bars = normalize_uptime_bars(
            [
                {'time': f'{today.isoformat()} 10:00:00', 'status': 0},
                {'time': f'{today.isoformat()} 10:05:00', 'status': 1},
                {'time': f'{today.isoformat()} 10:10:00', 'status': 0},
            ]
        )
        consecutive_bars = normalize_uptime_bars(
            [
                {'time': f'{today.isoformat()} 10:00:00', 'status': 1},
                {'time': f'{today.isoformat()} 10:05:00', 'status': 0},
                {'time': f'{today.isoformat()} 10:10:00', 'status': 0},
            ]
        )

        self.assertEqual(non_consecutive_bars[-1].state, ServiceState.OPERATIONAL)
        self.assertEqual(consecutive_bars[-1].state, ServiceState.DEGRADED)
        self.assertEqual(consecutive_bars[-1].tooltip_lines, ('Degraded performance',))

    @override_settings(STATUS_KUMA_BASE_URL='')
    def test_get_service_statuses_returns_safe_fallback_when_kuma_is_not_configured(self):
        service = create_page_service(create_status_page())

        result = get_service_statuses([service])

        self.assertFalse(result.is_available)
        self.assertIsNone(result.checked_at)
        self.assertEqual(result.services[0].state, ServiceState.UNKNOWN)
        self.assertEqual(len(result.services[0].uptime_bars), DEFAULT_BAR_COUNT)

    @override_settings(
        STATUS_KUMA_BASE_URL='http://uptime-kuma:3001',
        STATUS_KUMA_STATUS_PAGE_SLUG='main',
        STATUS_KUMA_CACHE_SECONDS=300,
    )
    def test_get_service_statuses_uses_cached_status_page_heartbeats(self):
        monitor = create_monitor(kuma_monitor_id='42')
        service = create_page_service(create_status_page(), monitor=monitor)
        yesterday = timezone.localdate() - timezone.timedelta(days=1)
        KumaMonitorDailyUptime.objects.create(
            monitor=monitor,
            day=yesterday,
            samples_count=1,
            up_count=1,
            last_status=ServiceState.OPERATIONAL,
            last_heartbeat_at=timezone.make_aware(
                datetime.combine(yesterday, time.min)
            ),
        )
        response = kuma_response(
            b'{"heartbeatList": {"42": [{"status": 1, "time": "2026-07-01 12:00:00"}]}}'
        )

        with patch('apps.status.kuma.urlopen', return_value=response) as urlopen:
            first_result = get_service_statuses([service])
            second_result = get_service_statuses([service])

        self.assertEqual(urlopen.call_count, 1)
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, 'http://uptime-kuma:3001/api/status-page/heartbeat/main')
        self.assertEqual(first_result.services[0].state, ServiceState.OPERATIONAL)
        self.assertEqual(first_result.services[0].uptime_bars[-1].label, timezone.localdate().isoformat())
        self.assertEqual(first_result.services[0].uptime_bars[-2].state, ServiceState.OPERATIONAL)
        self.assertEqual(second_result.services[0].state, ServiceState.OPERATIONAL)

    def test_ingest_kuma_daily_uptime_payload_updates_daily_aggregates_idempotently(self):
        monitor = create_monitor(kuma_monitor_id='42')
        yesterday = timezone.localdate() - timezone.timedelta(days=1)
        today = timezone.localdate()
        payload = {
            'heartbeatList': {
                '42': [
                    {'status': 1, 'time': f'{yesterday.isoformat()} 21:55:00'},
                    {'status': 0, 'time': f'{today.isoformat()} 00:00:00'},
                    {'status': 0, 'time': f'{today.isoformat()} 00:05:00'},
                ],
            },
        }

        first_result = ingest_kuma_daily_uptime_payload(payload)
        second_result = ingest_kuma_daily_uptime_payload(payload)

        self.assertEqual(first_result.heartbeats_ingested, 3)
        self.assertEqual(second_result.heartbeats_ingested, 0)
        yesterday_row = KumaMonitorDailyUptime.objects.get(monitor=monitor, day=yesterday)
        today_row = KumaMonitorDailyUptime.objects.get(monitor=monitor, day=today)
        self.assertEqual(yesterday_row.samples_count, 1)
        self.assertEqual(yesterday_row.up_count, 1)
        self.assertEqual(today_row.samples_count, 2)
        self.assertEqual(today_row.down_count, 2)
        self.assertEqual(today_row.max_consecutive_down, 2)

    @override_settings(STATUS_KUMA_BASE_URL='http://uptime-kuma:3001', STATUS_KUMA_STATUS_PAGE_SLUG='main')
    def test_ingested_daily_aggregates_drive_uptime_bars_when_kuma_only_returns_today(self):
        monitor = create_monitor(kuma_monitor_id='42')
        service = create_page_service(create_status_page(), monitor=monitor)
        yesterday = timezone.localdate() - timezone.timedelta(days=1)
        today = timezone.localdate()
        ingest_kuma_daily_uptime_payload(
            {
                'heartbeatList': {
                    '42': [
                        {'status': 1, 'time': f'{yesterday.isoformat()} 21:55:00'},
                        {'status': 1, 'time': f'{today.isoformat()} 10:00:00'},
                    ],
                },
            }
        )
        live_payload = {
            'heartbeatList': {
                '42': [
                    {'status': 1, 'time': f'{today.isoformat()} 12:00:00'},
                ],
            },
        }

        with patch('apps.status.kuma.KumaClient.get_status_page_heartbeats', return_value=live_payload):
            result = get_service_statuses([service])

        self.assertEqual(result.services[0].state, ServiceState.OPERATIONAL)
        self.assertEqual(result.services[0].uptime_bars[-2].date, yesterday)
        self.assertEqual(result.services[0].uptime_bars[-2].state, ServiceState.OPERATIONAL)
        self.assertEqual(result.services[0].uptime_bars[-1].state, ServiceState.OPERATIONAL)

    @override_settings(STATUS_KUMA_BASE_URL='http://uptime-kuma:3001', STATUS_KUMA_STATUS_PAGE_SLUG='main')
    def test_stored_pending_sample_does_not_discolor_day_with_confirmed_uptime(self):
        monitor = create_monitor(kuma_monitor_id='42')
        service = create_page_service(create_status_page(), monitor=monitor)
        today = timezone.localdate()
        ingest_kuma_daily_uptime_payload(
            {
                'heartbeatList': {
                    '42': [
                        {'status': 1, 'time': f'{today.isoformat()} 10:00:00'},
                        {'status': 2, 'time': f'{today.isoformat()} 10:05:00'},
                    ],
                },
            }
        )
        live_payload = {
            'heartbeatList': {
                '42': [
                    {'status': 2, 'time': f'{today.isoformat()} 10:10:00'},
                ],
            },
        }

        with patch('apps.status.kuma.KumaClient.get_status_page_heartbeats', return_value=live_payload):
            result = get_service_statuses([service])

        row = KumaMonitorDailyUptime.objects.get(monitor=monitor, day=today)
        self.assertEqual(row.up_count, 1)
        self.assertEqual(row.unknown_count, 1)
        self.assertEqual(result.services[0].state, ServiceState.DEGRADED)
        self.assertEqual(result.services[0].uptime_bars[-1].state, ServiceState.OPERATIONAL)

    @override_settings(STATUS_KUMA_BASE_URL='http://uptime-kuma:3001', STATUS_KUMA_STATUS_PAGE_SLUG='main')
    def test_stored_unknown_only_day_remains_unknown(self):
        monitor = create_monitor(kuma_monitor_id='42')
        service = create_page_service(create_status_page(), monitor=monitor)
        today = timezone.localdate()
        ingest_kuma_daily_uptime_payload(
            {
                'heartbeatList': {
                    '42': [
                        {'status': 2, 'time': f'{today.isoformat()} 10:00:00'},
                    ],
                },
            }
        )

        with patch(
            'apps.status.kuma.KumaClient.get_status_page_heartbeats',
            return_value={'heartbeatList': {'42': [{'status': 1, 'time': f'{today.isoformat()} 10:05:00'}]}},
        ):
            result = get_service_statuses([service])

        self.assertEqual(result.services[0].uptime_bars[-1].state, ServiceState.UNKNOWN)

    @override_settings(TIME_ZONE='UTC')
    def test_ingest_interprets_naive_kuma_timestamps_as_utc_before_local_bucket_assignment(self):
        monitor = create_monitor(kuma_monitor_id='42')

        ingest_kuma_daily_uptime_payload(
            {
                'heartbeatList': {
                    '42': [
                        {'status': 1, 'time': '2026-07-25 22:15:00'},
                    ],
                },
            }
        )

        self.assertTrue(
            KumaMonitorDailyUptime.objects.filter(
                monitor=monitor,
                day=datetime(2026, 7, 26).date(),
            ).exists()
        )
        self.assertFalse(
            KumaMonitorDailyUptime.objects.filter(
                monitor=monitor,
                day=datetime(2026, 7, 25).date(),
            ).exists()
        )

    @override_settings(TIME_ZONE='UTC')
    def test_ingest_preserves_explicit_kuma_timestamp_offsets(self):
        monitor = create_monitor(kuma_monitor_id='43')

        ingest_kuma_daily_uptime_payload(
            {
                'heartbeatList': {
                    '43': [
                        {'status': 1, 'time': '2026-07-25T20:15:00+00:00'},
                    ],
                },
            }
        )

        self.assertTrue(
            KumaMonitorDailyUptime.objects.filter(
                monitor=monitor,
                day=datetime(2026, 7, 25).date(),
            ).exists()
        )

    @override_settings(STATUS_KUMA_BASE_URL='http://uptime-kuma:3001')
    def test_sync_kuma_daily_uptime_from_status_page_fetches_and_ingests_heartbeats(self):
        monitor = create_monitor(kuma_monitor_id='42')
        today = timezone.localdate()
        payload = {'heartbeatList': {'42': [{'status': 1, 'time': f'{today.isoformat()} 12:00:00'}]}}

        with patch('apps.status.kuma.KumaClient.get_status_page_heartbeats', return_value=payload):
            result = sync_kuma_daily_uptime_from_status_page()

        self.assertEqual(result.monitors_matched, 1)
        self.assertEqual(result.heartbeats_ingested, 1)
        self.assertTrue(KumaMonitorDailyUptime.objects.filter(monitor=monitor, day=today).exists())

    def test_service_status_exposes_text_uptime_summary(self):
        service = create_page_service(create_status_page(), display_name='Website')
        result = get_service_statuses([service])

        self.assertIn('Recent daily uptime for Website', result.services[0].uptime_summary)
        self.assertIn('60 unknown', result.services[0].uptime_summary)

    def test_kuma_client_uses_status_page_slug_without_authorization_header(self):
        client = KumaClient(base_url='http://uptime-kuma:3001', timeout=2, status_page_slug='main')
        response = kuma_response(b'{"config": {"slug": "main"}}')

        with patch('apps.status.kuma.urlopen', return_value=response) as urlopen:
            payload = client.get_status_page()

        request = urlopen.call_args.args[0]
        self.assertEqual(payload, {'config': {'slug': 'main'}})
        self.assertEqual(request.full_url, 'http://uptime-kuma:3001/api/status-page/main')
        self.assertIsNone(request.headers.get('Authorization'))

    def test_kuma_client_wraps_upstream_errors(self):
        client = KumaClient(base_url='http://uptime-kuma:3001')

        with self.assertLogs('apps.status.kuma', level='WARNING'), patch(
            'apps.status.kuma.urlopen',
            side_effect=URLError('down'),
        ):
            with self.assertRaisesMessage(KumaClientError, 'Kuma status endpoint could not be reached.'):
                client.get_status_page()

    @override_settings(STATUS_KUMA_BASE_URL='http://uptime-kuma:3001', STATUS_KUMA_STATUS_PAGE_SLUG='main')
    def test_sync_kuma_monitors_adds_updates_and_hard_deletes_removed_monitors(self):
        page = create_status_page()
        existing_monitor = create_monitor(
            name='Old website',
            kuma_monitor_id='1',
            service_key='old-website',
            description='Manual public description',
            is_available=False,
        )
        stale_monitor = create_monitor(name='Stale', kuma_monitor_id='99', service_key='stale')
        stale_service = create_page_service(page, monitor=stale_monitor, display_name='Old service')
        payload = {
            'publicGroupList': [
                {
                    'monitorList': [
                        {'id': 1, 'name': 'Website', 'type': 'http'},
                        {'id': 2, 'name': 'Website', 'type': 'postgres'},
                    ],
                },
            ],
        }

        with patch('apps.status.kuma.KumaClient.get_status_page', return_value=payload):
            result = sync_kuma_monitors_from_status_page()

        self.assertEqual(result.fetched, 2)
        self.assertEqual(result.added, 1)
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.deleted, 1)
        self.assertFalse(KumaMonitor.objects.filter(pk=stale_monitor.pk).exists())
        self.assertFalse(StatusPageService.objects.filter(pk=stale_service.pk).exists())

        existing_monitor.refresh_from_db()
        new_monitor = KumaMonitor.objects.get(kuma_monitor_id='2')
        self.assertEqual(existing_monitor.name, 'Website')
        self.assertEqual(existing_monitor.service_key, 'kuma_1')
        self.assertEqual(existing_monitor.kuma_monitor_type, 'http')
        self.assertEqual(existing_monitor.description, 'Manual public description')
        self.assertTrue(existing_monitor.is_available)
        self.assertEqual(new_monitor.name, 'Website')
        self.assertEqual(new_monitor.service_key, 'kuma_2')
        self.assertEqual(new_monitor.kuma_monitor_type, 'postgres')
        self.assertEqual(new_monitor.description, 'Kuma 2')

    @override_settings(STATUS_KUMA_BASE_URL='http://uptime-kuma:3001')
    def test_sync_kuma_monitors_leaves_database_unchanged_when_payload_is_invalid(self):
        monitor = create_monitor(name='Existing', kuma_monitor_id='1', service_key='existing')

        with patch('apps.status.kuma.KumaClient.get_status_page', return_value={'config': {}}):
            with self.assertRaises(KumaClientError):
                sync_kuma_monitors_from_status_page()

        self.assertTrue(KumaMonitor.objects.filter(pk=monitor.pk).exists())

    @override_settings(STATUS_KUMA_BASE_URL='http://uptime-kuma:3001')
    def test_sync_status_uptime_command_reports_success(self):
        create_monitor(kuma_monitor_id='42')
        today = timezone.localdate()
        payload = {'heartbeatList': {'42': [{'status': 1, 'time': f'{today.isoformat()} 12:00:00'}]}}
        stdout = StringIO()

        with patch('apps.status.kuma.KumaClient.get_status_page_heartbeats', return_value=payload):
            call_command('sync_status_uptime', stdout=stdout)

        self.assertIn('1/1 heartbeats ingested', stdout.getvalue())

    @override_settings(STATUS_KUMA_BASE_URL='')
    def test_sync_status_uptime_command_raises_command_error_when_kuma_is_unconfigured(self):
        with self.assertRaises(CommandError):
            call_command('sync_status_uptime', stdout=StringIO())
