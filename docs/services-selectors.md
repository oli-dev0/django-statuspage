# Services and Selectors

Read logic is concentrated in `status/selectors.py` and the Kuma integration in `status/kuma.py`.

- `get_visible_status_page_for_site()` resolves the requested visible page.
- `get_status_page_services()` filters page selections to visible, available services and preserves editorial order.
- Incident selectors separate active and resolved incidents and apply the configured limits.
- Status aggregation gives manual incident state priority over transient Kuma health.
- Kuma client helpers normalize upstream payloads and return a safe unavailable result on timeout, HTTP failure, or invalid JSON.

The view should assemble a public context through these selectors rather than querying hidden services or incidents directly.
