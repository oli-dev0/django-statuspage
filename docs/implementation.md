# Implementation Guide

## Install the app

Copy `status/` into the host project, commonly as `apps/status/`. Add the app to `INSTALLED_APPS`, include its URLconf in the project’s localized URLs, run the migrations, and collect static files using the host project’s normal commands.

The app imports a site-registry integration from `apps.core.sites`. Either provide that module or adapt the imports to the host project’s equivalent. The registry must identify the current site, configured status hosts, and valid site slugs.

## Admin workflow

1. Configure or import `KumaMonitor` records.
2. Create a `StatusPage` for a registered site slug.
3. Select, label, order, and enable monitors on the page form.
4. Mark the page visible when it is ready.
5. Create incidents and at least one `IncidentUpdate` when communicating an issue.

`StatusPageService` is an internal join model and is managed from the status-page form rather than exposed as a separate editorial workflow.

Cloned inline selects use native controls by default. If a host Admin theme
adds an adjacent `.admin-action-select` dropdown, the script reconnects that
markup by structure without requiring a theme-specific data attribute.

## Kuma integration

The optional client reads Uptime Kuma’s public status-page and heartbeat endpoints. If no Kuma URL is configured or the upstream is unavailable, the page still renders database-backed services with an unknown/fallback health state. The scheduled `sync_status_uptime` command stores daily aggregates so the public page is not dependent on Kuma’s short heartbeat window.

## Routes

The standard routes are `/<language>/status/` and `/<language>/status/<slug>/`. A host project may map a dedicated status host’s language root to the `main` page and redirect normal-site status paths to that host. That host behavior depends on the site-registry middleware supplied by the integrating project.
