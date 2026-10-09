## Decisions

### D1. Trust the socket peer, never a header

`request.remote_addr` is the TCP peer: the chat app installs no `ProxyFix`, so a client cannot set it. `ipaddress.ip_address(...).is_loopback` accepts `127.0.0.0/8` and `::1`; an empty, unparsable, link-local, or IPv4-mapped non-loopback address is refused.

### D2. A forwarding header means "not local"

A reverse proxy on the same host connects from loopback, so its requests would pass a peer check alone. Any of `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host`, `X-Real-IP` makes the request non-local. The Slack bot sends none of them (it sends `X-OpenWebUI-Chat-Id` and, with auth on, `Authorization`).

Limit: a same-host relay that adds no forwarding header (a bare proxy, a port forwarder, an SSRF-capable local service) passes. This is an application-layer gate, not a network boundary: in host mode the chat UI and `/v1` share one listener on 7861, which users must reach, so a port-level control cannot separate them.

Evidence for dev (2026-10-08, probed from claw): users reach the chat app directly — `http://archi.rc.fas.harvard.edu:7861/` answers 200 with `Server: Werkzeug/3.1.8`, nothing answers on 80 or 443, and `/v1/models` answers 404 (off). So no relay fronts 7861 today. The rollout verifies the running service from off the host on every public path (`/v1/models` → 403) and on the host (`localhost:7861/v1/models` → 200), and must be re-run if a proxy is ever put in front of the chat app.

### D3. The gate runs first

A `before_request` on the blueprint answers before `require_bearer_auth` and before body validation, so a remote caller gets the same 403 on every `/v1` path and learns nothing about auth or the request schema.

### D4. Off by default, opt-in per deployment

`local_only` defaults to `false`, so existing `/v1` deployments (claw, Open WebUI users) do not change. It is a deployment setting in the stored config, like `enabled`.

Rejected: a CIDR allow-list. Only the same-host case is needed now; in bridge mode the bot is a container peer, and authentication is the right control there.

## Risks

- **Config ahead of code.** An older chat app ignores `local_only` and would serve an open `/v1` if `enabled: true` is deployed with it. Mitigation, enforced by git order rather than procedure: dev's config pin (`deploy/scripts/lib.sh`) lives in the same checkout that builds the chat app, so the pin bump PR that points dev at the new `dev.yaml` can only land on a `dev` that already has this change; its test fails unless the gate is present. `redeploy.sh` (`archi create --force`) recreates the chatbot, so no older process keeps serving. Bypassed only by a hand-set `CONFIG_REF`/`CONFIG_SHA` or a hand-moved `config/` checkout on an old checkout.
