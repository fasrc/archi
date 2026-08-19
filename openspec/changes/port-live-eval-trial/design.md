# Design: targeted port of upstream feat/live-eval

## Pinned source

- Upstream repo: `archi-physics/archi`, branch `feat/live-eval`, PR #608 (base =
  `feat/archi-eval-command`, PR #596; both open).
- **Pin: `bebfbe56640b4e6ee9fbd2ca5f7f766af27343ab`** (2026-08-18). Local tag
  `upstream-live-eval-pin` (not pushed). Every port commit message records the short
  SHA. PR #608 still moves upstream; a re-pin is a deliberate act, never implicit.
- Fetch: `git fetch https://github.com/archi-physics/archi feat/live-eval`.

## Where the port diff comes from

Merge base with upstream `main` is `d1c29380` (2026-03-24). The correct per-file
port diff is `git diff d1c29380 bebfbe56 -- <path>`, **not** the PR #608 view: the PR
diff hides eval code that landed on upstream `main` after March. Proven example: the
`services.chat_app.evaluations` block in `base-config.yaml` is context in the PR
view, and it is absent on our `dev`.

Scope vs upstream `main`: 35 commits, 86 files. Of these, ~70 do not exist on our
`dev` (verbatim copies), 14 need hand-ported hunks, and the rest is skipped.

## File dispositions

### Verbatim adds (do not exist on `dev`)

- `src/evaluation/**` (23 files: dataset gateway, oracle, oracle_config,
  live_checks, preparation, workflow, runtime, scoring, schema, workspace, catalog,
  jobs, console, history, artifacts, phases, profile, tool_traces, validation,
  worker, constants).
- `src/cli/qa_eval.py`, `src/interfaces/chat_app/evaluation_routes.py`,
  `static/evaluations.css`, `static/evaluations.js`, `templates/evaluations.html`.
- `tests/unit/evaluation/**` including `fake_mcp_server.py`;
  `tests/unit/test_evaluation_routes.py`, `test_evaluation_config_staging.py`,
  `test_base_compose_mcp_mounts.py`. Imported test dirs get `__init__.py`
  (fork test tree is a package).
- `docs/docs/evaluation.md`.

### Take-theirs (files the fork never modified since the merge base)

`src/utils/rbac/permission_enum.py` (Evaluations permissions),
`src/interfaces/chat_app/templates/index.html` (nav link),
`static/chat.css`, `docs/docs/index.md`, `docs/docs/user_guide.md`.

### Hand-ported files (the 14)

| File | Disposition |
| --- | --- |
| `app.py` | Thin call sites only; all logic in the new seam module (below). |
| `base_react.py` | Two hunks: `invoke(self, callbacks=None, **kwargs)` pass-through + `loaded_mcp_tools` property over `_mcp_tools`. Skip the cached-tokens and `_mcp_skills_text` hunks. Known black-churn file — run the seam check before the edit. |
| `cli_main.py` | `from src.cli.qa_eval import eval_cli` + `cli.add_command(eval_cli)` in `main()`. Skip the helm `install` hunk (the fork has no `install` command and no helm tree). |
| `templates_manager.py` | Port `EVALUATION_CONFIG_DIR` / `EVALUATION_MCP_CONFIG_FILENAME` / `EVALUATION_MCP_RUNTIME_PATH`, the `evaluation_mcp_configured` context field, `_stage_evaluation_config` with the helm branch removed, the stage-list entry, and the runtime-path substitution in `_render_config_files` (rewrites `mcp_config_path` to `/root/archi/evaluation_config/qa_evaluation_mcp.yaml` when staged). |
| `base-config.yaml` | Add the `evaluations` block under `chat_app` (enabled / root / agent_config_path / mcp_config_path). |
| `base-compose.yaml` | Add the `./data/evaluations` mount and the conditional read-only `./evaluation_config` mount. |
| `pyproject.toml` | Add `mcp==1.27.2`, `ijson==3.5.1`, and the pytest `markers` section. Keep every fork-only dep. |
| `tools/mcp.py`, `utils/mcp_utils.py` | **Skip.** Upstream's hunks serve upstream-main skills/http-auth work; the eval runtime does not import them. |
| `docs/cli_reference.md` | Content merge (+274 upstream lines — the one real docs merge). |
| `docs/configuration.md`, `docs/benchmarking.md` | Merge the eval sections. |
| `docs/mkdocs.yml` | Nav entry `Evaluation: evaluation.md` after Benchmarking. |
| `README.md` | Optional one-line mention; take ours otherwise. |

### Skipped entirely

`src/cli/templates/helm/**`, `evaluation-configmap.yaml`, `exec_plans/**`, and these
imported tests (they cover non-ported upstream work): `tests/unit/test_mcp.py`,
`tests/unit/archi/pipelines/agents/test_mcp_utils.py`,
`test_chat_app_authorization.py` (imports `app.py` / upstream `authorize_request`),
`test_templates_manager_ab_agents.py` (verify before dropping), and the non-callback
parts of upstream `test_base_react.py`. Playwright specs (`tests/ui/evaluation*`)
are outside the gate; optional copy, run once by hand.

## The console seam

Upstream `register_evaluations(app, *, authorize_request, service)` needs an
`authorize_request` callable that upstream's `App` provides (added after our merge
base). Our fork has `require_auth` (app.py:3555) and `require_perm` (app.py:3602)
instead, and our `app.py` is not unit-imported, so new logic there fails the
diff-coverage gate.

New fork-authored module `src/interfaces/chat_app/evaluation_console.py` (pattern:
`config_fingerprint.py`), TDD:

- `build_evaluation_service(chat_app_config)` — parses
  `services.chat_app.evaluations` (root default `/root/archi/evaluations`,
  `agent_config_path` default `/root/archi/configs/config.yaml`, optional
  `mcp_config_path`; `enabled` must be strictly `True`). Returns the configured
  service or `None`.
- `build_authorize_request(auth_enabled)` — when auth is off, returns a callable
  that always allows. When auth is on, the callable checks the session login and
  `has_permission(permission)`, and returns a 401/403 JSON response on failure.
  This is narrower than upstream's bearer/SSO-aware version — a recorded adoption
  cost, acceptable because the dev deployment runs auth-off.
- `can_view_evaluations(evaluations_enabled, auth_enabled)` — nav-link visibility.

`app.py` thin call sites: build the service near the openai_compat block
(~app.py:2853); call `register_evaluations(...)` after `register_service_alerts`
(~app.py:3280); pass `can_view_evaluations` into the index render. Upstream's
`test_evaluation_config.py` imports `app.py` directly — adapt it to import the seam
module instead.

## Runtime compatibility (verified against the pin)

- `pipeline_class(config=..., agent_spec=..., default_provider=...,
  default_model=...)` matches `BaseReActAgent.__init__` (base_react.py:101).
- `get_model(provider, model, {}, **kwargs)` matches
  `src/archi/providers/__init__.py:241`.
- `pipeline.invoke(history=..., vectorstore=..., callbacks=[...])` — fork `invoke`
  drops `callbacks` silently; hence the pass-through hunk.
- `pipeline.loaded_mcp_tools` — absent on the fork; the property reads the existing
  `_mcp_tools` field (base_react.py:132). The fork builds `_mcp_tools` lazily in
  `refresh_agent`; if imported runtime tests fail on the mcp-selected path, the
  recorded fix is one `refresh_agent(force=True)` call in `_runtime_for_attempt`.
- `agent_spec.load_agent_spec_from_text` and
  `src.archi.utils.vectorstore_connector.VectorstoreConnector` exist on the fork.

## Dependencies

- `mcp==1.27.2`: the eval code imports `ClientSession`, `stdio_client`,
  `streamable_http_client`, `FastMCP` directly. Today `mcp` arrives only
  transitively (`langchain-mcp-adapters 0.1.11` → `mcp>=1.9.2`), so the explicit pin
  is required. `mcp` needs `httpx>=0.27.1,<1.0`; the fork pins `httpx==0.27.2`.
- `ijson==3.5.1`: streaming JSON parsing in the dataset gateway.
- Both pins go into `pyproject.toml` and `requirements/requirements-base.txt`; the
  two generated dockerfile `requirements.txt` files are regenerated
  (`test_requirements_generated_in_sync.py` enforces the concatenation).
- LangChain pins are identical on both sides — no framework skew.

## Gate strategy

`scripts/gate.sh` measures diff coverage cumulatively vs `origin/dev`, `.py` lines
only. Consequences:

- The imported test suite lands in the same commit as the code it covers.
- Black 24.10.0 will reflow imported code — harmless; the imported tests still cover
  it. Keep upstream's `# isort: skip_file` headers.
- `app.py` gains only thin call-site lines; their coverage debt is diluted by the
  branch-wide diff and the seam module is fully tested.

## Trial fixtures (`examples/qa_eval/`)

- `dataset.json` — `qa-dataset-v2` envelope; 3 static items and 2 live items whose
  `oracle` recipes call the fake server's `current_capacity` tool.
- `qa_evaluation_mcp.cli.yaml` — `qa-evaluation-mcp-v1`; alias `capacity`; stdio;
  `command: python3`; args = repo path of
  `tests/unit/evaluation/qa/fake_mcp_server.py`;
  `authentication: {mode: inherited_environment}`.
- `qa_evaluation_mcp.console.yaml` — same, args =
  `/root/archi/evaluations/fake_mcp_server.py` (container path).
- `evaluator-profile.yaml` — `provider: anthropic` + the standby model id, timeout
  120, for both atom extraction and judging. The profile schema has no `base_url`
  field, so a vLLM evaluator only works as `provider: openai` + `OPENAI_BASE_URL`
  env; that variant ships as `evaluator-profile.vllm.yaml` (fallback).
- `agent-config.yaml` — a copy of the rendered dev config (must carry
  `services.chat_app.{agent_class, default_provider, default_model}`).
- `agent-spec.md` — minimal spec. Live rows exercise the evaluator's oracle, not
  agent tools.

## Trial acceptance

CLI (full-deps env, `ANTHROPIC_API_KEY` set): prepare → run (`--attempts 2
--run-workers 2`) → score, then the composite single command in a fresh dir, then
the determinism probe (`QA_FAKE_MCP_VALUE_FILE` pointing at a file with `9` changes
the resolved live answer). Pass = exit 0 each phase, zero failed rows, tool-trace
records present in `answers.jsonl`.

Console (FASRC dev): set `services.chat_app.evaluations: {enabled: true,
mcp_config_path: qa_evaluation_mcp.console.yaml}` in the host config; copy
`fake_mcp_server.py` into the host dir that mounts to `/root/archi/evaluations`;
`redeploy.sh`; verify chat 200 + `GET /evaluations` 200 + nav link; then import
dataset → import profile → generate atoms → review/approve → run → score → history.
Rollback: disable the block, redeploy from `dev`.

## Risks

- **Seam narrows auth** (no bearer/SSO branch): fine on auth-off dev; recorded
  adoption cost.
- **`loaded_mcp_tools` lazy-build timing**: imported runtime tests catch it; the
  recorded fix is `refresh_agent(force=True)` in `_runtime_for_attempt`.
- **Upstream keeps moving**: the pin is deliberate; the writeup lists every skipped
  hunk and test as the future re-sync burden.
- **`archi eval` vs `archi evaluate`**: coexist on the trial branch; rename or merge
  is an adoption decision.
