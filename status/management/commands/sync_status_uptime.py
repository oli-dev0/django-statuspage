from django.core.management.base import BaseCommand, CommandError

from apps.status.kuma import KumaClientError, sync_kuma_daily_uptime_from_status_page


class Command(BaseCommand):
    help = 'Fetch recent Kuma heartbeats and update stored daily status-page uptime history.'

    def handle(self, *args, **options):
        try:
            result = sync_kuma_daily_uptime_from_status_page()
        except KumaClientError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                'Processed %(monitors_matched)d/%(monitors_seen)d Kuma monitors: '
                '%(heartbeats_ingested)d/%(heartbeats_seen)d heartbeats ingested, '
                '%(daily_rows_updated)d daily rows updated.'
                % {
                    'monitors_matched': result.monitors_matched,
                    'monitors_seen': result.monitors_seen,
                    'heartbeats_ingested': result.heartbeats_ingested,
                    'heartbeats_seen': result.heartbeats_seen,
                    'daily_rows_updated': result.daily_rows_updated,
                }
            )
        )
        for warning in result.warnings:
            self.stderr.write(self.style.WARNING(warning))
