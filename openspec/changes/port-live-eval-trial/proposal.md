# Port upstream feat/live-eval for a fork trial

## Why

Upstream (`archi-physics/archi`) built a new QA-evaluation stack on branch
`feat/live-eval` (PR #608, base = PR #596; both open and unmerged). It scores complete
agent answers against explicit gold atoms, and it resolves time-sensitive answers
through evaluator-only MCP "oracle" servers, with pre-run and post-run truth checks.
Our fork has none of this code. Our own evaluation stack (`archi evaluate` /
`archi grade`, RAGAS + golden set) measures retrieval metrics on static rows and
handles fact drift offline, not at run time.

We want to trial the upstream stack against our agent before any adoption decision.
A trial needs the code on a fork branch: the CLI runs the tested agent in-process, so
an upstream checkout would evaluate upstream's agent, not ours.

A git merge is not an option. The fork and upstream `main` split at `d1c29380`
(2026-03-24); upstream `main` holds 83 commits we do not carry, and the pinned
release plan allows cherry-pick only, no sync. A 35-commit cherry-pick would conflict
at almost every step against our diverged files. The chosen method is a **targeted
port** pinned to upstream commit `bebfbe56640b4e6ee9fbd2ca5f7f766af27343ab`
(head of `feat/live-eval`, 2026-08-18): eval-capability files that do not exist on
our `dev` copy verbatim; every file that exists on the fork receives hand-ported
eval hunks only (never a wholesale copy — the pin's versions of shared files carry
unrelated upstream-main content); helm-only parts are skipped (the fork has no helm
tree). A disposition table accounts for every file in the candidate diff
(`d1c29380..bebfbe56`, 220 files) so nothing enters unreviewed.

**Tracker disposition**: this is operator-initiated evidence work under the
release plan's **evidence-trial** state (added by the plan amendment that precedes
this implementation — see `docs/docs/proposals/release-plan-2026.md`, "Evidence
trials"). The tracking issue (task 0.1) carries the `evidence-trial` label. The
implementation PR merges only after the trial passes from the PR branch and the
human records the adopt decision on that issue; a rejected trial closes the PR
unmerged. Adoption itself enters a milestone through the plan's normal gate bar.

## What Changes

- **Port the eval core**: `src/evaluation/**` (23 files), `src/cli/qa_eval.py`, and
  the imported unit-test suite under `tests/unit/evaluation/**`, verbatim from the
  pin. New deps: `mcp==1.27.2` (imported directly by the eval code; today only a
  transitive dep) and `ijson==3.5.1`.
- **Wire the CLI**: register `archi eval` next to the existing `evaluate` and
  `grade` commands. The upstream helm `install` hunk is dead on the fork and is
  skipped.
- **Two small agent hunks** in `base_react.py`: a `callbacks` pass-through in
  `invoke` (without it, tool traces come back empty) and a 4-line
  `loaded_mcp_tools` property over the existing `_mcp_tools` field.
- **Port the browser console** (`/evaluations`): routes, static assets, and
  templates verbatim; a new fork-authored seam module
  `src/interfaces/chat_app/evaluation_console.py` supplies the config parsing and
  the `authorize_request` callable that upstream's `app.py` provides but ours does
  not. `app.py` gets thin call sites only (it is not unit-imported, so new logic
  there fails the coverage gate).
- **Port the deploy templates**: the `evaluations` block in `base-config.yaml`, the
  evaluation-config staging in `templates_manager.py` (helm branch removed), and the
  compose mounts.
- **Docs**: `docs/docs/evaluation.md` verbatim plus content merges into the CLI
  reference, configuration, and benchmarking pages.
- **Trial fixtures** in `examples/qa_eval/`: a Dataset V2 file (3 static + 2 live
  rows), MCP oracle registries (CLI and console variants) that target the bundled
  fake MCP server, an Anthropic evaluator profile, and a minimal agent spec.

## Capabilities

### New Capabilities

- `qa-evaluation-trial`: the ported upstream QA-evaluation stack runs on the fork —
  CLI all-phases runs, live-oracle resolution through an evaluator-only MCP
  registry, and the `/evaluations` browser console — sufficient for an adopt/reject
  decision. Trial scope: the live oracle is the bundled fake MCP server, and the
  console auth seam supports the auth-off dev deployment only.

### Modified Capabilities

None. The existing RAGAS stack (`archi evaluate`, `archi grade`, golden-set
maintenance) is untouched and keeps its behavior.

## Impact

- **Code**: ~9.8k added lines, almost all verbatim upstream code with its own
  imported tests. Fork-authored code is limited to the seam module, the two
  `base_react.py` hunks, and the CLI registration line.
- **Deps**: `mcp==1.27.2` (fits our `httpx==0.27.2` pin), `ijson==3.5.1`; both added
  to `pyproject.toml` and `requirements/requirements-base.txt`, with the two
  generated dockerfile requirements files regenerated (a unit test enforces sync).
- **Tests**: the imported suite (~320 tests) carries patch coverage for the ported
  code. Imported tests that cover non-ported upstream work are skipped and listed in
  the design doc. `tests/unit/` is a package here — imported test dirs get
  `__init__.py`. The pytest `markers` section is added.
- **Deployment**: off by default. The console activates only when
  `services.chat_app.evaluations.enabled: true` is set at deploy time. The FASRC dev
  trial flips it on **from the PR branch, before any merge**; rollback = disable and
  redeploy from `dev`.
- **Dependency evidence**: `mcp==1.27.2` raises the version the agent MCP stack
  resolves transitively today, so the change requires a clean fresh-env resolution
  (`pip check`) and the full unit suite, not just a smoke import. The imported
  upstream tests include real stdio and streamable-HTTP MCP integration coverage.
- **UX note**: `archi eval` (ported) and `archi evaluate` (ours) will coexist on the
  trial branch. The naming collision is a recorded adoption question, not fixed here.
- **Not in scope**: adoption into a release milestone, an SSO/bearer-aware
  `authorize_request`, RBAC role mapping for evaluations permissions, a real FASRC
  oracle MCP server, golden-set convergence, and any upstream sync. The adopt/reject
  writeup decides the follow-ups.
