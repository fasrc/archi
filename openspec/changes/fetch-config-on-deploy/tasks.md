## 1. Pin + sync in fasrc/archi-config (ordered first — the PR depends on the tag)

- [ ] 1.1 Sync the live `deploy/fasrc-dev/config.yaml` into archi-config as `environments/dev.yaml` (verbatim copy, comments included; replaces the stale April template), commit to archi-config main
- [ ] 1.2 Create annotated tag `deploy-pin-2026-07` at archi-config HEAD (post-sync), push it, and record its commit SHA (becomes the `CONFIG_SHA` default in lib.sh)

## 2. ensure_config in fasrc/archi (branch `fetch-config-on-deploy` from origin/dev)

- [ ] 2.1 Write the dirty-tree self-test FIRST (`deploy/fasrc-dev/scripts/test_ensure_config.sh`, red): fixture repo + six cases — fresh clone, clean-tree convergence, dirty-tree untouched by default (warning includes pin-drift count+diffstat), CONFIG_FORCE=1 stashes (never reset/clean), re-pointed tag aborts naming both SHAs, provenance record present in output; watch it fail before ensure_config exists
- [ ] 2.2 Implement `ensure_config` in `lib.sh` (clone-if-absent from `${CONFIG_REPO:-git@github.com:fasrc/archi-config.git}`, fetch --tags, verify $CONFIG_REF resolves to $CONFIG_SHA or die, clean→checkout, dirty→loud warning naming paths + pin drift, CONFIG_FORCE=1→named stash -u + recovery hint, log provenance rev-parse HEAD/pin-match/dirty name-status every run, verify sources.list/environments/dev.yaml/agents/ or die); self-test green via CONFIG_REPO=file://fixture
- [ ] 2.3 Wire `ensure_config` into the shared deploy path before `archi create` (covers both create.sh and redeploy.sh); document pin-bump in lib.sh header and update the `.gitignore` :40 comment
- [ ] 2.4 Update `config.example.yaml`: sources path → the nested `config/lists/sources.list`; and rewrite the header so `create.sh` is THE documented path (raw `archi create` bypasses ensure_config, and `_copy_web_input_lists` silently skips missing lists → empty corpus on a fresh host) — keep the raw command only as an advanced note with an explicit "config/ must be provisioned first" warning
- [ ] 2.5 Gate: `bash scripts/gate.sh` green (shell-only; format + unit tests)

## 3. Host-local (not in the PR)

- [ ] 3.1 Fix live `deploy/fasrc-dev/config.yaml` sources path: `/home/austin/Projects/archi-config/lists/sources.list` → `/home/austin/Projects/archi/config/lists/sources.list`

## 4. Verify (issue #99 checks A–F, refreshed anchors)

- [ ] 4.1 A: fresh-clone check in a mktemp dir at `deploy-pin-2026-07` — sources.list + environments/dev.yaml + agents/ present
- [ ] 4.2 B: dirty-tree byte-for-byte safety — via the self-test, plus a live run against the real (deliberately dirtied then restored) checkout
- [ ] 4.3 C: clean-tree pin convergence — `git -C config rev-parse HEAD == deploy-pin-2026-07^{commit}` after a real `redeploy.sh`
- [ ] 4.4 D: `git -C config check-ignore secrets/` still holds; no secret in any commit
- [ ] 4.5 E+F: both entrypoints reach ensure_config (grep) and the gate is green
- [ ] 4.6 Live: `redeploy.sh` end-to-end on this host (clean tree expected) → containers healthy, chat smoke HTTP 200 (no behavior change expected — this proves no regression); confirm which agents dir the running deployment bind-mounts (`config/agents` vs `deploy/fasrc-dev/agents`) and document it in the lib.sh header

## 5. PR + close

- [ ] 5.1 Push branch, open PR to fasrc/archi:dev with `Closes #99` + refreshed-anchor note (issue's SHAs predate the history rewrite), request review
- [ ] 5.2 Work review findings (verify-first, in-thread replies), merge when green
