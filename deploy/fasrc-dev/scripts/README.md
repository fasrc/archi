# archi 'dev' deployment — management scripts

Thin, safe wrappers around the `archi` CLI for the local **`dev`** deployment
(FASRC vLLM backend). All scripts resolve the repo root from their own location,
so they run from anywhere. The deployment name is hard-wired to `dev` in
`lib.sh`, so these scripts can never affect your other deployments.

| Script | Action | Data |
|---|---|---|
| `create.sh` | Create / bring up (`archi create --hostmode --force`). Safe to re-run. | preserved |
| `redeploy.sh` | Rebuild + re-render config + restart (picks up config/code edits). | preserved |
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

## Notes

- **VPN required for chat.** The LLM endpoint is VPN-only; `create`/`redeploy`/
  `status` warn if it's unreachable but do not block.
- **`create` vs `redeploy`** run the same `archi create --force` underneath
  (archi has no separate redeploy verb); both preserve data volumes. Only
  `nuke` removes volumes.
- **`nuke` is irreversible** — it wipes the Postgres DB and the ingested corpus.
  The next `create` re-ingests and rebuilds images from scratch (slow).
- Config: `../config.yaml` (git-excluded — host-specific). First-time setup:
  `cp ../config.example.yaml ../config.yaml` and fill in the LLM host, paths, etc.
- Secrets: an env file with `PG_PASSWORD` (required) plus `HUIT_API_KEY` /
  `ANTHROPIC_API_KEY` as needed. Defaults to `~/.secrets/archi-secrets.env`;
  override with the `ARCHI_ENV_FILE` env var. Resolved in `lib.sh` — never commit it.
