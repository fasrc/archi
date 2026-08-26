## 1. PR1 — Layer 2: deploy re-seed + chat-process restart (incident fix, shell-only)

- [x] 1.1 In `deploy/scripts/redeploy.sh`, after the `archi_deploy` call, add an explicit targeted recreate: `docker compose -f "$HOME/.archi/archi-$DEPLOYMENT/compose.yaml" --env-file "$ENV_FILE_ABS" up -d --force-recreate config-seed chatbot` (service names `config-seed` and `chatbot`; do NOT pass `--always-recreate-deps`; do NOT name `postgres`/`data-manager`).
- [x] 1.2 Confirm `$DEPLOYMENT` and `$ENV_FILE_ABS` are in scope in `redeploy.sh` (sourced from `lib.sh`); wire them through if not.
- [x] 1.3 Do NOT pin `ARCHI_COMPOSE_UP_FLAGS` with `--always-recreate-deps` in `lib.sh` (would reintroduce the Postgres/data-manager bounce the targeted command avoids). Add a one-line comment in `redeploy.sh` explaining why the targeted `up -d` exists (closes the bare-`docker restart` and env-override bypass holes).
- [x] 1.4 Verify on `archi-dev`: run `redeploy.sh`; `docker inspect -f '{{.State.StartedAt}} {{.RestartCount}}' chatbot-dev` shows a fresh `StartedAt`; `docker ps -a | grep config-seed` shows a recent `Exited (0)`; `docker volume ls` shows Postgres/corpus volumes unchanged.
- [x] 1.5 Update `deploy/fasrc-dev` docs/README (or the deploy script header) to state: config changes must go through `redeploy.sh`; a bare `docker restart` re-reads Postgres but does NOT re-run `config-seed`, so it can serve stale config.

## 2. PR2 — Layer 3a: config observability + verify assertion (additive, no behavior change)

- [x] 2.1 In `src/interfaces/chat_app/app.py`, in `FlaskAppWrapper.__init__` after `self.chat.update_config(...)` (~line 2559, near the existing "Auth enabled" summary logs), add one `logger.info` emitting: resolved `default_provider`, that provider's effective `extra_kwargs`, `self.config['config_version']`, and `sha256(json.dumps(providers_block, sort_keys=True))`.
- [x] 2.2 Ensure the log does not dump secret-bearing kwargs verbatim: log the providers-block hash plus the specific `chat_template_kwargs`/`enable_thinking` value; keep any future sensitive kwargs hash-only.
- [x] 2.3 Verify: `docker logs chatbot-dev | grep -E "enable_thinking|providers-hash|config_version"` returns the effective loaded values on a fresh boot.
- [x] 2.4 In `~/.claude/skills/archi-dev-deploy-verify/SKILL.md`, add a post-smoke-test assertion step: the returned answer body MUST contain no `</think>` substring / bare chain-of-thought (pure string check, no code change).
- [x] 2.5 In the same skill, add a step to grep the boot log (from 2.1) for the expected toggle after redeploy, so verification checks what the process actually loaded, not just subjective behavior.
- [x] 2.6 Document the assertion harness request shape in the skill: `POST /api/get_chat_response` with `last_message: [["User","<q>"]]`, `client_id`, `client_sent_msg_ts` and `client_timeout` in epoch **milliseconds = now** (values are divided by 1000 at app.py:4483-4484; seconds/omitted → instant 408).

## 3. PR3 (optional) — Layer 3b: read-only config surface on /api/health

- [x] 3.1 In `src/interfaces/chat_app/app.py` `health()` handler (~line 3386), extend the JSON with `config_version`, current `provider/model`, a boolean resolved `enable_thinking`, and the providers-block `sha256`. Expose NO raw `extra_kwargs` (endpoint is public/unauthenticated, registered at app.py:2592 above the `require_auth` block).
- [x] 3.2 Verify: `curl -s localhost:7861/api/health` returns the added fields and no secret-bearing data.
- [x] 3.3 Update `archi-dev-deploy-verify` to optionally assert the toggle via `GET /api/health` instead of the log grep.

## 4. Workflow compliance

- [ ] 4.1 Each PR: branch from `dev`, PR targets `fasrc/archi:dev`, no `Co-Authored-By` trailer, black/isort-clean, patch-covered by tests (`bash scripts/gate.sh`). PR1 is shell-only; PR2/PR3 are additive logging/handler changes.
