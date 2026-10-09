# Tasks — v1-local-only

TDD in every group: failing test first, watch it fail, then implement. Gate:
`bash scripts/gate.sh`.

## 1. The gate

- [x] 1.1 `model: sonnet` — Red: `tests/unit/test_openai_compat_local_only.py` — loopback (127.0.0.1, 127.0.0.53, ::1) answered; remote, link-local, IPv4-mapped, empty, and unparsable peers get 403 `permission_error`; each forwarding header refused from loopback; refused before validation (POST {} → 403, not 400) and before auth (403, not 401); loopback with auth on still needs a token; other routes not gated; a later registration without `local_only` turns it off.
- [x] 1.2 `model: sonnet` — `local_only` in `register_openai_compat`, `_is_local_request`, and a blueprint `before_request`. 1.1 passes; the existing `/v1` suites pass.

## 2. Wiring

- [x] 2.1 `model: sonnet` — Red: `openai_compat_options` (local_only true/false/null/absent, token_ttl_days kept), `app.py` passes `**openai_compat_options(openai_compat_config)`, and `base-config.yaml` renders `local_only` (true / false / null / absent).
- [x] 2.2 `model: haiku` — Implement `openai_compat_options`, the `app.py` call site, and the template line. 2.1 passes.

## 3. Docs

- [x] 3.1 `model: haiku` — `api-reference-v1.md` "Local callers only"; the Slack prerequisites and config block in `services.md`; `configuration.md` `services.slack` line.
