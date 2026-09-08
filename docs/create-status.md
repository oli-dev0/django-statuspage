# Create a Status Page

This checklist assumes the app has been integrated into an existing Django project.

## 1. Migrate and configure

Run the host project’s migrations. For Kuma-backed health data, configure safe environment values such as:

```text
STATUS_KUMA_BASE_URL=http://uptime-kuma:3001
STATUS_KUMA_STATUS_PAGE_SLUG=main
STATUS_KUMA_TIMEOUT_SECONDS=3
STATUS_KUMA_CACHE_SECONDS=300
```

Leave `STATUS_KUMA_BASE_URL` empty to exercise the database-backed fallback locally.

## 2. Import or create monitors

Use the Django admin `Fetch from Kuma` action, or create a `KumaMonitor` manually for local testing. Imported monitor IDs are stable external identifiers; public descriptions can be edited without being overwritten by later syncs.

Run `python manage.py sync_status_uptime` on a regular schedule if daily history is required.

## 3. Create and configure the page

Create a `StatusPage` with a registered `site_slug` and `slug=main` for the default page. Add its public title and optional description, logo URL, and back link. Select visible monitors, set their public labels and order, then enable `is_visible`.

## 4. Publish incidents

Create an incident with a severity, phase, start time, summary, and affected services. Add an update before saving. Resolve the incident by choosing the resolved phase; a missing resolved timestamp is filled automatically.

## 5. Verify

Check the host project’s localized status route, canonical URL, noindex metadata, service visibility, incident timeline, uptime bars, and fallback behavior when Kuma is unavailable. Run the focused Django tests listed in [tests-backend-web-api.md](tests-backend-web-api.md).
