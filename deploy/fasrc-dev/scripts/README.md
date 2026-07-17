# archi 'dev' deployment — management scripts

Thin, safe wrappers around the `archi` CLI for the local **`dev`** deployment
(FASRC vLLM backend). All scripts resolve the repo root from their own location,
so they run from anywhere. The deployment name is hard-wired to `dev` in
`lib.sh`, so these scripts can never affect your other deployments.

| Script | Action | Data |
|---|---|---|
| `create.sh` | Create / bring up (`archi create --hostmode --force`). Provisions `config/` first (see below). Safe to re-run. | preserved |
| `redeploy.sh` | Rebuild + re-render config + restart (picks up config/code edits). Provisions `config/` first. | preserved |
| `nuke.sh [-y]` | **Destroy everything**: containers, volumes (DB + corpus), images, files. | **WIPED** |
| `status.sh` | Read-only: containers, volumes, chat UI, LLM reachability. | — |

## Usage

```bash
deploy/fasrc-dev/scripts/create.sh      # first-time or re-run
deploy/fasrc-dev/scripts/redeploy.sh    # after editing config.yaml / code
deploy/fasrc-dev/scripts/status.sh      # check state
deploy/fasrc-dev/scripts/nuke.sh        # full teardown (asks you to type 'dev')
deploy/fasrc-dev/scripts/nuke.sh -y     # full teardown, no prompt (automation)
```

## Config provisioning (`ensure_config`)

Every `create`/`redeploy` first provisions the `config/` checkout (the private
`fasrc/archi-config` repo: source lists, environments, agent prompts) at a
**pinned, checksum-verified version** — so a fresh host never deploys against a
missing directory, and an existing host never silently follows a moved tag:

- The pin lives in `lib.sh` as `CONFIG_REF` (a tag) + `CONFIG_SHA` (the exact
  commit it must resolve to). The remote tag object is checked too — a
  re-pointed tag **aborts the deploy** naming both commit ids. An unreachable
  origin only warns (local verification still applies).
- **Local edits are never destroyed.** What happens instead depends on the dirt:

  | `config/` state | Deploy behavior |
  |---|---|
  | clean, off the pin | converges to the pin |
  | untracked files only | converges to the pin; untracked files stay in place |
  | tracked edits, HEAD **at** the pin | proceeds with the on-disk config (live-edit workflow); warns with the paths |
  | tracked edits, HEAD **off** the pin | **aborts** (would silently deploy an old base) — commit-and-sync, or `CONFIG_FORCE=1` |
  | any dirt + `CONFIG_FORCE=1` | stashes everything (`git -C config stash pop` recovers), then converges |

- Every deploy logs **provenance**: the config commit actually deployed, whether
  it matched the pin, and any dirty paths — so any deployment's exact config
  state is reconstructable from the deploy output.
- **Bumping the pin:** create a **new** tag in `fasrc/archi-config` (never move
  an existing one), update `CONFIG_REF` + `CONFIG_SHA` in `lib.sh` in the same
  PR, then deploy.
- **Self-test:** `bash deploy/fasrc-dev/scripts/test_ensure_config.sh` — 10
  cases against a local fixture repo; no network, never touches the real
  checkout.
- Raw `archi create` **bypasses all of this** — see the warning in
  `config.example.yaml`.

## Notes

- **VPN required for chat.** The LLM endpoint is VPN-only; `create`/`redeploy`/
  `status` warn if it's unreachable but do not block.
- **`create` vs `redeploy`** run the same `archi create --force` underneath
  (archi has no separate redeploy verb); both preserve data volumes. Only
  `nuke` removes volumes.
- **Config changes must go through `redeploy.sh`.** The running config is read
  from Postgres `static_config` at chat-process boot, seeded by the one-shot
  `config-seed` container. A bare `docker restart chatbot-dev` re-reads Postgres
  but does **not** re-run `config-seed`, so it can come up serving *stale* config
  (this caused a two-day `enable_thinking` leak). `redeploy.sh` ends with an
  explicit `docker compose up -d --force-recreate config-seed chatbot` so the
  re-seed always precedes the chat restart — do not hand-edit `config.yaml` and
  bounce the container instead.
- **`nuke` is irreversible** — it wipes the Postgres DB and the ingested corpus.
  The next `create` re-ingests and rebuilds images from scratch (slow).
- Config: `../config.yaml` (git-excluded — host-specific). First-time setup:
  `cp ../config.example.yaml ../config.yaml` and fill in the LLM host, paths, etc.
- Secrets: an env file with `PG_PASSWORD` (required) plus `HUIT_API_KEY` /
  `ANTHROPIC_API_KEY` as needed. Defaults to `~/.secrets/archi-secrets.env`;
  override with the `ARCHI_ENV_FILE` env var. Resolved in `lib.sh` — never commit it.
