# Database

The migrations create five related model areas:

- `StatusPage`: public page metadata and site/slug visibility.
- `KumaMonitor`: imported or manually configured service identity and availability.
- `StatusPageService`: per-page monitor selection, label, order, and visibility.
- `KumaMonitorDailyUptime`: one aggregate per monitor and calendar day.
- `Incident` and `IncidentUpdate`: public operational communication.

Uniqueness constraints prevent duplicate page slugs per site, duplicate monitor identities, duplicate monitor selections, and duplicate daily uptime rows. Foreign keys cascade page-owned content and protect selected monitors from accidental deletion.

The host project owns the database engine and migration execution. Run all included migrations before using the admin.
