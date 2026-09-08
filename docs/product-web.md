# Product Web Behavior

The product is a public status page for a configured website or service. Visitors can read current overall health, selected service states, recent uptime, active incidents, and resolved incident history without signing in.

The page supports an optional logo and back link, responsive layouts, light/dark appearance, browser-local timestamps, and automatic refresh. Operators manage content in Django admin; public visitors cannot change status data.

Visible pages are selected by host/site and slug. Hidden services, unavailable monitors, raw Kuma data, and admin-only fields are excluded from the public response. Missing or unavailable upstream health data is shown as an explicit unknown/fallback state rather than treated as healthy.
