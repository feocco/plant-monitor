# Security

Plant Monitor runs inside the private homelab and exposes a small HTTP surface
for health checks, service docs, OpenAPI, and guarded watering callbacks.

## Trust Boundaries

- Home Assistant is the source of truth for plant state and pump control.
- Homelab Functions sends phone notifications and action callbacks.
- Plant Monitor accepts watering callbacks only through its HTTP server.
- Deployment secrets and runtime config are owned outside this repo by
  `homelab-config`.

## Inbound HTTP

- `GET /health`, `GET /docs`, and `GET /openapi.json` are read-only and do not
  require the callback token.
- `POST /water/{plant_id}` can run a watering action. When
  `SERVICE_CALLBACK_TOKEN` is configured, callers must send either
  `X-Plant-Monitor-Token` or the `token` query parameter.
- Prefer the `X-Plant-Monitor-Token` header for callback clients because URLs
  can be logged by proxies and tools.

## Credentials

Keep Home Assistant tokens, Homelab Functions tokens, callback tokens, and
OpenAI keys in ignored local `.env` files or deployment secrets. Do not commit
real credentials or runtime state.

## External Calls

The service calls Home Assistant, Homelab Functions, and optionally OpenAI for
notification wording. Network failures should degrade to skipped notifications
or plain local wording rather than bypassing watering guards.
