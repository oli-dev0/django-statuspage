from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.forms.models import BaseInlineFormSet
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.core.sites import get_site_slug_choices

from .kuma import KumaClientError, sync_kuma_monitors_from_status_page
from .models import Incident, IncidentUpdate, KumaMonitor, StatusPage, StatusPageService


STATUS_PAGE_MONITOR_FIELD_PREFIX = 'status_page_monitor'


class StatusPageAdminForm(forms.ModelForm):
    site_slug = forms.ChoiceField(label=_('Website'))
    slug = forms.SlugField(
        label=_('Public page slug'),
        help_text=_('"main" for /status/, other for /status/other/'),
        max_length=80,
    )

    class Meta:
        model = StatusPage
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['site_slug'].choices = get_site_slug_choices()
        self.fields['slug'].initial = self.instance.slug or 'main'


class IncidentAdminForm(forms.ModelForm):
    class Meta:
        model = Incident
        fields = '__all__'
        widgets = {
            'affected_services': forms.CheckboxSelectMultiple(attrs={'class': 'status-affected-services'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['affected_services'].help_text = _(
            'Leave all services unchecked when the incident affects the full status page.'
        )
        status_page = self._get_status_page()
        if status_page is not None:
            self.fields['affected_services'].queryset = StatusPageService.objects.filter(
                status_page=status_page,
            ).select_related('monitor')
        else:
            self.fields['affected_services'].queryset = StatusPageService.objects.none()

    def clean(self):
        cleaned_data = super().clean()
        status_page = cleaned_data.get('status_page')
        affected_services = cleaned_data.get('affected_services')
        if status_page is None or affected_services is None:
            return cleaned_data

        wrong_page_services = affected_services.exclude(status_page=status_page)
        if wrong_page_services.exists():
            self.add_error('affected_services', 'Affected services must belong to the selected status page.')
        return cleaned_data

    def _get_status_page(self):
        if self.instance.pk:
            return self.instance.status_page

        status_page_id = self.data.get('status_page') if self.data else None
        if not status_page_id:
            status_page_id = self.initial.get('status_page')
        if not status_page_id:
            return None

        try:
            return StatusPage.objects.get(pk=status_page_id)
        except (StatusPage.DoesNotExist, ValueError):
            return None


class IncidentUpdateInlineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return

        update_count = 0
        for form in self.forms:
            cleaned_data = getattr(form, 'cleaned_data', None)
            if not cleaned_data or cleaned_data.get('DELETE'):
                continue
            if cleaned_data.get('message'):
                update_count += 1

        if update_count == 0:
            raise ValidationError(_('Add at least one incident update before saving the incident.'))


class IncidentUpdateInline(admin.StackedInline):
    model = IncidentUpdate
    formset = IncidentUpdateInlineFormSet
    min_num = 1
    validate_min = True
    extra = 0


@admin.register(StatusPage)
class StatusPageAdmin(admin.ModelAdmin):
    form = StatusPageAdminForm
    change_form_template = 'admin/status/statuspage/change_form.html'
    list_display = ('title', 'site_slug', 'slug', 'is_visible', 'updated_at')
    list_filter = ('site_slug', 'is_visible')
    search_fields = ('title', 'description', 'slug')

    class Media:
        css = {
            'all': ('status/admin_status_page.css',),
        }
        js = ('status/admin_status_page.js',)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        status_page = self.get_object(request, object_id) if object_id else None
        extra_context = extra_context or {}
        extra_context['monitor_selection_rows'] = self.get_monitor_selection_rows(request, status_page)
        return super().changeform_view(request, object_id, form_url, extra_context)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        self.save_monitor_selections(request, form.instance)

    def get_monitor_selection_rows(self, request, status_page):
        existing_services = {}
        if status_page is not None and status_page.pk:
            existing_services = {
                service.monitor_id: service
                for service in status_page.selected_services.select_related('monitor')
            }

        monitors = KumaMonitor.objects.filter(is_available=True)
        if existing_services:
            monitors = KumaMonitor.objects.filter(Q(is_available=True) | Q(pk__in=existing_services))

        rows = [
            self.get_monitor_selection_row(request, monitor, existing_services.get(monitor.pk))
            for monitor in monitors
        ]
        return sorted(rows, key=self.get_monitor_selection_sort_key)

    def get_monitor_selection_sort_key(self, row):
        if row['selected']:
            return (0, self.clean_monitor_position(row['position']), row['monitor'].name.lower())
        return (1, row['monitor'].name.lower())

    def get_monitor_selection_row(self, request, monitor, service):
        prefix = self.get_monitor_selection_prefix(monitor.pk)
        selected_field = f'{prefix}_selected'
        display_name_field = f'{prefix}_display_name'
        position_field = f'{prefix}_position'
        is_post = request.method == 'POST'

        return {
            'monitor': monitor,
            'selected_field': selected_field,
            'display_name_field': display_name_field,
            'position_field': position_field,
            'selected': selected_field in request.POST if is_post else service is not None,
            'display_name': request.POST.get(display_name_field, '') if is_post else (service.display_name if service else ''),
            'position': request.POST.get(position_field, '0') if is_post else (service.position if service else 0),
            'is_available': monitor.is_available,
        }

    def save_monitor_selections(self, request, status_page):
        existing_services = {
            service.monitor_id: service
            for service in status_page.selected_services.select_related('monitor')
        }
        visible_monitors = KumaMonitor.objects.filter(
            Q(is_available=True) | Q(pk__in=existing_services)
        )

        for monitor in visible_monitors:
            prefix = self.get_monitor_selection_prefix(monitor.pk)
            service = existing_services.get(monitor.pk)
            is_selected = f'{prefix}_selected' in request.POST

            if not is_selected:
                if service is not None:
                    service.delete()
                continue

            defaults = {
                'display_name': request.POST.get(f'{prefix}_display_name', '').strip(),
                'position': self.clean_monitor_position(request.POST.get(f'{prefix}_position')),
                'is_visible': True,
            }
            if service is None:
                StatusPageService.objects.create(status_page=status_page, monitor=monitor, **defaults)
            else:
                for field, value in defaults.items():
                    setattr(service, field, value)
                service.save(update_fields=list(defaults))

    def clean_monitor_position(self, value):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    def get_monitor_selection_prefix(self, monitor_id):
        return f'{STATUS_PAGE_MONITOR_FIELD_PREFIX}_{monitor_id}'


@admin.register(KumaMonitor)
class KumaMonitorAdmin(admin.ModelAdmin):
    change_list_template = 'admin/status/kumamonitor/change_list.html'
    list_display = ('name', 'unique_key', 'kuma_monitor_id', 'kuma_monitor_type', 'is_available', 'updated_at')
    list_filter = ('is_available', 'kuma_monitor_type')
    search_fields = ('name', 'service_key', 'kuma_monitor_id', 'kuma_monitor_type')

    @admin.display(description=_('Unique key'), ordering='service_key')
    def unique_key(self, obj):
        return obj.service_key

    def get_readonly_fields(self, request, obj=None):
        if obj and obj.kuma_monitor_type:
            return ('kuma_monitor_type',)
        return ()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == 'service_key' and formfield is not None:
            formfield.label = _('Unique key')
        return formfield

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'fetch-from-kuma/',
                self.admin_site.admin_view(self.fetch_from_kuma),
                name='status_kumamonitor_fetch_from_kuma',
            ),
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context['fetch_from_kuma_url'] = reverse('admin:status_kumamonitor_fetch_from_kuma')
        extra_context['has_kuma_fetch_permission'] = self.has_change_permission(request)
        return super().changelist_view(request, extra_context=extra_context)

    def fetch_from_kuma(self, request):
        if not self.has_change_permission(request):
            self.message_user(request, _('You do not have permission to fetch Kuma monitors.'), level=messages.ERROR)
            return redirect('..')

        if request.method != 'POST':
            return redirect('..')

        try:
            result = sync_kuma_monitors_from_status_page()
        except KumaClientError as exc:
            self.message_user(
                request,
                _('Could not fetch Kuma monitors: %(error)s') % {'error': exc},
                level=messages.ERROR,
            )
            return redirect('..')

        self.message_user(
            request,
            _(
                'Fetched %(fetched)d monitors from Kuma: %(added)d added, %(updated)d updated, '
                '%(deleted)d deleted.'
            ) % {
                'fetched': result.fetched,
                'added': result.added,
                'updated': result.updated,
                'deleted': result.deleted,
            },
            level=messages.SUCCESS,
        )
        return redirect('..')


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    date_hierarchy = "started_at"
    form = IncidentAdminForm
    inlines = [IncidentUpdateInline]
    list_display = ('title', 'status_page', 'severity', 'phase', 'started_at', 'resolved_at')
    list_filter = ('severity', 'phase', 'status_page__site_slug')
    search_fields = ('title', 'summary')

    class Media:
        css = {
            'all': ('status/admin_incident.css',),
        }
        js = ('status/admin_incident.js',)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        status_page_id = request.GET.get('status_page')
        if status_page_id and StatusPage.objects.filter(pk=status_page_id).exists():
            initial['status_page'] = status_page_id
        return initial
