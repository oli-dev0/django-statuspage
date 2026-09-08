from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.sites import get_site_definition


class ServiceState(models.TextChoices):
    OPERATIONAL = 'operational', _('Healthy')
    DEGRADED = 'degraded', _('Degraded')
    DOWN = 'down', _('Unavailable')
    UNKNOWN = 'unknown', _('Unknown')


class OverallStatus(models.TextChoices):
    OPERATIONAL = 'operational', _('All systems operational')
    DEGRADED = 'degraded', _('Some systems are degraded')
    DOWN = 'down', _('Some systems are unavailable')
    UNKNOWN = 'unknown', _('Status information is temporarily unavailable.')
    EMPTY = 'empty', _('No services are configured yet.')


class IncidentSeverity(models.TextChoices):
    MINOR = 'minor', _('Minor')
    MAJOR = 'major', _('Major')
    CRITICAL = 'critical', _('Critical')


class IncidentPhase(models.TextChoices):
    INVESTIGATING = 'investigating', _('Investigating')
    IDENTIFIED = 'identified', _('Identified')
    MONITORING = 'monitoring', _('Monitoring')
    RESOLVED = 'resolved', _('Resolved')


class StatusPage(models.Model):
    site_slug = models.CharField(max_length=40)
    slug = models.SlugField(max_length=80, default='main')
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    logo_url = models.URLField(blank=True)
    back_link_label = models.CharField(max_length=80, blank=True)
    back_link_url = models.URLField(blank=True)
    is_visible = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['site_slug', 'slug'], name='status_one_page_slug_per_site'),
        ]
        indexes = [
            models.Index(fields=['site_slug', 'slug', 'is_visible']),
        ]
        ordering = ['site_slug', 'slug']

    def __str__(self):
        return f'{self.title} ({self.site_slug})'

    def clean(self):
        super().clean()

        errors = {}
        if self.site_slug and get_site_definition(self.site_slug) is None:
            errors['site_slug'] = _('Choose a configured site.')
        if self.is_visible and not self.title:
            errors['title'] = _('Visible status pages must have a title.')

        if errors:
            raise ValidationError(errors)


class KumaMonitor(models.Model):
    name = models.CharField(max_length=120)
    kuma_monitor_id = models.CharField(max_length=80, unique=True)
    kuma_monitor_type = models.CharField(max_length=40, blank=True, editable=False)
    service_key = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    is_available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['is_available']),
        ]
        ordering = ['name']

    def __str__(self):
        return self.name


class StatusPageService(models.Model):
    status_page = models.ForeignKey(StatusPage, on_delete=models.CASCADE, related_name='selected_services')
    monitor = models.ForeignKey(KumaMonitor, on_delete=models.PROTECT, related_name='status_page_selections')
    display_name = models.CharField(max_length=120, blank=True)
    position = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['status_page', 'monitor'], name='status_one_monitor_per_page'),
        ]
        indexes = [
            models.Index(fields=['status_page', 'position']),
        ]
        ordering = ['position', 'monitor__name']

    def __str__(self):
        return self.display_label

    @property
    def display_label(self):
        return self.display_name or self.monitor.name

    def clean(self):
        super().clean()

        if self.monitor_id and not self.monitor.is_available:
            raise ValidationError({'monitor': _('Selected services must use available Kuma monitors.')})


class KumaMonitorDailyUptime(models.Model):
    monitor = models.ForeignKey(KumaMonitor, on_delete=models.CASCADE, related_name='daily_uptime')
    day = models.DateField()
    samples_count = models.PositiveIntegerField(default=0)
    up_count = models.PositiveIntegerField(default=0)
    down_count = models.PositiveIntegerField(default=0)
    unknown_count = models.PositiveIntegerField(default=0)
    max_consecutive_down = models.PositiveIntegerField(default=0)
    current_consecutive_down = models.PositiveIntegerField(default=0)
    last_heartbeat_at = models.DateTimeField(blank=True, null=True)
    last_status = models.CharField(
        max_length=20,
        choices=ServiceState.choices,
        default=ServiceState.UNKNOWN,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['monitor', 'day'], name='status_one_daily_uptime_per_monitor_day'),
        ]
        indexes = [
            models.Index(fields=['monitor', 'day']),
        ]
        ordering = ['monitor', 'day']

    def __str__(self):
        return f'{self.monitor} uptime for {self.day:%Y-%m-%d}'


class Incident(models.Model):
    status_page = models.ForeignKey(StatusPage, on_delete=models.CASCADE, related_name='incidents')
    title = models.CharField(max_length=180)
    severity = models.CharField(
        max_length=20,
        choices=IncidentSeverity.choices,
        default=IncidentSeverity.MINOR,
    )
    phase = models.CharField(
        max_length=20,
        choices=IncidentPhase.choices,
        default=IncidentPhase.INVESTIGATING,
    )
    affected_services = models.ManyToManyField(StatusPageService, blank=True, related_name='incidents')
    started_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(blank=True, null=True)
    summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['status_page', 'phase', 'started_at']),
            models.Index(fields=['status_page', 'resolved_at']),
        ]
        ordering = ['-started_at', '-created_at']

    def __str__(self):
        return self.title

    @property
    def is_resolved(self):
        return self.phase == IncidentPhase.RESOLVED

    def clean(self):
        super().clean()

        if self.phase != IncidentPhase.RESOLVED and self.resolved_at:
            raise ValidationError({'resolved_at': _('Only resolved incidents can have a resolved time.')})

    def save(self, *args, **kwargs):
        if self.phase == IncidentPhase.RESOLVED and self.resolved_at is None:
            # Admin users can mark an incident resolved without remembering the timestamp field.
            self.resolved_at = timezone.now()
        super().save(*args, **kwargs)


class IncidentUpdate(models.Model):
    incident = models.ForeignKey(Incident, on_delete=models.CASCADE, related_name='updates')
    phase = models.CharField(max_length=20, choices=IncidentPhase.choices)
    message = models.TextField()
    published_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['incident', 'published_at']),
        ]
        ordering = ['published_at', 'created_at']

    def __str__(self):
        return f'{self.incident} update at {self.published_at:%Y-%m-%d %H:%M}'
