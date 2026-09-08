# Django Status Page

A public status page for Django projects.

It gives visitors one simple place to check whether your website or services are working, whether there is an active incident, and how things have been behaving recently.

## What it includes

- A public page showing the overall status of your services.
- Service health supplied by [Uptime Kuma](https://github.com/louislam/uptime-kuma), when you want to use it.
- A 60-day view of recent uptime.
- Incidents with updates, affected services, severity, and progress.
- A Django admin screen where you choose and order the services shown publicly.
- Responsive HTML, a light/dark theme, and visitor-friendly timestamps.
- A fallback mode that still shows your page when Kuma is unavailable or not configured.

## What this repository is

This is a showcase of the status-page feature, rather than a complete Django project that you can clone and run immediately.

It does not include a `manage.py`, Django settings, a database, or a user login system. Those parts belong to the larger project where the feature is installed. The useful part here is the app itself: the models, admin workflow, templates, static files, migrations, and tests.

## Using it in your own project

The basic process is:

1. Copy the `status/` folder into your Django project, usually as `apps/status/`.
2. Add `apps.status` to `INSTALLED_APPS`.
3. Include `status.urls` in your project’s URL configuration.
4. Connect the app to your project’s way of identifying a site from a request host.
5. Run the included migrations and collect static files.
6. Open Django admin, create a status page, and choose the services it should show.

The app supports more than one website in the same Django project. If your project only has one website, you can keep the same idea with a single site entry.

There is one important integration detail: this showcase expects the host project to provide the site lookup helpers imported by `status/models.py`, `status/admin.py`, and `status/views.py`. A different Django project will need to connect those imports to its own site and host setup.

The [`examples/host_integration/`](examples/host_integration/) folder provides
copyable URL and Kuma settings snippets plus the exact site-registry interface
the host must supply.

## Uptime Kuma

Kuma is optional. Without it, you can create services in Django admin and use the status page with its fallback health state.

For Kuma-backed health and uptime history, configure these environment settings in the host project:

```text
STATUS_KUMA_BASE_URL=http://uptime-kuma:3001
STATUS_KUMA_STATUS_PAGE_SLUG=main
STATUS_KUMA_TIMEOUT_SECONDS=3
STATUS_KUMA_CACHE_SECONDS=300
```

The app reads Kuma’s public status-page endpoints. It does not need Kuma credentials for the current integration. The scheduled `sync_status_uptime` management command stores daily history in Django so the page is not limited to Kuma’s short public heartbeat window.

## Documentation

If you want to look closer at the implementation, start here:

- [Create a status page](docs/create-status.md)
- [How the app works](docs/overview.md)
- [Integration details](docs/tech-backend-web-api.md)
- [Database models](docs/database.md)
- [Public HTML behavior](docs/api.md)
- [Tests](docs/tests-backend-web-api.md)
- [Design decisions](docs/decisions.md)

## License

Released under the [MIT License](LICENSE).
