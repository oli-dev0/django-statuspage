"""Functions the host's ``apps.core.sites`` integration must provide.

Site definition objects must expose at least ``slug`` and the host/status-host
metadata needed by the consuming project.
"""

# Required by status/models.py:
# get_site_definition(site_slug) -> SiteDefinition

# Required by status/admin.py:
# get_site_slug_choices() -> iterable[tuple[str, str]]

# Required by status/views.py:
# get_status_host_for_host(host) -> str | None
# is_status_host(host) -> bool
# normalize_host(host) -> str
# require_site_for_host(host) -> SiteDefinition
