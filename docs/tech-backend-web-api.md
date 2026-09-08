# Backend and Integration Details

## Stack

The app uses Django models, admin, templates, migrations, standard-library HTTP (`urllib`) for Kuma, plain CSS, and small vanilla JavaScript. It has no DRF API, frontend framework, HTMX, or Alpine.js dependency.

## Host-project integration

Install the app as `apps.status` or adjust its package imports. Include `status.urls` in the project’s localized URLconf and provide the site-registry functions imported by `models.py`, `admin.py`, and `views.py`. The host project also supplies middleware that resolves the current site from the request host.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `STATUS_KUMA_BASE_URL` | empty | Optional Kuma base URL; empty enables fallback health. |
| `STATUS_KUMA_STATUS_PAGE_SLUG` | `main` | Kuma status page used for sync and heartbeats. |
| `STATUS_KUMA_TIMEOUT_SECONDS` | `3` | Kuma request timeout. |
| `STATUS_KUMA_CACHE_SECONDS` | `300` | Cache lifetime for current Kuma health. |

Do not place credentials in source, templates, fixtures, or logs. The current public Kuma endpoints do not require credentials.

## Rendering and freshness

Selectors enforce site scope, visibility, service availability, editorial order, and incident precedence before the view renders `status/detail.html`. Current Kuma health is cached. Daily uptime is stored by `sync_status_uptime`; the browser refreshes the full page periodically and formats timestamp labels in its local timezone.

## Static assets

`status.css` and `status.js` own the public page. `admin_status_page.*` owns monitor ordering controls, and `admin_incident.*` owns incident-form enhancements. These assets are app-local and do not require a particular admin theme.

## Operational boundaries

The integrating project is responsible for HTTPS, allowed hosts, status-host routing, scheduled command execution, database backups, static collection, and authentication for Django admin. The status app itself exposes only public GET rendering and protected admin workflows.
