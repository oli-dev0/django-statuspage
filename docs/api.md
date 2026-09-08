# Public HTML Contract

There is no DRF API. The public surface is server-rendered HTML:

```text
GET /<language>/status/
GET /<language>/status/<slug>/
```

Authentication is not required. Only a visible page matching the current site and slug is returned. Missing, invisible, or cross-site pages return 404. A host project may add dedicated status-host redirects around these routes.

The HTML exposes only configured visible services and public incident content. Kuma URLs, raw upstream payloads, unavailable monitors, and admin-only fields are never rendered.
