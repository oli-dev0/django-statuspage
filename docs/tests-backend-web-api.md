# Backend and Web Tests

The copied test suite covers models, admin forms, selectors, Kuma integration, management behavior, and rendered Django responses:

- `tests/test_models.py`
- `tests/test_admin.py`
- `tests/test_selectors.py`
- `tests/test_kuma.py`
- `tests/test_views.py`
- `tests/helpers.py`

Important scenarios include visibility and site scoping, monitor ordering and availability, incident validation and updates, Kuma failures and synchronization, fallback rendering, noindex metadata, canonical URLs, and protection of hidden services.

The tests depend on the integrating project’s Django settings, URLconf, site registry, and authentication stack. Adapt `apps.status` and site-registry fixtures before running them in another project. Run the host project’s focused Django test command for these modules.
