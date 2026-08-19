# Tasks: port-live-eval-trial

All implementation happens on a fresh branch from `origin/dev`
(`feat/port-live-eval-trial`) in the full-deps environment. Gate
(`bash scripts/gate.sh`) before every commit; commit messages record the pin
`bebfbe56`. Per-file hunks come from `git diff d1c29380 bebfbe56 -- <path>`,
hunk-classified per design.md — never from the PR #608 view, and never as a
wholesale copy of a file that exists on the fork.

**Merge order (review round 1):** the PR does not merge until the CLI smoke (7.1)
and the console trial (7.2) pass from the PR branch, and the human records the
adopt decision on the tracking issue (0.1).

## 0. Tracker disposition

- [ ] 0.0 `model: fable` — **Blocker for everything below**: the release-plan
  amendment (evidence-trial state + pinned-SHA trial intake path) is merged to
  `dev` (its own small docs PR; see design.md "Policy basis").
- [ ] 0.1 `model: sonnet` — File the tracking issue (`archi-followup-issue` flow):
  operator-initiated trial of upstream feat/live-eval (pin `bebfbe56`), linked to
  this OpenSpec change, labeled **`evidence-trial`** per the amended plan; states
  that the adopt/reject decision (7.3) and any milestone case are recorded there
  by a human before the implementation PR merges.

## 1. Eval core and CLI

- [ ] 1.1 `model: opus` — Fetch and publish the pin: `git fetch
  https://github.com/archi-physics/archi feat/live-eval`; verify `bebfbe56...` is
  reachable; `git push origin
  bebfbe56640b4e6ee9fbd2ca5f7f766af27343ab:refs/tags/upstream-live-eval-pin` so
  every clone can recover the snapshot (`git fetch origin tag
  upstream-live-eval-pin`) even if upstream force-pushes the branch away. Only
  then generate the **disposition table**:
  every file in `git diff --name-status d1c29380 bebfbe56` (220 files) gets exactly
  one of `port-verbatim` / `port-hunks` / `skip-unrelated-upstream` /
  `skip-dead-on-fork` plus a one-line reason; commit the table with the PR
  (`openspec/changes/port-live-eval-trial/disposition.md`). Then copy verbatim from
  the pin (eval-capability files absent on the fork only): `src/evaluation/**`
  (23 files), `src/cli/qa_eval.py`, `tests/unit/evaluation/**` (including
  `fake_mcp_server.py`). Add `__init__.py` to each imported test directory. Keep
  upstream `# isort: skip_file` headers.
- [ ] 1.2 `model: opus` — Deps: add `mcp==1.27.2` and `ijson==3.5.1` to
  `pyproject.toml` `[project] dependencies` and
  `requirements/requirements-base.txt`; regenerate both generated dockerfile
  `requirements.txt` files (`test_requirements_generated_in_sync.py` enforces it).
  Add the pytest `markers` section from upstream. **Resolution evidence**: install
  the full dependency set into a fresh env; `pip check` must be clean; smoke-import
  `python -c "from mcp.client.streamable_http import streamable_http_client; import
  ijson"`; run the fork's existing agent/MCP unit tests.
- [ ] 1.3 `model: sonnet` — CLI wiring: `from src.cli.qa_eval import eval_cli` and
  `cli.add_command(eval_cli)` in `main()` (`src/cli/cli_main.py`, after the
  `sources` registration ~line 985). Do NOT port the helm `install` hunk (dead on
  the fork). Verify `archi eval qa --help`, the three subcommand helps, and
  `archi evaluate --help` / `archi grade --help` all exit 0.
- [ ] 1.4 `model: opus` — `base_react.py` hunks, RED first: (a) `invoke(self,
  callbacks=None, **kwargs)` pass-through into the invoke config; (b)
  `loaded_mcp_tools` property over the existing `_mcp_tools` field
  (base_react.py:132). The RED test is ONE test on the real seam: patch the agent
  module's `get_model` with a recording fake model, invoke the real
  `BaseReActAgent` with a supplied callback object, and assert that exact callback
  object reaches the compiled agent's invoke configuration (port upstream's
  callback case into this shape). Skip the cached-tokens
  and `_mcp_skills_text` hunks. This file is a known black-churn trap — run the
  black-seam check before editing. If imported runtime tests fail on the
  mcp-selected path, apply the recorded fix: `refresh_agent(force=True)` in
  `_runtime_for_attempt`.
- [ ] 1.5 `model: opus` — Fork-authored boundary tests (gate-enforced, no live
  model), TDD: (a) **isolation, runtime seam** — run the ported runtime on a live
  row whose oracle resolves to a sentinel, with a stub pipeline class recording
  every constructor and invoke argument; walk the recordings and assert no oracle
  registry/config, recipe field, resolved truth, provenance, gold-atom text, or
  sentinel-derived value; (b) **isolation, agent seam** — run the real
  `BaseReActAgent` message-building path with the provider replaced by a recording
  fake model (patch `get_model`); structurally walk the exact serialized request
  the model received for the same oracle content (evaluator-model traffic asserted
  separately — it legitimately carries truth and atoms); (c) **trace
  persistence** — a stub pipeline programmatically emits one call to a known tool
  with a known payload; assert the workspace tool-trace records carry that exact
  name and payload end to end. The real-seam callback gate is task 1.4's test:
  it MUST assert the supplied callback object reaches the compiled agent's invoke
  configuration.
- [ ] 1.6 `model: opus` — Run the imported eval suite (`python -m pytest
  tests/unit/evaluation/ -x -q`) and then the full gate; commit 1: `port qa eval
  core and cli from upstream feat/live-eval (bebfbe56)`.

## 2. Console and deploy templates

- [ ] 2.1 `model: opus` — Seam module
  `src/interfaces/chat_app/evaluation_console.py` (pattern:
  `config_fingerprint.py`), TDD with a new fork test file:
  `build_evaluation_service(chat_app_config)` (root default
  `/root/archi/evaluations`, `agent_config_path` default
  `/root/archi/configs/config.yaml`, optional `mcp_config_path`, `enabled` strict
  `is True`); `build_authorize_request(auth_enabled)` (auth off → always allow;
  auth on → session + `has_permission` → 401/403 JSON);
  `can_view_evaluations(evaluations_enabled, auth_enabled)`.
- [ ] 2.2 `model: opus` — Console port: copy verbatim `evaluation_routes.py`,
  `static/evaluations.css`, `static/evaluations.js`, `templates/evaluations.html`,
  `tests/unit/test_evaluation_routes.py`. Adapt upstream
  `tests/unit/test_evaluation_config.py` to import the seam module (never
  `app.py`). Apply **eval-relevant hunks only** to the fork-unmodified files
  `src/utils/rbac/permission_enum.py`, `templates/index.html`, `static/chat.css`
  (never wholesale — the pin's versions carry unrelated upstream content). `app.py`
  thin call sites only: build the service near the openai_compat block (~line
  2853); `register_evaluations(...)` after `register_service_alerts` (~line 3280);
  `can_view_evaluations` into the index render.
- [ ] 2.3 `model: opus` — Deploy templates + staging: `templates_manager.py`
  (constants, `evaluation_mcp_configured` context field,
  `_stage_evaluation_config` minus the helm branch, stage-list entry, runtime-path
  substitution in `_render_config_files`); `base-config.yaml` `evaluations` block;
  `base-compose.yaml` mounts (`./data/evaluations`, conditional
  `./evaluation_config:ro`). Copy verbatim `test_evaluation_config_staging.py` and
  `test_base_compose_mcp_mounts.py`; verify whether
  `test_templates_manager_ab_agents.py` covers only non-ported upstream work before
  dropping it (record the verdict in the disposition table).
- [ ] 2.4 `model: opus` — Full gate; commit 2: `port qa eval console and deploy
  templates (bebfbe56)`.

## 3. Docs

- [ ] 3.1 `model: sonnet` — Add `docs/docs/evaluation.md` verbatim. Content-merge
  the eval sections into `docs/docs/cli_reference.md` (+274 upstream lines — the
  one real merge), `configuration.md`, and `benchmarking.md`. Apply eval-relevant
  hunks only to `index.md` and `user_guide.md` (the pin's versions carry unrelated
  Jira/playbook content). Add `Evaluation: evaluation.md` to the mkdocs nav after
  Benchmarking. Gate; commit 3: `docs: qa evaluation guide and reference merges
  (bebfbe56)`.

## 4. Trial fixtures

- [ ] 4.1 `model: sonnet` — `examples/qa_eval/`: `dataset.json` (qa-dataset-v2; 3
  static rows + 2 live rows whose recipes call `current_capacity` on alias
  `capacity`); `qa_evaluation_mcp.cli.yaml` and `qa_evaluation_mcp.console.yaml`
  (qa-evaluation-mcp-v1; stdio; `authentication: {mode: inherited_environment}`;
  CLI variant args = repo path of `fake_mcp_server.py`, console variant args =
  `/root/archi/evaluations/fake_mcp_server.py`); `evaluator-profile.yaml`
  (`provider: anthropic`, the standby model id from the dev config, timeout 120)
  plus `evaluator-profile.vllm.yaml` (`provider: openai` + `OPENAI_BASE_URL` env
  note); `agent-config.yaml` (rendered dev config copy with
  `services.chat_app.{agent_class, default_provider, default_model}`);
  `agent-spec.md` (enables the retrieval tool and mandates its use before any
  knowledge-base answer; the tested agent gets NO tool that reaches the oracle).
  One static row is the **forced-tool row**: its question demands a knowledge-base
  lookup. Add `sentinel_value.txt` (a distinctive value, for example `7314159`)
  for the isolation probe. Validate `dataset.json` and both registries with the
  ported validators in a unit test. Gate; commit 4: `add qa eval trial fixtures`.

## 5. Optional playwright port

- [ ] 5.1 `model: haiku` — Copy `tests/ui/evaluations.spec.ts`,
  `tests/ui/evaluation_test_server.py`, `tests/ui/evaluation_test_worker.py`
  (outside the gate; run once by hand, record the result). Commit 5: `port qa eval
  playwright specs (bebfbe56)`.

## 6. Pre-PR review and PR (no merge yet)

- [ ] 6.1 `model: opus` — Adversarial review LOOP on the branch
  (`/codex:adversarial-review --wait`): verify each finding, fix (TDD) or push back
  with reasons, commit, re-run; stop at zero findings or nits-only (file nits as
  issues; bound 3–4 rounds). Carry the round summary into the PR body.
- [ ] 6.2 `model: sonnet` — Push, open the PR **as a draft** (`gh pr create
  --draft --repo fasrc/archi --base dev`) with the trial-gated merge condition
  stated in the body — draft status keeps the hourly readiness reconciler from
  ever advertising the unadopted trial as `ready-to-merge` (amended plan). Comment
  `@codex review`, drive the post-PR loop per `archi-pr-review-response`
  (round-log comments) to a clean round. **Do not mark ready or merge** — that is
  section 8.

## 7. Trial execution from the PR branch (operator present — needs-deploy)

- [ ] 7.1 CLI smoke in the full-deps env with `ANTHROPIC_API_KEY`: staged phases,
  composite re-run, determinism probe (`QA_FAKE_MCP_VALUE_FILE` → 9). One pass
  rule for staged AND composite: exit 0, `scored` manifest, zero failed rows —
  inspect the failed-row count in both workspaces explicitly. Then two
  **secondary-evidence checks** (the gating proofs are the unit tests in 1.5):
  (a) isolation grep — re-run with `QA_FAKE_MCP_VALUE_FILE` →
  `examples/qa_eval/sentinel_value.txt`; grep the persisted agent config, agent
  spec, and each attempt's recorded agent input for the oracle alias `capacity`,
  the tool name `current_capacity`, recipe fields, the sentinel, the provenance
  revision, and gold-atom text — all absent; (b) forced-tool row — record whether
  the live model produced the mandated retrieval-tool trace; investigate and
  record a miss, do not fail the trial on it alone. Record pass/fail per the spec
  scenarios on the tracking issue, **naming the tested PR-head SHA and the tested
  base (`origin/dev`) SHA** (amended plan: stale evidence never merges — if either
  moves after the trial, rerun on the current pair).
- [ ] 7.2 Console trial on FASRC dev, deployed from the PR branch checkout.
  **Provenance first** (the redeploy can silently run a stale installed CLI): in
  the deploy env, `pip install -e .` from the PR checkout, then verify
  `python -c "import src.cli.managers.templates_manager as m; print(m.__file__)"`
  resolves inside the PR checkout; record that output and the checkout's head SHA
  on the tracking issue before treating the deployment as PR-branch evidence.
  Then: host config `services.chat_app.evaluations: {enabled: true,
  mcp_config_path: <PR-checkout>/examples/qa_eval/qa_evaluation_mcp.console.yaml}`
  (the fixture's actual path — the staging step validates it as a host source file
  and copies it into the generated `evaluation_config/`); copy
  `fake_mcp_server.py` into the host dir mounted at `/root/archi/evaluations`;
  `deploy/fasrc-dev/scripts/redeploy.sh`; verify via `archi-dev-deploy-verify`
  (chat 200 + `GET /evaluations` 200 + nav link); run the full console loop;
  record results. Rollback = disable the block, redeploy from `dev`.
- [ ] 7.3 Adopt/reject writeup posted on the tracking issue: functional results;
  verdict agreement on 10–20 golden-set rows converted to Dataset V2 vs the RAGAS
  stack; capability delta; cost (tokens/row, wall time, gate-time delta);
  maintenance burden (disposition table + skip lists = re-sync cost); the
  `eval`/`evaluate` naming collision; adoption preconditions (SSO-aware
  authorize_request, RBAC mapping, golden-set convergence, milestone case).

## 8. Merge decision (human gate)

- [ ] 8.1 The human records adopt/reject on the tracking issue. **Adopt** — in
  this order per the amended plan: (1) file the adoption's follow-on work into a
  milestone through the normal gate bar, and name the release the capability
  ships in — this capability satisfies the dark-ride-along rule (the console
  activates only on `evaluations.enabled: true`, off in every deployed config by
  default), so an earlier release may carry it dark; (2) mark the draft PR ready
  and merge it, only when the recorded tested head AND base SHAs equal the
  current PR head and `origin/dev` tip (either moved → rerun the trial first),
  the review round is clean, and CI is green; (3) close the issue — the
  `evidence-trial` label stays until closure. **Reject** — close the PR first,
  then revert the dev-stack deploy to `dev`, then close the issue with the
  writeup as the record (a green open PR never outlives its rejection).

## 9. Archive (path depends on the 8.1 decision)

- [ ] 9.1 `model: haiku` — **Adopt**: verify the implementation PR actually merged,
  then `/opsx:archive port-live-eval-trial` (spec deltas sync into
  `openspec/specs/`), archive PR (no codex review), merge. **Reject**: abandon
  path — archive the change WITHOUT syncing the spec deltas into canonical specs
  (the capability was never implemented); record the rejection and the writeup
  link in the archived proposal, and never publish `qa-evaluation-trial`
  requirements to `openspec/specs/`.
