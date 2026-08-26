# archi deployment — management scripts

Thin, safe wrappers around the `archi` CLI for this host's local archi
deployment (FASRC vLLM backend). All scripts resolve the repo root from their
own location, so they run from anywhere. The deployment name defaults to
**`dev`** in `lib.sh` and is pinned per host by a git-excluded `host.env` (see
"Per-host configuration" below), so every script affects only this host's own
deployment.

| Script | Action | Data |
|---|---|---|
| `create.sh` | Create / bring up (`archi create --hostmode --force`). Provisions `config/` first (see below). Safe to re-run. | preserved |
| `redeploy.sh` | Rebuild + re-render config + restart (picks up config/code edits). Provisions `config/` first. | preserved |
| `nuke.sh [-y]` | **Destroy everything**: containers, volumes (DB + corpus), images, files. | **WIPED** |
| `status.sh` | Read-only: containers, volumes, chat UI, LLM reachability. | — |
| `firewall.sh` | Idempotently (re-)apply the archi host firewall rules. Not part of deploy. | — |

## Usage

```bash
deploy/scripts/create.sh      # first-time or re-run
deploy/scripts/redeploy.sh    # after editing config.yaml / code
deploy/scripts/status.sh      # check state
deploy/scripts/nuke.sh        # full teardown (asks you to type the deployment name)
deploy/scripts/nuke.sh -y     # full teardown, no prompt (automation)
```

## Per-host configuration (`host.env`)

Two hosts deploy from this repository, and they must not share one identity.
`lib.sh` reads an optional `host.env` (this directory; git-excluded via the
`deploy/scripts/**/*.env` rule) before it applies its defaults. Copy
`host.env.example` to start.

- **Data, not code.** `host.env` is parsed, never sourced — nothing in it can
  execute. Only `KEY=VALUE` lines for `DEPLOYMENT`, `CONFIG`, and `GPU_IDS`
  are accepted — each key at most once, the identity keys need a non-empty
  value, and `DEPLOYMENT` must be one `[A-Za-z0-9_-]+` token from any source
  (the name becomes `~/.archi/archi-<name>`, which the deploy's `--force`
  path removes — a path separator in it is refused) (plus comments and blank
  lines; edge whitespace and CRLF are stripped); any other line **aborts
  every script that reads `lib.sh`** —
  `status.sh` and `nuke.sh` included, because an unparsable `host.env` makes
  the deployment identity ambiguous, and failing closed beats tearing down
  the wrong deployment. The error names the offending line. The config pin
  (`CONFIG_REF`/`CONFIG_SHA`/`CONFIG_REPO`) is deliberately not accepted:
  it stays overridable per-invocation only.
- **Precedence:** command-line environment > `host.env` > tracked default —
  a `host.env` value applies only when the variable is not already set, so
  the command line always wins. For `DEPLOYMENT`/`CONFIG` an **empty**
  environment value counts as unset (an empty identity is never valid, so it
  cannot bypass the host pin); for `GPU_IDS` empty stays the explicit
  disable. Pinned by `test_host_env.sh`.
- **No `host.env`** resolves exactly the tracked defaults (`DEPLOYMENT=dev`,
  `CONFIG=deploy/fasrc-dev/config.yaml`). The GPU host needs no file.
- **Reserved names (issue #363):** `dev` is the GPU host (`holygpu7c0717`, the
  production deployment); `claw` is the no-GPU / no-local-vLLM workstation.
- **Self-test:** `bash deploy/scripts/test_host_env.sh` — 17 cases
  against a fake `archi` and a fixture tree; renders nothing, deploys nothing.

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
- **Self-test:** `bash deploy/scripts/test_ensure_config.sh` — 10
  cases against a local fixture repo; no network, never touches the real
  checkout.
- Raw `archi create` **bypasses all of this** — see the warning in
  `config.example.yaml`.

## Host firewall (`firewall.sh`)

The archi service ports are reachable only from Harvard VPN networks. Those
rules are **hand-added and unmanaged**: the host's base ruleset is
config-managed (puppet — its rules carry numeric `0000`/`0010` comment
prefixes), but the archi ports are not in that managed set. So they can vanish
on a host rebuild, and a puppet run may purge them outright if the firewall
class runs with `purge => true`.

`firewall.sh` is the reproducible record of what to re-add:

```bash
deploy/scripts/firewall.sh --dry-run   # show what's missing
deploy/scripts/firewall.sh             # apply (needs sudo)
deploy/scripts/firewall.sh --list      # show archi rules in place
```

- **Idempotent** — each rule is checked with `iptables -C` before insertion, so
  re-running is a no-op. `--dry-run` never mutates.
- **Placement** is derived from the chain, not hardcoded: rules are inserted
  ahead of the terminal `REJECT all -- 0.0.0.0/0 0.0.0.0/0`. On a chain with no
  such rule they are appended instead.
- **Access:** `7861` (chat UI) is open to the Harvard gencom VPN
  (`10.1.4.0/22`, `10.1.16.0/22`) and the FASRC VPN (`10.255.8.0/22`); every
  other port is admin-VPN only (`10.255.13.96/27`). The table in the script is
  the source of truth.
- **Deliberately not wired into `create`/`redeploy`** — opening host-wide ports
  is a privileged action that should stay an explicit human decision, never a
  deploy side effect.
- **Rules are runtime-only until persisted** (e.g. `service iptables save`).
  The durable fix is to get these ports into the puppet-managed set via FASRC
  ops; this script is the stopgap and the documentation of intent.
- **Self-test:** `bash deploy/scripts/test_firewall.sh` — 8 cases
  against a fake `iptables`; no root, no network, never touches the real
  firewall.

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
- Config: repo-relative, named by `CONFIG` (git-excluded — host-specific). The tracked
  default is `deploy/fasrc-dev/config.yaml`, the GPU host's own file. Another host points
  `CONFIG` at its own file from `host.env`. First-time setup:
  `cp deploy/fasrc-dev/config.example.yaml <your CONFIG path>` and fill in the LLM host,
  paths, etc. Paths here are repo-relative, not relative to this directory: these scripts
  are host-neutral and no longer sit beside any one host's config.
- Secrets: an env file with `PG_PASSWORD` (required) plus `HUIT_API_KEY` /
  `ANTHROPIC_API_KEY` as needed. Defaults to `~/.secrets/archi-secrets.env`;
  override with the `ARCHI_ENV_FILE` env var. Resolved in `lib.sh` — never commit it.
