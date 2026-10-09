## Decisions

### D1. Trust the socket peer, never a header

`request.remote_addr` is the TCP peer: the chat app installs no `ProxyFix`, so a client cannot set it. `ipaddress.ip_address(...).is_loopback` accepts `127.0.0.0/8` and `::1`; an empty, unparsable, link-local, or IPv4-mapped non-loopback address is refused.

### D2. A forwarding header means "not local"

A reverse proxy on the same host connects from loopback, so its requests would pass a peer check alone. Any of `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Real-IP` makes the request non-local. The Slack bot sends none of them (it sends `X-OpenWebUI-Chat-Id` and, with auth on, `Authorization`).

Limit: a same-host proxy that adds no forwarding header passes. Documented as a warning; dev serves the chat app on 7861 directly.

### D3. The gate runs first

A `before_request` on the blueprint answers before `require_bearer_auth` and before body validation, so a remote caller gets the same 403 on every `/v1` path and learns nothing about auth or the request schema.

### D4. Off by default, opt-in per deployment

`local_only` defaults to `false`, so existing `/v1` deployments (claw, Open WebUI users) do not change. It is a deployment setting in the stored config, like `enabled`.

Rejected: a CIDR allow-list. Only the same-host case is needed now; in bridge mode the bot is a container peer, and authentication is the right control there.

## Risks

- **Config ahead of code.** An older chat app ignores `local_only` and would serve an open `/v1` if `enabled: true` is deployed with it. Mitigation: dev's config change ships in a separate archi-config pin, deployed only after this change is on the dev host's checkout (the dev pin bump PR says so).
