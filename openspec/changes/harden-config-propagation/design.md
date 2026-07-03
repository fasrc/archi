## Context

A live `archi-dev` deployment served Qwen3 chain-of-thought in chat answers for two days because the running chat process used provider config it had cached at boot, from before `chat_template_kwargs.enable_thinking: false` was seeded. Grounded investigation established the mechanics:

- **Serving model**: the chat app is a **single-process Flask dev server** (`src/bin/service_chat.py:47-52`, `app.run(debug=True, use_reloader=False)`), no gunicorn. It reads config from Postgres exactly once at boot (`get_full_config()`), cached in two stacked layers: `ChatWrapper._config_cache` (app.py:383, per-`config_name`) and `ConfigService._static_cache` (config_service.py:140, under `get_static_config()`).
- **Config source of truth is Postgres `static_config`**, seeded by a one-shot `config-seed` container (`base-compose.yaml:106-131`, `restart: "no"`, `command: python -m src.cli.tools.config_seed`) that UPSERTs the rendered `config.yaml` into `static_config` (config_service.py:405-461). The chatbot container `depends_on config-seed: service_completed_successfully` and reads Postgres at boot.
- **What already live-reloads**: the agent spec (on file mtime, app.py:416-437) and `dynamic_config` (queried every turn). Static/services config — `providers.*.extra_kwargs`, including `enable_thinking` — is the only frozen layer.
- **The deploy default already force-recreates**: `DeploymentManager.start_deployment()` (deployment_manager.py:48-51) defaults `ARCHI_COMPOSE_UP_FLAGS="--build --force-recreate --always-recreate-deps"`. So a normal `redeploy.sh` run re-runs `config-seed` and recreates `chatbot`. **The incident came from bypassing that path** — a bare `docker restart chatbot-dev` (which re-reads Postgres but does NOT re-run the one-shot `config-seed`, so it re-reads *stale* rows) or an ambient `ARCHI_COMPOSE_UP_FLAGS` override that strips `--force-recreate` (as CI does: `.github/workflows/test-and-build-tag.yml:161`).

This design closes the deploy-bypass hole and the observability gap. A restartless live-reload for the residual "re-seed without chatbot restart" case was evaluated and deliberately deferred (see Decision 3).

## Goals / Non-Goals

**Goals:**
- A config change reliably reaches the running chat process, or the deploy visibly fails — never silent staleness.
- The effective loaded config is inspectable in one command (`docker logs | grep`), not reconstructed by hand.
- Post-deploy verification asserts the toggle under test (e.g. no `</think>` leakage) against a live turn.
- Minimal blast radius: no needless Postgres bounce, no schema risk to the live DB, PRs that separate mechanical churn from behavior change.

**Non-Goals:**
- Bare `config.yaml` edits taking effect without a re-seed. Config still flows through Jinja2 rendering → `config-seed` → Postgres; this is intentional (`dev-mode-mounts` spec) and unchanged.
- Restartless "live reload" of static config while the process keeps running. Considered and deferred (Decision 3); config changes take effect via the guaranteed re-seed + recreate on redeploy.
- Changing global prod recreate semantics in `deployment_manager.py` (scope creep; the default is already correct).

## Decisions

### Decision 1 — Layer 2 (deploy restart guarantee) is the primary fix; scoped to the dev deploy path

Close the two bypass holes without cascading to Postgres:

- **`deploy/fasrc-dev/scripts/redeploy.sh`**: after `archi_deploy`, add an explicit targeted recreate:
  ```
  docker compose -f "$HOME/.archi/archi-$DEPLOYMENT/compose.yaml" --env-file "$ENV_FILE_ABS" \
    up -d --force-recreate config-seed chatbot
  ```
  This makes "re-seed then re-read" deterministic **independent of the compose default and independent of a bare `docker restart`**. Compose honors `depends_on` ordering (postgres healthy → config-seed re-runs the one-shot and re-seeds `static_config` → chatbot recreated and re-reads it). Deliberately **omit `--always-recreate-deps`** and **do not name `postgres`/`data-manager`**, so the DB and corpus containers are not bounced.

**Why not** pin `ARCHI_COMPOSE_UP_FLAGS` with `--always-recreate-deps` in `lib.sh` *as well*: reviewer flagged this as self-contradicting — it reintroduces the Postgres/data-manager bounce the targeted command avoids. Pick one lane; the targeted `up -d` is the minimal one. **Why not** harden `deployment_manager.py:48` globally: it changes prod recreate semantics to fix a dev incident; the default there is already correct. (Alternative kept open: if a source-level guarantee is later wanted, make `deployment_manager.py` always *append* `--force-recreate` to any env override while leaving `--always-recreate-deps` opt-in — closes the CI-override hole without the Postgres cascade.)

### Decision 2 — Layer 3 (observability + verification), two zero-risk items

- **Boot-time effective-config log** in `FlaskAppWrapper.__init__`, after `self.chat.update_config(...)` (app.py:~2559, where `config_version` and the resolved providers block are already in scope): one `logger.info` emitting `default_provider`, the effective `extra_kwargs` for that provider, `config_version`, and a `sha256` of the sorted providers block. On the single-process dev server this one line authoritatively describes what the live process loaded, making `docker logs chatbot-dev | grep enable_thinking` (or the hash) the check that replaces hand-reconstruction.
- **Verify-skill assertion**: extend `archi-dev-deploy-verify` to (a) assert the smoke-test answer body contains no `</think>` / bare chain-of-thought leakage (pure string check, no code change), and (b) grep the boot log (or health field) for the expected toggle.

**Optional**: a machine-readable field on `/api/health` (app.py:3386). The endpoint is **public/unauthenticated** (registered at app.py:2592, above the `require_auth` block), so expose only a boolean `enable_thinking` + a providers-block hash — never raw `extra_kwargs`. Isolated to its own PR so the public-endpoint discussion doesn't block the rest.

### Decision 3 — Live restartless static-config reload: considered and DEFERRED (out of scope)

A restartless "live reload" — a per-turn probe that reloads static config while the process keeps running — was evaluated and deliberately excluded. Rationale from the adversarial review (all three reviewers concurred):

- **Redundant with Layer 2 for this incident.** Static config only changes via a re-seed; a correct Layer 2 couples that re-seed to a chatbot recreate, so the process already re-reads config. Live-reload uniquely serves only the "re-seed without a chatbot restart" case (selective deploy, admin `/api/update_config`, manual `config-seed`) — none of which was the incident, and no requirement for it exists today.
- **Highest risk per unit of value.** It requires a `static_config` schema migration (the table has no change signal — `config_version` is a hardcoded constant, `created_at` is pinned by the UPSERT), a per-turn DB probe on the hot path, and a mid-turn cache + pipeline swap. The naive placement has a genuine **within-turn split-brain bug**: the probe must run at the *top* of `update_config()` (before app.py:404) or `config_payload` binds from the stale cache while `archi.update()` re-reads `_static_cache` independently; it must invalidate *both* the `ConfigService._static_cache` and `ChatWrapper._config_cache` layers; the admin trigger must bump the DB signal (not a local cache) to survive a future multi-worker move; and the `archi.update()` pipeline swap races concurrent `invoke`/`stream`.
- **Semantics change.** It erodes the "restart-to-apply" boundary that `dev-mode-mounts` and the selective-service-deploy work implicitly rely on, letting a session adopt new provider/thinking behavior mid-conversation.

If a concrete requirement for restartless config changes arises later (e.g. an admin "reload config" button), this should be revisited as its own change — split into an inert migration (add a content-gated `static_config.updated_at` via `_ensure_config_tables` ALTER, matching the runtime `TIMESTAMP` type) and a separately-reviewed behavior PR (top-of-`update_config()` probe, two-cache invalidation, DB-mediated admin trigger, synchronized pipeline swap). It is not part of this change.

## Risks / Trade-offs

- **Downtime (Layer 2)** → force-recreating `chatbot` stops/starts the single Flask process for a few seconds to ~a minute. Acceptable for a dev redeploy; the targeted command avoids bouncing Postgres, minimizing it.
- **Broken config now blocks redeploys visibly (Layer 2)** → `chatbot depends_on config-seed: service_completed_successfully`; a bad `config.yaml` that fails `config_seed` blocks chatbot start. This is existing behavior surfaced more often; treat the visible failure as a feature (fail-loud beats silent staleness).
- **Public `/api/health` (Layer 3 optional)** → expose only boolean + hash, never raw `extra_kwargs`; or gate behind auth. Keep in its own PR.
- **Residual "re-seed without restart" gap** → because restartless live-reload is deferred (Decision 3), a re-seed that is not followed by a chatbot recreate still serves stale config. This is exactly why Layer 2 guarantees the recreate and Layer 3 makes the drift observable/verifiable; the gap is closed by procedure + verification, not by hot-reload.

## Migration Plan

Ship in dependency order; stop early if the cheap layers suffice:

1. **PR1 — Layer 2 (deploy scripts only)**. Shell-only, no app/DB change. Closes the incident. Verify with a redeploy + `docker inspect` showing `chatbot` recreated and `config-seed` re-run.
2. **PR2 — Layer 3a (observability)**. Additive `logger.info` + `archi-dev-deploy-verify` skill edits (`</think>` assertion + boot-log grep). Pure logging/doc.
3. **PR3 (optional) — Layer 3b `/api/health` field**. Boolean + hash only.

**Rollback**: PR1 revert restores the prior scripts (the default deploy still force-recreates). PR2/PR3 are additive and independently revertible. No schema change is introduced by this change, so there is nothing to migrate back.

## Open Questions

- If Layer 2 wants a source-level guarantee against the CI env-override, harden `deployment_manager.py` to always append `--force-recreate` (keeping `--always-recreate-deps` opt-in) — or leave it scoped to `fasrc-dev` scripts only? (This change keeps it scoped to the `fasrc-dev` scripts.)
- Verify harness endpoint: drive `/api/get_chat_response` (simple JSON, what the skill documents) vs `/api/get_chat_response_stream` (NDJSON) — pick the non-stream one for a simpler string check unless streaming is under test.
- For the boot-log/health surface: expose the resolved `enable_thinking` boolean explicitly (highest signal for the `</think>` assertion) plus a providers-block hash (catches any drift)?
