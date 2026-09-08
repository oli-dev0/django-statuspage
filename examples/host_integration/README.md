# Host Integration Reference

These snippets are intended for an existing Django project.

1. Copy `status/` to `apps/status/` so its current package imports remain valid.
2. Add `apps.status.apps.StatusConfig` to `INSTALLED_APPS`.
3. Include `apps.status.urls` as shown in `urls.py`.
4. Implement the `apps.core.sites` interface listed in `site_contract.py`, or
   adapt those imports to the host project's site registry.
5. Run migrations and collect static files.
6. Optionally configure Kuma and schedule `sync_status_uptime` after verifying
   the internal Kuma URL from the Django runtime.

Kuma is optional. Without it, Admin-managed services and incidents continue to
render with their fallback health state.
