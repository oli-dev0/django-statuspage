from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.forms import CheckboxSelectMultiple, Select
from django.test import TestCase, override_settings
from django.forms.models import inlineformset_factory
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.status.admin import (
    STATUS_PAGE_MONITOR_FIELD_PREFIX,
    IncidentAdminForm,
    IncidentUpdateInlineFormSet,
    StatusPageAdminForm,
)
from apps.status.kuma import KumaClientError, KumaMonitorSyncResult
from apps.status.models import Incident, IncidentPhase, IncidentSeverity, IncidentUpdate, StatusPageService

from .helpers import create_monitor, create_page_service, create_status_page


TEST_STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}


class StatusAdminFormTests(TestCase):
    def test_status_page_admin_form_uses_website_dropdown_for_site_slug(self):
        form = StatusPageAdminForm()

        self.assertIsInstance(form.fields['site_slug'].widget, Select)
        self.assertEqual(form.fields['site_slug'].label, 'Website')
        self.assertTrue(form.fields['site_slug'].choices)

    def test_status_page_admin_form_labels_public_page_slug(self):
        form = StatusPageAdminForm()

        self.assertEqual(form.fields['slug'].label, 'Public page slug')
        self.assertEqual(form.fields['slug'].help_text, '"main" for /status/, other for /status/other/')
        self.assertEqual(form.fields['slug'].initial, 'main')

    def test_incident_admin_form_limits_affected_services_to_selected_status_page(self):
        page = create_status_page()
        visible_service = create_page_service(page, display_name='Website')
        other_page_service = create_page_service(
            create_status_page(slug='secondary', title='Secondary Status'),
            monitor=create_monitor(name='Other', kuma_monitor_id='other', service_key='other'),
        )

        form = IncidentAdminForm(data={'status_page': page.pk})

        self.assertIn(visible_service, form.fields['affected_services'].queryset)
        self.assertNotIn(other_page_service, form.fields['affected_services'].queryset)

    def test_incident_admin_form_uses_initial_status_page_for_affected_services(self):
        page = create_status_page()
        visible_service = create_page_service(page, display_name='Website')
        other_page_service = create_page_service(
            create_status_page(slug='secondary', title='Secondary Status'),
            monitor=create_monitor(name='Other', kuma_monitor_id='other', service_key='other'),
        )

        form = IncidentAdminForm(initial={'status_page': page.pk})

        self.assertIn(visible_service, form.fields['affected_services'].queryset)
        self.assertNotIn(other_page_service, form.fields['affected_services'].queryset)

    def test_incident_admin_form_uses_checkbox_widget_for_affected_services(self):
        form = IncidentAdminForm()

        field = form.fields['affected_services']
        self.assertIsInstance(field.widget, CheckboxSelectMultiple)
        self.assertEqual(field.widget.attrs['class'], 'status-affected-services')
        self.assertEqual(
            field.help_text,
            'Leave all services unchecked when the incident affects the full status page.',
        )

    def test_incident_admin_form_rejects_affected_service_from_another_status_page(self):
        page = create_status_page()
        other_page_service = create_page_service(
            create_status_page(slug='secondary', title='Secondary Status'),
            monitor=create_monitor(name='Other', kuma_monitor_id='other', service_key='other'),
        )
        form = IncidentAdminForm(
            data={
                'status_page': page.pk,
                'title': 'Wrong service incident',
                'severity': IncidentSeverity.MAJOR,
                'phase': IncidentPhase.INVESTIGATING,
                'affected_services': [other_page_service.pk],
                'started_at': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
                'resolved_at': '',
                'summary': '',
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn('affected_services', form.errors)

    def test_incident_update_inline_requires_at_least_one_update(self):
        incident = Incident.objects.create(
            status_page=create_status_page(),
            title='Current incident',
            phase=IncidentPhase.INVESTIGATING,
        )
        formset_class = inlineformset_factory(
            Incident,
            IncidentUpdate,
            formset=IncidentUpdateInlineFormSet,
            fields=('phase', 'message', 'published_at'),
            extra=1,
        )
        formset = formset_class(
            data={
                'updates-TOTAL_FORMS': '1',
                'updates-INITIAL_FORMS': '0',
                'updates-MIN_NUM_FORMS': '0',
                'updates-MAX_NUM_FORMS': '1000',
                'updates-0-phase': '',
                'updates-0-message': '',
                'updates-0-published_at': '',
            },
            instance=incident,
            prefix='updates',
        )

        self.assertFalse(formset.is_valid())
        self.assertIn('Add at least one incident update', str(formset.non_form_errors()))


@override_settings(STORAGES=TEST_STORAGES)
class KumaMonitorAdminTests(TestCase):
    admin_host = 'admin.localhost'

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='password',
        )
        self.verify_admin_session(self.user)

    def verify_admin_session(self, user):
        device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def admin_get(self, path, **kwargs):
        return self.client.get(path, HTTP_HOST=self.admin_host, **kwargs)

    def admin_post(self, path, data=None, **kwargs):
        return self.client.post(path, data or {}, HTTP_HOST=self.admin_host, **kwargs)

    def test_kuma_monitor_changelist_shows_fetch_button(self):
        response = self.admin_get(reverse('admin:status_kumamonitor_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Fetch from Kuma status page')
        self.assertContains(response, reverse('admin:status_kumamonitor_fetch_from_kuma'))

    def test_fetch_from_kuma_admin_action_runs_sync_and_shows_success_message(self):
        result = KumaMonitorSyncResult(fetched=3, added=1, updated=1, deleted=1)

        with patch('apps.status.admin.sync_kuma_monitors_from_status_page', return_value=result) as sync:
            response = self.admin_post(reverse('admin:status_kumamonitor_fetch_from_kuma'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '..')
        sync.assert_called_once_with()
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn('Fetched 3 monitors from Kuma: 1 added, 1 updated, 1 deleted.', messages)

    def test_fetch_from_kuma_admin_action_shows_error_when_sync_fails(self):
        with patch(
            'apps.status.admin.sync_kuma_monitors_from_status_page',
            side_effect=KumaClientError('Kuma base URL is not configured.'),
        ):
            response = self.admin_post(reverse('admin:status_kumamonitor_fetch_from_kuma'))

        self.assertEqual(response.status_code, 302)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn('Could not fetch Kuma monitors: Kuma base URL is not configured.', messages)

    def test_staff_without_change_permission_cannot_fetch_kuma_monitors(self):
        staff_user = get_user_model().objects.create_user(
            username='staff',
            email='staff@example.com',
            password='password',
            is_staff=True,
        )
        self.verify_admin_session(staff_user)

        with patch('apps.status.admin.sync_kuma_monitors_from_status_page') as sync:
            response = self.admin_post(reverse('admin:status_kumamonitor_fetch_from_kuma'))

        self.assertEqual(response.status_code, 302)
        sync.assert_not_called()
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn('You do not have permission to fetch Kuma monitors.', messages)


@override_settings(STORAGES=TEST_STORAGES)
class IncidentAdminTests(TestCase):
    admin_host = 'admin.localhost'

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='password',
        )
        self.verify_admin_session(self.user)

    def verify_admin_session(self, user):
        device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def admin_get(self, path, **kwargs):
        return self.client.get(path, HTTP_HOST=self.admin_host, **kwargs)

    def test_incident_add_form_prefills_status_page_from_query_string(self):
        page = create_status_page()
        service = create_page_service(page, display_name='Website')
        other_page_service = create_page_service(
            create_status_page(slug='secondary', title='Secondary Status'),
            monitor=create_monitor(name='Other', kuma_monitor_id='other', service_key='other'),
            display_name='Other',
        )

        response = self.admin_get(
            f"{reverse('admin:status_incident_add')}?status_page={page.pk}",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'status/admin_incident.css')
        self.assertContains(response, 'status/admin_incident.js')
        self.assertContains(response, 'class="status-affected-services"')
        self.assertContains(response, 'type="checkbox"')
        self.assertContains(response, 'name="affected_services"')
        self.assertContains(response, service.display_name)
        self.assertNotContains(response, other_page_service.display_name)
        self.assertNotContains(response, 'class="selector"')
        self.assertContains(response, f'<option value="{page.pk}" selected>{page}</option>', html=True)


@override_settings(STORAGES=TEST_STORAGES)
class StatusPageAdminTests(TestCase):
    admin_host = 'admin.localhost'

    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='password',
        )
        self.verify_admin_session(self.user)

    def verify_admin_session(self, user):
        device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def admin_get(self, path, **kwargs):
        return self.client.get(path, HTTP_HOST=self.admin_host, **kwargs)

    def admin_post(self, path, data=None, **kwargs):
        return self.client.post(path, data or {}, HTTP_HOST=self.admin_host, **kwargs)

    def monitor_prefix(self, monitor):
        return f'{STATUS_PAGE_MONITOR_FIELD_PREFIX}_{monitor.pk}'

    def status_page_data(self, page, **extra):
        data = {
            'site_slug': page.site_slug,
            'slug': page.slug,
            'title': page.title,
            'description': page.description,
            'logo_url': page.logo_url,
            'back_link_label': page.back_link_label,
            'back_link_url': page.back_link_url,
            'is_visible': 'on',
            '_save': 'Save',
        }
        data.update(extra)
        return data

    def test_status_page_service_is_hidden_from_standalone_admin(self):
        self.assertFalse(admin.site.is_registered(StatusPageService))

    def test_incident_update_is_hidden_from_standalone_admin(self):
        self.assertFalse(admin.site.is_registered(IncidentUpdate))

    def test_status_page_change_form_shows_kuma_monitor_selection(self):
        page = create_status_page()
        monitor = create_monitor(name='API', kuma_monitor_id='api', service_key='api')

        response = self.admin_get(reverse('admin:status_statuspage_change', args=[page.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kuma monitors on this page')
        self.assertContains(response, 'API')
        self.assertContains(response, 'Order')
        self.assertContains(response, 'data-status-monitor-move="up"')
        self.assertContains(response, 'data-status-monitor-move="down"')
        self.assertContains(response, f'name="{self.monitor_prefix(monitor)}_position"')
        self.assertContains(response, 'type="hidden"')
        self.assertContains(response, 'status/admin_status_page.css')
        self.assertContains(response, 'status/admin_status_page.js')
        self.assertNotContains(response, 'Status page services')
        self.assertNotContains(response, '<th scope="col">Position</th>', html=True)
        self.assertNotContains(response, '<th scope="col">Visible</th>', html=True)

    def test_status_page_save_creates_selected_monitor_service(self):
        page = create_status_page()
        monitor = create_monitor(name='API', kuma_monitor_id='api', service_key='api')
        prefix = self.monitor_prefix(monitor)

        response = self.admin_post(
            reverse('admin:status_statuspage_change', args=[page.pk]),
            self.status_page_data(
                page,
                **{
                    f'{prefix}_selected': 'on',
                    f'{prefix}_display_name': 'Public API',
                    f'{prefix}_position': '2',
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        service = StatusPageService.objects.get(status_page=page, monitor=monitor)
        self.assertEqual(service.display_name, 'Public API')
        self.assertEqual(service.position, 2)
        self.assertTrue(service.is_visible)

    def test_status_page_save_updates_existing_monitor_service(self):
        page = create_status_page()
        service = create_page_service(page, display_name='Old name', position=1, is_visible=True)
        prefix = self.monitor_prefix(service.monitor)

        response = self.admin_post(
            reverse('admin:status_statuspage_change', args=[page.pk]),
            self.status_page_data(
                page,
                **{
                    f'{prefix}_selected': 'on',
                    f'{prefix}_display_name': 'New name',
                    f'{prefix}_position': '5',
                },
            ),
        )

        self.assertEqual(response.status_code, 302)
        service.refresh_from_db()
        self.assertEqual(service.display_name, 'New name')
        self.assertEqual(service.position, 5)
        self.assertTrue(service.is_visible)

    def test_status_page_save_unchecked_monitor_removes_page_selection(self):
        page = create_status_page()
        service = create_page_service(page)

        response = self.admin_post(
            reverse('admin:status_statuspage_change', args=[page.pk]),
            self.status_page_data(page),
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(StatusPageService.objects.filter(pk=service.pk).exists())

    def test_same_monitor_can_be_selected_on_multiple_pages_with_different_metadata(self):
        monitor = create_monitor(name='API', kuma_monitor_id='api', service_key='api')
        first_page = create_status_page()
        second_page = create_status_page(slug='secondary', title='Secondary Status')

        first_prefix = self.monitor_prefix(monitor)
        self.admin_post(
            reverse('admin:status_statuspage_change', args=[first_page.pk]),
            self.status_page_data(
                first_page,
                **{
                    f'{first_prefix}_selected': 'on',
                    f'{first_prefix}_display_name': 'Main API',
                    f'{first_prefix}_position': '1',
                    f'{first_prefix}_is_visible': 'on',
                },
            ),
        )
        self.admin_post(
            reverse('admin:status_statuspage_change', args=[second_page.pk]),
            self.status_page_data(
                second_page,
                **{
                    f'{first_prefix}_selected': 'on',
                    f'{first_prefix}_display_name': 'Secondary API',
                    f'{first_prefix}_position': '3',
                    f'{first_prefix}_is_visible': 'on',
                },
            ),
        )

        first_service = StatusPageService.objects.get(status_page=first_page, monitor=monitor)
        second_service = StatusPageService.objects.get(status_page=second_page, monitor=monitor)
        self.assertEqual(first_service.display_name, 'Main API')
        self.assertEqual(first_service.position, 1)
        self.assertEqual(second_service.display_name, 'Secondary API')
        self.assertEqual(second_service.position, 3)
