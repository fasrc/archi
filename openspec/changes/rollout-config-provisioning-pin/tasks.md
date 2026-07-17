# Tasks: rollout-config-provisioning-pin

## 1. Pre-flight capture (baseline to verify against)

- [x] 1.1 Record the pre-rollout corpus baseline from `postgres-dev`: web document
  count, git documents embedded, git documents failed (expect web ≈ 549, git 724
  embedded / 13 failed). Save the numbers into the change dir for the parity check.
  → `baseline.md`: web 546+3, git 724 embedded / 13 failed (12 ipynb + 1 md), 17767 parent nodes.
- [x] 1.2 Snapshot the live source manifest: `git -C config diff 990c54c7 --
  lists/sources.list` and confirm the 2 `git-` sources (User_Codes, ood-documentation)
  and the 219 KB pages are the working-tree version.
  → confirmed: 2 git sources + 219 KB pages + 370 lines in working tree; pin has 0 git sources.
- [x] 1.3 Confirm the host `config.yaml` `input_lists` already points at
  `config/lists/sources.list` and this host stages `deploy/fasrc-dev/agents/` (not
  `config/agents/`) — no behavior change from the agent-spec commits.
  → confirmed: input_lists→config/lists/sources.list; agents_dir→deploy/fasrc-dev/agents.

## 2. Reconcile fasrc/archi-config content (D1, D2)

- [x] 2.1 In `config/`, branch from the pin `990c54c7` for the reconciliation commit.
  → refined: branched from `origin/main` (2 unrelated nightly commits ahead of the pin)
  via a shared-object worktree `reconcile-deploy-pin-2026-07b`, so the push fast-forwards.
- [x] 2.2 Stage the live `lists/sources.list` (370-line, git sources included).
- [x] 2.3 Stage `agents/fasrc-inline-v1.md` and `agents/fasrc-archi-v12.md`; stage
  `agents/archive/**`; `git mv agents/fasrc-cannon.md agents/archive/fasrc-cannon.md`.
  → rename shows as R100; archive now 14 files.
- [x] 2.4 Delete `agents/fasrc-cannon.yaml` (0 bytes),
  `agents/fasrc-inline-v1-corrected-tools.md` (dupe of canonical), and `scripts/.env`.
  → satisfied by construction: none exist at the branch base, none copied in. `.env` is
  also blocked by the repo's `*.env` gitignore.
- [x] 2.5 Stage `benchmarking/**` (shared banks).
  → 12 files; `benchmarking/secrets.env` (real PG_PASSWORD/ANTHROPIC_API_KEY) verified
  EXCLUDED by `*.env` gitignore; only `secrets.env.example` staged.
- [x] 2.6 Keep the pin's `environments/dev.yaml`; discard the stale working-tree edit.
- [x] 2.7 Leave host-local (do NOT commit): `scripts/` host additions, `compose.yaml`,
  `lists/sources.list-old`. → none copied in.
- [x] 2.8 Content-parity gate (new spec requirement): assert the staged
  `lists/sources.list` contains every web and `git-` source from task 1.2 — no git
  source dropped. Abort reconciliation if any is missing.
  → PASS: 2 git sources + 219 KB pages + 370 lines, identical to baseline.
- [x] 2.9 Commit to fasrc/archi-config `main` and push.
  → pushed: `main` fast-forwarded `67e731b..98f9bd2` (verified remote main = 98f9bd22).

## 3. Mint the new pin and bump lib.sh (D3)

- [x] 3.1 Create a NEW annotated tag `deploy-pin-2026-07b` at the reconciled commit in
  fasrc/archi-config (never move `deploy-pin-2026-07`); push the tag.
  → tag `64ca5ffa` → commit `98f9bd22` on remote (local==remote object, verified). Old
  `deploy-pin-2026-07` untouched (still `ab4593e`→`990c54c7`).
- [ ] 3.2 Branch `fasrc/archi` from `origin/dev`; update `CONFIG_REF` and `CONFIG_SHA`
  in `deploy/fasrc-dev/scripts/lib.sh` to the new tag + its commit id.
- [ ] 3.3 Run `deploy/fasrc-dev/scripts/test_ensure_config.sh` against a local fixture —
  the pin-values substitute for the (absent) Python diff-cover signal.
- [ ] 3.4 Run `bash scripts/gate.sh` (format + unit tests); commit lowercase, no
  trailers; open PR to `fasrc/archi:dev`, print the full PR URL. Flag the
  `fasrc-cannon.md` path move for any host that bind-mounts `config/agents/` live.

## 4. Roll out on the dev host (D4)

- [ ] 4.1 Sync local `dev` to `origin/dev` (brings `ensure_config` + merged #108) and
  update the working branch.
- [ ] 4.2 Back up the stranded checkout: `mv config config.pre-rewrite-bak` (retains the
  2 stranded commits + all host-local files).
- [ ] 4.3 Run `deploy/fasrc-dev/scripts/redeploy.sh` (with the new pin, via the branch or
  a one-off `CONFIG_REF=…/CONFIG_SHA=…`); `ensure_config` clones fresh at the new pin.
- [ ] 4.4 Restore kept host-local untracked files (`scripts/` additions, `compose.yaml`)
  from `config.pre-rewrite-bak` into the fresh `config/`.

## 5. Verify corpus parity (D5, spec requirement)

- [ ] 5.1 Assert the deploy provenance line reports `match=yes`.
- [ ] 5.2 Wait for re-ingest (~53 min); poll for the "Vectorstore update has been
  completed" marker.
- [ ] 5.3 Compare post-ingest corpus counts to the task 1.1 baseline: web ≈ 549, git 724
  embedded / 13 failed. A drop in embedded git documents = rollout failure → stop and
  investigate (restore from `config.pre-rewrite-bak` if needed).
- [ ] 5.4 Live chat smoke test returns HTTP 200 with the active agent `FASRC-inline-v1`.

## 6. Close out

- [ ] 6.1 Remove `config.pre-rewrite-bak` only after 5.3/5.4 pass.
- [ ] 6.2 Note in the change that the RAGAS re-baseline is now unblocked and MUST be
  taken on this post-rollout corpus (D6 ordering satisfied).
