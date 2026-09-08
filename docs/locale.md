# Localization

The app uses Django’s translation tools for labels and status copy. The host project owns `LANGUAGES`, locale middleware, URL prefixes, translation files, and translated content policy.

The implementation is compatible with localized routes such as `/<language>/status/`. It does not assume a specific set of languages or domains. Browser JavaScript formats incident and uptime timestamps in the visitor’s timezone while server-side grouping uses Django’s configured timezone.
