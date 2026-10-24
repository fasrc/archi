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

### D5. `local_only` alone turns `/v1` on, so a config ahead of its code fails closed

`/v1` is registered when `enabled` OR `local_only` is true (`openai_compat_wanted`). A deployment that wants a local-only `/v1` sets `local_only: true` and leaves `enabled` unset. A chat app older than this change reads only `enabled`, so that config keeps `/v1` OFF there (the Slack bot cannot start, which is visible and safe) instead of serving an unauthenticated `/v1` to the network. This holds on every path, including a hand-set `CONFIG_REF`/`CONFIG_SHA` and a raw `archi create`, with no version handshake. Review round 2 asked for a fail-closed guard; this is it.

### D6. A headerless same-host relay is out of scope (declined, review rounds 1–2)

A relay on the host that adds no forwarding header (an SSH `-L`, a bare TCP forwarder, a sidecar) would pass the gate. Declined because:
- **No escalation.** Whoever can run such a relay on dev already has a shell on dev and can call `localhost:7861` directly.
- **The network is already narrowed.** dev's firewall (`deploy/scripts/firewall.sh`) opens 7861 only to Harvard and FASRC VPN ranges. The 2026-10-08 probe shows nothing fronts 7861.
- **`/v1` adds no capability beyond what dev already serves without auth.** dev's chat UI and its `/api` endpoints on 7861 run the same chat pipeline and are unauthenticated for every caller that can reach 7861 — every VPN user, and every local user or process on the node. A relay, a forwarding-only SSH account, or a local process that reaches `/v1` gets only a more convenient API to the same thing. `local_only` keeps the network-facing OpenAI-compatible API off (no client tooling pointed at dev from outside); it is not, and is not claimed to be, an access-control boundary. Review round 3 and the archi-config review raised the shared-node form of this; same answer.
- **The alternatives are the ones the operator set aside.** A separate private listener for the bot, or bearer auth on `/v1`, are the operator's other options (2026-10-08 decision: local-only). If dev ever puts a proxy in front of the chat app, turn on auth instead.

## Risks

- **Config ahead of code.** Closed by D5: dev's config sets `local_only` without `enabled`, so an older chat app keeps `/v1` off. Only a config that sets BOTH keys reaches the old open behavior on old code; the docs say not to, and fasrc/archi-config's `test_host_portable.sh` fails if `environments/dev.yaml` sets `enabled` beside `local_only`. Rendering `enabled: false` in the template would not help: the template ships with the code, and the new code already gates. A second layer, enforced by git order: dev's config pin (`deploy/scripts/lib.sh`) lives in the same checkout that builds the chat app, so the pin bump PR that points dev at the new `dev.yaml` can only land on a `dev` that already has this change; its test fails unless the gate is present. `redeploy.sh` (`archi create --force`) recreates the chatbot, so no older process keeps serving. Bypassed only by a hand-set `CONFIG_REF`/`CONFIG_SHA` or a hand-moved `config/` checkout on an old checkout.
