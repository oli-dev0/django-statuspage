# Design Decisions

## Reusable, site-scoped data

Status pages store a site slug and page slug rather than hard-coding one product. This keeps the feature reusable in a multi-site Django project while leaving site and host policy to the host project.

## Admin-managed joins

`StatusPageService` remains a database join, but monitor selection is edited from the `StatusPage` form. This matches the operator’s task and avoids an unnecessary second admin workflow.

## Kuma is optional

Current health is enriched from Kuma when configured, but rendering remains useful without it. Django stores daily aggregates because a public heartbeat endpoint is not a durable history API.

## Manual incidents take precedence

An explicitly published incident communicates operator knowledge more clearly than a transient monitor result, so incident state can override the corresponding public summary and uptime-bar presentation.

## No API in this feature

The current client is server-rendered HTML. A mobile or external client should receive a deliberately designed normalized API rather than scrape templates.
