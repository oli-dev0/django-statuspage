# Overview

This repository contains a reusable Django status-page app. It renders public, read-only pages for one or more configured sites and provides Django admin tools for service selection and incident publishing.

The app is intentionally not a complete Django project. It is designed to be integrated into an existing project that owns settings, URL routing, authentication, site/host resolution, and deployment.

## Public behavior

Each visible `StatusPage` is selected by the current site and page slug. The default slug is `main`. A page can display selected Uptime Kuma monitors, normalized current health, daily uptime summaries, active incidents, and resolved incident history.

Pages are server-rendered, public, responsive, and marked `noindex, nofollow` because they are operational pages rather than search content.

## Main modules

- `status/models.py`: pages, monitors, uptime aggregates, incidents, and updates.
- `status/admin.py`: editorial forms and monitor synchronization actions.
- `status/selectors.py`: visibility, ordering, status precedence, and context assembly.
- `status/kuma.py`: public Uptime Kuma JSON client and uptime synchronization.
- `status/views.py`: host-aware public rendering and redirects.
- `status/templates/`: public page and reusable partials.
- `status/static/`: public and admin CSS/JavaScript.

See the [README](../README.md) for the host-project integration contract.
