## Why

A live deployment (`archi-dev`) served chat answers polluted with Qwen3 chain-of-thought for two days because the running process was using configuration it had cached at boot — from *before* `chat_template_kwargs.enable_thinking: false` was added. The flag was correct in `config.yaml` and in Postgres `static_config`, the code forwarded it correctly, and a direct request to vLLM honored it — yet the live agent never applied it, because the chat app caches static/services config once at startup and never refreshes it, and the deploy that changed the config never restarted the process. Nothing detected the drift. This is a systemic trap: any change to provider settings (model, base URL, `extra_kwargs`, timeouts) can silently fail to take effect, with no signal that the running process disagrees with the stored config.

## What Changes

- **Deploy restart guarantee**: the dev redeploy SHALL guarantee the chat service process is re-seeded and recreated after config changes, so a config change can never be silently ignored by a still-running process — closing the two bypass holes (a bare `docker restart` that skips re-seeding, and an ambient `ARCHI_COMPOSE_UP_FLAGS` override that strips `--force-recreate`). Postgres and ingested-corpus data volumes SHALL be preserved and not bounced.
- **Config observability + post-deploy verification**: the chat app SHALL log the effective resolved provider config (and a stable config fingerprint) at startup so the loaded config is greppable from container logs, and post-deploy verification SHALL assert the feature toggle of interest against a live turn (e.g. no `</think>` leakage) so drift is caught immediately rather than days later.

Deliberately **out of scope**: a restartless "live reload" of static config while the process keeps running. It was evaluated (see design.md) and deferred — it is redundant with the deploy restart guarantee for this incident, carries the most risk (schema migration, per-turn DB probe, mid-turn cache/pipeline swap), and no requirement for restartless config changes exists today.

## Capabilities

### New Capabilities
- `config-propagation`: Guarantees that a configuration change reaches the running chat process — via a deploy that always re-seeds and restarts the service, plus observability and post-deploy verification that surface any drift between stored and effective config.

### Modified Capabilities
<!-- No existing spec's REQUIREMENTS change. dev-mode-mounts states "config changes require redeploy; this is intentional" — this change is compatible: redeploy still re-seeds Postgres; this change ensures the running process then actually notices, and does not introduce config bind-mounts. -->

## Impact

- **Deploy tooling**: `deploy/scripts/redeploy.sh` / `lib.sh` (guarantee re-seed + chat-service recreate after a config change, scoped so Postgres/data-manager are not bounced).
- **Code**: `src/interfaces/chat_app/app.py` (startup effective-config log; optional read-only field on the public `/api/health`). No schema change and no runtime config-cache behavior change.
- **Verification**: the `archi-dev-deploy-verify` skill (assert the toggle against a live turn, e.g. no `</think>` leakage; grep the boot log for the loaded toggle).
- **Data**: none destroyed — data volumes preserved and not force-recreated.
- **Risk surface**: brief chat-UI downtime on redeploy while the single Flask process restarts; a broken `config.yaml` now fails the redeploy visibly (fail-loud, preferred over silent staleness).
