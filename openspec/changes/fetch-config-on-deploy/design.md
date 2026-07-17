## Context

`deploy/fasrc-dev/scripts/lib.sh` defines `REPO_ROOT` (:9), `CONFIG=deploy/fasrc-dev/config.yaml`
(:13), and the shared deploy path that runs `archi create --config "$CONFIG" --force` (:76–78).
Nothing provisions `$REPO_ROOT/config` (the nested fasrc/archi-config clone). Issue #99 specifies
the fix but its anchors are stale: archi-config's history was rewritten in the secrets purge
(`944556f` gone; now ~67 tracked files at `7e2cd5c`+, including systemd units and specs), and its
`environments/dev.yaml` is an April template that drifted far behind the live dev config. The
operator confirmed scope: #99's `ensure_config` + the dead sources-path fix + a one-way sync of
the live config into archi-config. Deployment behavior does not change.

## Goals / Non-Goals

**Goals:**
- Fresh host: `create.sh` works with no pre-existing `config/` (clone at pin).
- Existing host: every deploy converges `config/` to `CONFIG_REF` when clean; NEVER
  destroys live edits (bind-mounted agent prompts, untracked local question banks).
- The next re-ingest reads a sources.list path that exists.
- archi-config's `environments/dev.yaml` matches the live dev config at sync time.

**Non-Goals:**
- Pointing the deployment at `environments/dev.yaml` (that's issue #88's layered-config
  design — deliberately not folded in).
- Any change to what the running containers read; no redeploy required by this change.
- Secret handling (config repo already ignores `secrets/`; stays host-local).

## Decisions

1. **Pin = annotated tag, env-overridable** (`CONFIG_REF:-deploy-pin-2026-07`), created at
   the sync commit (see Migration Plan order). Config schema is validated by code (see PR
   #98), so deploys must get a known ref, not a floating `main`. **Bump = a NEW tag name**
   (`deploy-pin-YYYY-MM[-n]`) + updating the default in `lib.sh` — never move an existing
   tag: `git fetch --tags` refuses to clobber a changed local tag without `--force`
   (verified against a fixture: the fetch fails loudly, exit 1, so an already-provisioned
   host would abort while a fresh host resolved the moved tag — divergence, just not
   silent). **The pin is content-addressed, not name-trusted:** `lib.sh` records the
   expected commit id (`CONFIG_SHA`) next to the tag name, and `ensure_config` dies if
   `git -C config rev-parse "$CONFIG_REF^{commit}"` differs — a re-pointed remote tag
   aborts the deploy instead of being believed, and the tag→commit binding is reviewable
   in the fasrc/archi PR (a submodule/vendored copy was rejected: archi is PUBLIC,
   archi-config is PRIVATE — a gitlink breaks public cloners and vendoring leaks ops
   config). One-off override: `CONFIG_REF=... CONFIG_SHA=... redeploy`. (A GitHub tag
   ruleset protecting `deploy-pin-*` was attempted as defense-in-depth and is
   unavailable on the private repo's plan — 403, requires Pro; the `CONFIG_SHA` check
   is the load-bearing control.)
2. **Dirty-tree policy: split by dirt type** (refined after a second adversarial pass,
   operator-approved 2026-07-16). Untracked-only dirt does NOT block pin convergence —
   git's checkout collision protection makes converging safe, and the dev host's dirt
   is typically untracked-only (question banks), so a pure dirty-wins rule would have
   silently pinned this host to an old base forever after any pin bump. Tracked edits
   with HEAD *at* the pin (the live-edit workflow) win: untouched, warn, deploy on-disk.
   Tracked edits with HEAD *off* the pin abort (that deploy would silently run an old
   base under local edits) unless `CONFIG_FORCE=1` → `git stash -u` then checkout —
   stash is recoverable; `reset --hard`/`clean -fd` are banned. Rationale over blanket
   fail-on-dirty stands: it would train operators to force (issue #99 captured dirty as
   the normal state). **Every deploy records config provenance** — `git -C config
   rev-parse HEAD`, a pin-match boolean, and a name-status of dirty paths — and the
   dirty-tree warning states how far off the pin the deploy is running
   (`rev-list --count HEAD..$CONFIG_REF` + `diff --stat`), so an off-pin deploy is always
   reconstructable after the fact. (Fail-closed and allowlist variants were evaluated and
   rejected: deploys are human-run only — automation rails forbid `deploy/fasrc-dev/**` —
   and issue #99's captured dirty set was exactly the tracked deploy-critical files, so an
   allowlist collapses; the truly fatal class, deleted critical paths, already fails
   closed via decision 4.)
3. **Shell-only, in lib.sh.** No Python helper → nothing new for diff-cover; the gate
   still runs format + unit tests. Dirty-tree safety gets a self-test script
   (`deploy/fasrc-dev/scripts/test_ensure_config.sh`) exercising clone/clean/dirty/forced
   paths against a throwaway fixture repo — runnable locally and cited in the PR. To make
   the fixture possible (and offline runs non-flaky), the remote is a defaulted variable:
   `CONFIG_REPO="${CONFIG_REPO:-git@github.com:fasrc/archi-config.git}"`; the self-test
   overrides it to a local `file://` fixture.
4. **Post-provision verification**: `lists/sources.list`, `environments/dev.yaml`, and
   `agents/` must exist after `ensure_config`, else `die` (catches a bad ref/partial clone
   before `archi create` bakes a broken image).
5. **Sync direction is live → repo, once, manually reviewed.** `environments/dev.yaml`
   becomes a copy of the live `config.yaml` (comments included — they encode deploy
   footguns like the Anthropic `models:` crash and `enable_thinking`). Host-specific
   absolute paths stay as-is and are acceptable in a private single-deployment ops repo;
   parameterizing them is #88's problem.
6. **Ordering vs `harden-config-propagation`**: both edit `lib.sh`. This change lands
   first (it's shell-only and unblocks re-ingest); the propagation change rebases on it.

## Risks / Trade-offs

- [Pin goes stale as archi-config moves] → deploys keep working at the pin (that's the
  point); the every-deploy `git -C config fetch` in ensure_config makes drift visible in
  the warning; bump procedure documented in lib.sh header.
- [agents_dir ambiguity] → the live config currently points `agents_dir` at
  `deploy/fasrc-dev/agents`, while the live-edit rationale anchors on `config/agents`
  (issue #99's capture) — confirm at implementation which directory the running
  deployment actually bind-mounts and document it in the lib.sh header.
- [Stash-on-force surprises an operator] → stash is named (`ensure_config forced update
  <date>`) and the warning prints `git -C config stash pop` as the recovery command.
- [`config.yaml` is gitignored so the path fix can't be PR'd] → fix applied host-locally
  AND mirrored in tracked `config.example.yaml`; acceptance check greps the live file.
- [Tag lives in a different repo than the PR] → task list orders tag creation before the
  fasrc/archi PR merge; ensure_config fails loudly if the ref doesn't resolve.

## Migration Plan

1. Sync `environments/dev.yaml` into archi-config and commit; THEN create the tag at that
   sync commit; push both. (Order matters: tagging first would pin the stale April
   template — the exact drift this change exists to close.)
2. Land the fasrc/archi PR (lib.sh + config.example.yaml + .gitignore comment).
3. Host-local: fix `config.yaml` sources path.
4. Verify: `redeploy.sh` runs `ensure_config` (clean tree → converges to pin; no
   container behavior change expected); re-ingest smoke can find sources.list.
5. Rollback: revert the lib.sh commit; `config/` on disk is untouched by rollback.

## Open Questions

- None blocking. (Tag name finalized at implementation: `deploy-pin-YYYY-MM`.)
