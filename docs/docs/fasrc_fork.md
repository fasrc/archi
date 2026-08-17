# The FASRC fork — feature inventory vs upstream

[`fasrc/archi`](https://github.com/fasrc/archi) is FASRC's fork of
[`archi-physics/archi`](https://github.com/archi-physics/archi). This page is an
exhaustive inventory of everything the fork (and its private companion repo
[`fasrc/archi-config`](https://github.com/fasrc/archi-config)) adds that is **not**
in upstream.

**Snapshot:** 2026-07-24 — `origin/dev` at `0ed6fe66` (PR #150) vs `upstream/main`;
merge-base `d1c29380` (2026-03-24). The fork is ~207 commits ahead (528 files
changed, 347 added) and 83 commits behind upstream's tip.

**How to read this page**

- Plain `#N` numbers are [fasrc/archi pull requests](https://github.com/fasrc/archi/pulls);
  `archi-config#N` are PRs/issues in the private config repo.
- Each item is tagged: **[capability]** new feature, **[fix]** bugfix/hardening,
  **[design]** Loop-1 design only (not yet implemented), **[inherited]**
  upstream-authored work the fork carries but upstream's `main` doesn't have yet.
- To regenerate the raw delta:
  `git log --first-parent upstream/main..origin/dev` (PR-level list) and
  `git diff --name-status upstream/main...origin/dev` (file-level).

---

## Part I — fasrc/archi

### 1. Retrieval & ranking

- **Hierarchical rerank retrieval** [capability] — the fork's flagship retrieval
  change, now **default-on** after a measured A/B. Child-chunk hybrid (BM25 +
  vector) candidate generation → FlashRank CPU cross-encoder rerank → expansion to
  deduplicated *parent* context nodes stored in a new `document_parent_nodes`
  Postgres table (children linked via `metadata.parent_id`). Config-gated at
  `data_manager.retrievers.hierarchical_rerank.enabled`. PRs #30 (design), #31
  (implementation), #32 (A/B → default-on), #33 (archive).
  Key files: `src/data_manager/vectorstore/retrievers/hierarchical_retriever.py`,
  `retrievers/factory.py`, `node_parsing.py` (parent/child parsing; `sentence`
  default and `markdown` strategies), `schema.py` (idempotent runtime DDL for
  already-deployed volumes), `src/cli/templates/init.sql`,
  `docs/decisions/0001–0003-*.md` (ADRs). New pinned deps:
  `llama-index-core==0.14.19`, `flashrank==0.2.10`.
- **Configurable chunk sizes / strategy** [capability] — chunking exposed in
  config (part of the A/B harness work, PR #32). `src/cli/templates/base-config.yaml`.
- **Agent retrieval-tool budget** [fix] — caps `search_vectorstore_hybrid` calls
  per turn (default 2) with a neutral over-budget message. PR #21.
  `src/archi/pipelines/agents/tools/retriever.py`, `base_react.py`.
- **Forced initial retrieval** [fix] — the agent always performs one vector
  retrieval before answering. Landed with PR #22's branch.
  `cms_comp_ops_agent.py`.
- **Bedrock rerank backend** [design] — configurable rerank backend targeting
  AWS Bedrock. PR #129; `openspec/changes/add-bedrock-rerank-backend/`.

### 2. Citations

- **Hyperlink citations** [capability] — sources cited as `[title](url)` markdown
  links instead of numeric `[1]`, title metadata overlaid from Postgres; links
  open in a new tab. PRs #52/#53/#55. `src/archi/utils/citation_formatter.py`
  (shared by the chat UI and the `/v1` endpoint),
  `src/data_manager/vectorstore/postgres_vectorstore.py`; spec
  `openspec/specs/source-citations/spec.md`.
- **Tracked citation-guidance default** [capability] — every agent spec inherits
  `[title](url)` citation guidance without hand-copying it into each prompt.
  PRs #56/#57/#58 (closes #54). `src/archi/pipelines/agents/agent_spec.py`.

### 3. Data ingestion & scraping

- **HTML→Markdown→Categorize ingest processing** [capability] — configurable
  per-document processing at the persistence seam: `HtmlToMarkdownProcessor`
  (headings/lists/tables/links survive chunking) and `CategorizationProcessor`
  (optional LLM-assigned label in `metadata["llm_category"]`); best-effort, never
  blocks ingest. PRs #36/#38. `src/data_manager/collectors/processing.py`; new
  dep `markdownify==1.2.2`.
- **FASRC KB article slicing + source category capture** [capability] — slices
  the KB article body out of page chrome; captures the KB's own category as
  metadata. PR #97.
- **`archi sources build` command** [capability] — builds an importable web
  `sources.list` from a typed YAML manifest (literal / sitemap-expand / same-host
  crawl seeds) with glob filtering, URL normalization, dedupe, `--dry-run` diff,
  and advisory `--import`. PRs #35/#37/#64/#42.
  `src/cli/tools/sources_builder.py`, `examples/sources.manifest.yaml`; spec
  `openspec/specs/sources-build/spec.md`.
- **`sitemap-` source prefix** [capability] — a `sitemap-<url>` line in a source
  list expands at ingest time into the sitemap's page URLs (runtime counterpart
  to the offline `sources build`); v1 scoped to trusted first-party sitemaps,
  SSRF hardening for untrusted ones explicitly deferred. PRs #131/#133/#134.
  `src/data_manager/collectors/scrapers/sitemap_source.py`.
- **Content-hash staleness detection** [fix] — re-ingest detects changed content
  and refreshes chunks; upsert-by-URL previously masked in-place edits. PR #50
  (issue #39). `src/data_manager/vectorstore/manager.py`.
- **Trailing-slash URL canonicalization** [fix] — `_normalize_url` collapses
  trailing slashes so the same page isn't stored twice. PR #128 (issue #118).
- **Git code files load as text** [fix] — git-scraped code files are indexed
  instead of failing as unsupported. PR #108.
  `src/data_manager/vectorstore/loader_utils.py`.
- **`.ipynb` notebook loader** [capability] — `select_loader` handles Jupyter
  notebooks. PR #125 (issue #109).
- **Deeply-nested HTML segfault** [fix] — HTML→Markdown conversion made
  recursion-safe. PR #48 (issue #40).
- **Tolerant category extraction for reasoning models** [fix] — categorization
  survives `<think>`-style output (issue #44).
- **Catalog search apostrophe handling** [fix] — query grammar extracted to a
  testable `src/utils/catalog_query.py`; `'` treated as literal. PR #101.

### 4. Benchmarking & evaluation

- **Human-grading benchmark harness (Argilla + RAGAS)** [capability] —
  self-hosted Argilla 2.x stack for team-based human grading of benchmark
  outputs, with HUIT Bedrock Claude as the RAGAS judge. PR #18.
  `argilla/docker-compose.yaml`, `scripts/bootstrap_argilla.py`,
  `src/utils/benchmark_argilla.py`, `docs/eval/rubric.md`.
- **RAGAS prompt sweep** [capability] — run N agent-prompt variants through the
  harness with everything else held fixed; one rendered config per variant,
  leaderboard, pre-registration discipline
  (`docs/eval/preregs/_template.md`). PR #22.
  `scripts/benchmarking/generate_prompt_sweep.py`; CLI
  `archi evaluate --config-dir` multi-config sweeps.
- **Argilla grading-ops scripts** [capability] —
  `scripts/benchmarking/reset_argilla_dataset.py` (wipe submitted annotations via
  REST; PR #20), `qa_reset_grading.py`, `push_ragas_to_argilla.py`,
  `push_bench_output_to_argilla.py`, `rebuild_benchmark.sh` (rsync source into
  the deploy dir before rebuild — the image bakes a snapshot).
- **Hierarchical-rerank A/B harness + grounded question banks** [capability] —
  A/B config pairs and grounded RAGAS banks for the FASRC corpus. PR #32.
  `examples/benchmarking/hierarchical_rerank_ab/`,
  `examples/benchmarking/fasrc_ragas_queries.json`.
- **SUT `provider: local` for OpenAI-compatible endpoints** [fix] — the
  system-under-test resolves the right local mode so vLLM `/v1` endpoints don't
  get a `ChatOllama` 404. PR #74 (issue #73). `src/bin/benchmark_sut.py`.
- **Per-question failure isolation + degraded rows** [fix] — one bad question
  never aborts a run; context-overflow answers are marked degraded, never scored
  clean or sent to graders. PRs #90/#92/#94/#95.
  `src/utils/benchmark_resilience.py`; specs `benchmark-run-resilience`,
  `agent-context-resilience`.
- **RAGAS 0.3.5 modern dialect + per-metric eligibility** [capability] —
  normalizes the legacy authoring dialect (`question`/`answer`/`contexts`) onto
  the modern schema (`user_input`/`reference`/`retrieved_contexts`) with
  per-metric data-emptiness eligibility. PRs #93/#96.
  `src/utils/benchmark_schema.py`.
- **Question-bank preflight** [capability] — validates the bank against the
  harness schema in under a second, before the ~50-minute deploy+ingest.
  PRs #98/#100. `scripts/benchmarking/validate_queries.py`.
- **Anchor delivery + results interpretation docs** [fix] — anchors staged into
  the container; `docs/docs/interpreting_benchmark_results.md`. PR #105.
- **Bank rebuild + gold-source URL canonicalization** [fix] — PR #106.
- **Category-boost ceiling measurement** [design] — designed (PR #102) then
  shelved with eval-set defects recorded (PR #103).
- **RAGAS golden-set maintenance suite** [capability] — keeps the golden-set
  question bank in sync with the live KB; every detection pass is
  read-only/proposal-only (bank edits stay human-initiated). PRs #132 (design),
  #140 (per-row confirmation state `status`/`source_hashes` + backfill), #141
  (coverage gaps + orphan detection against the **live KB inventory** — the
  persisted corpus lags edits and never prunes), #142 (candidate proposal
  restricted to greenlit gaps + declines-only ledger), #144 (fact-drift
  detection: source-hash tripwire first, LLM diff only on tripped rows), #149
  (one-shot `report` command + nightly cron wrapper), #150 (summary-JSON schema
  docs). `scripts/benchmarking/goldenset_maintenance.py`,
  `src/utils/goldenset_maintenance.py`,
  `scripts/benchmarking/goldenset_report_cron.sh`; full usage in
  [Benchmarking](benchmarking.md).

### 5. FASRC agent, providers & LLM handling

- **FASRCDocsAgent** [capability] — a ReAct docs-assistant pipeline for FASRC
  research computing (Cannon cluster, SLURM, storage, accounts), CMS tooling
  stripped. PRs #25/#26; `fetch_catalog_document` auto-include via #47 → #49
  (revert) → #51 (re-apply). `src/archi/pipelines/agents/fasrc_docs_agent.py`,
  `examples/agents/fasrc-docs.md`.
- **Harvard HUIT Bedrock provider** [capability] — first-class provider for
  HUIT's Anthropic-compatible Bedrock proxy (data stays inside HUIT's compliance
  boundary); used as the RAGAS judge. Via PR #18.
  `src/archi/providers/huit_bedrock_provider.py`.
- **Anthropic content-block flattening** [fix] — collapses list-of-blocks
  `.content` to prose so users never see Python reprs. PR #66 (issue #41).
  `src/archi/pipelines/agents/message_content.py`.
- **Context-window overflow graceful degrade** [fix] — `invoke()` degrades
  instead of crashing. PR #91.
- **Orphan `</think>` tag stripping** [fix] — reasoning-model closing tags never
  reach visible output. PR #121.
- **Request-local LLM overrides** [fix] — closes the concurrency race where one
  request's A/B model override leaked into another request. PR #124 (issue #86).
  `src/archi/archi.py`, `base_react.py`, `chat_app/app.py`.
- **Config-propagation hardening + `enable_thinking` leak** [fix] —
  effective-config fingerprint logged and exposed via `/api/health`, after an
  incident where a process served stale cached config for two days. PR #85.
  `src/interfaces/chat_app/config_fingerprint.py`.
- **Provider `extra_kwargs` passthrough** [fix] — config `extra_kwargs` actually
  reach the client. PR #1.
- **`current_model_used` in `get_chat_response`** [fix] — PR #27.
- **Dependency pins** [fix] — loose deps pinned to unblock the CI resolver
  (PR #8); `langgraph-prebuilt<1.0.9` pinned to match `langgraph==1.0.2` (PR #19,
  issue #15). Rationale comments in `requirements/requirements-base.txt`.

### 6. Chat interface & OpenAI-compatible API

- **OpenAI-compatible `/v1` API** [capability] — `/v1/models` and
  `/v1/chat/completions` so Open WebUI / LiteLLM / Continue.dev can use archi as
  a backend; bearer-token auth with anonymous-access mode, token TTL and audit
  logging, self-service token check/generate/revoke. PR #7 plus follow-up fixes.
  `src/interfaces/chat_app/openai_compat.py` (gated by
  `services.chat_app.openai_compat.enabled`), token tables in
  `src/cli/templates/init.sql`, `scripts/setup_auth_tables.py`. Docs:
  [api-reference-v1.md](api-reference-v1.md),
  [openwebui-integration.md](openwebui-integration.md);
  `examples/deployments/openwebui/`.
- **Multi-collection routing** [design] — proposal only:
  `docs/docs/proposals/multi-collection-routing.md`.
- **Model-label chip removed from assistant messages** [capability] — PR #11.
- **FASRC splash/welcome copy** [capability] — rebranded from CMS. PR #23.
- **A/B comparison insert + `/v1` upgrade migration repair** [fix].
- **Dev chat UI port derived from config** [fix] — no hardcoded 7866. PR #89.
- **Source display-name truncation** [fix] — PR #2.

### 7. Security hardening

- **Markdown XSS in chat closed; Grafana anonymous access default OFF; ELOG
  scraper SSL verification default ON** [fix] — one security pass (note: PR #10
  had earlier enabled anonymous read-only Grafana; the pass flipped the default
  back off).
- **`/v1` bearer-auth hardening** [fix] — token TTL, audit logging,
  anonymous-mode rules (part of PR #7's arc).
- **Secret-hygiene `.gitignore` policy** [fix] — default-deny
  `deploy/fasrc-dev/*` with an explicit allow-list; `**/*.env` and `**/*secret*`
  never trackable even under allowed dirs; the private nested `config/` checkout
  ignored. PR #5 and the deploy-tracking commit.

### 8. Deployment & ops (fasrc-dev host)

- **Tracked fasrc-dev deploy scripts** [capability] —
  `create.sh`/`redeploy.sh`/`nuke.sh`/`status.sh` wrappers around the archi CLI;
  deployment name hard-wired to `dev`; secrets sourced from `~/.secrets/`.
  `deploy/fasrc-dev/scripts/`, `config.example.yaml` (the real config, agents,
  and manifests stay gitignored).
- **Config provisioning at a SHA-verified pin** [capability] — every deploy
  provisions the private `fasrc/archi-config` checkout at a pinned tag, verified
  by SHA (`ensure_config` in `deploy/fasrc-dev/scripts/lib.sh`; executable
  contract in `test_ensure_config.sh`). PRs #110/#111/#113/#120; pin bumps #116,
  #134. Spec `openspec/specs/deploy-config-provisioning/`.
- **Idempotent host firewall script** [capability] — reproducible record of
  archi service ports that Puppet doesn't manage; deliberately not wired into
  deploy (privileged action stays human). PR #127.
  `deploy/fasrc-dev/scripts/firewall.sh`.
- **Deployment source-commit provenance** [capability] — deploys record the
  archi git commit (`SOURCE_COMMIT` artifact, `-dirty` suffix; never fatal).
  PR #75. `src/cli/managers/source_version.py`.
- **`--dev` restart-only development mode** [capability] — bind-mounts source
  into services so code edits need only a restart; dev-mount macros, `.pyc`
  suppression, staged `./data/agents` mount. PR #3.
  `src/cli/templates/base-compose.yaml`, `src/cli/cli_main.py`.
- **Docker check off the `--dry` path** [fix] — `archi create --dry` no longer
  requires a Docker daemon. PR #126 (issue #112).
- **vLLM ops notes** [capability] — GPU-memory OOM cap at 0.88, no
  reasoning-parser flag, systemd notes. PRs #24/#46 → `docs/docs/fasrc_archi.md`.

### 9. CI & quality gate

- **`scripts/gate.sh` single-source quality gate** [capability] — format (black,
  isort) → tests with **diff coverage** (≥80% on changed lines via diff-cover,
  not a whole-package floor); release PRs (dev→main) compared against dev with
  coverage advisory. Invoked identically by the pre-commit hook
  (`hooks/pre-commit`), CI, and the autonomous dev loop so they can't drift.
  From PR #31; refinements in #68 and follow-up commits.
- **`.github/workflows/ci.yml`** [capability] — runs the same gate on a GitHub
  runner.
- **Black/isort adoption** [capability] — isort on the black profile (PR #28),
  whole-tree normalization with no logic changes (PR #69),
  `.git-blame-ignore-revs` so blame skips the reflow (PR #70), CI `--check`
  enforcement (PR #71).
- **Pyright config + RBAC type/test debt paid** [capability] — `[tool.pyright]`
  in `pyproject.toml` (PR #6); RBAC pyright errors fixed with new unit coverage
  (`tests/unit/test_rbac_*.py`).
- **GHCR dual-publish** [capability] — base images pushed to ghcr.io alongside
  docker.io. PRs #13/#16/#17. `.github/workflows/publish-base-images.yml`.
- **~90 new unit-test files** plus `tests/unit/conftest.py` and smoke-test
  updates — upstream has no comparable test suite for these areas.

### 10. Autonomous development workflow

- **Ralph loop harness** [capability] — containerized autonomous dev loop that
  drains one task at a time through the quality gate. Root files: `Makefile`
  (`make loop-headless`), `Containerfile`, `PROMPT.md` (one-task-per-invocation
  contract), `ralph.conf` (pins `GH_REPO=fasrc/archi`; `RALPH_TASKS` forwarded
  into the container — PR #117), `tasks.md`, `STATUS.md`, `docs/questions.md`
  (loop-appended open questions), `docs/operator-checklist.md`.
- **`WORKFLOW.md`** [capability] — the fork's two-loop spec-driven development
  contract: origin is trunk, PRs target `fasrc/archi:dev`, the gate must pass,
  OpenSpec for planning.
- **Tracked Claude Code subagents** [capability] — `.claude/agents/`
  (`black-seam-scout`, `ingestion-verifier`, `provider-config-auditor`) versioned
  as institutional knowledge while the rest of `.claude/` stays ignored. PR #67.
- **Codex OpenSpec skills** [capability] — `.codex/skills/openspec-*` (10
  skills) so the Codex CLI can drive the same OpenSpec workflow.

### 11. Process — OpenSpec adoption

Upstream's OpenSpec scaffold was removed (PR #12: the four deleted files —
three `.github/prompts/openspec-*.prompt.md` Copilot prompts and
`openspec/AGENTS.md`), then OpenSpec was re-adopted wholesale with a different
toolchain (PR #31 onward): `openspec/config.yaml`, **tracked** `openspec/specs/`
(9 living capability specs: `agent-context-resilience`,
`benchmark-bank-preflight`, `benchmark-run-resilience`,
`deploy-config-provisioning`, `hierarchical-rerank-retrieval`,
`ingest-processing`, `retrieval-benchmarking`, `source-citations`,
`sources-build`), ~13 archived changes, and in-flight change folders. The fork
un-ignored `openspec/` in `.gitignore` — upstream ignored it; the fork versions
its specs. `AGENTS.md` was rewritten, adding the rule that PR/issue bodies link
first-use jargon to the [glossary](glossary.md).

### 12. Documentation added by the fork

- [Glossary](glossary.md) with site-wide acronym hover tooltips
  (`docs/docs/includes/abbreviations.md` auto-appended via `pymdownx.snippets`).
  PR #107.
- `docs/docs/fasrc_archi.md` — FASRC deployment/ops notes (PRs #24/#46).
- `docs/docs/rag_architecture.md` — map of the RAG pipeline and its extension
  seams.
- [Interpreting benchmark results](interpreting_benchmark_results.md) (PR #105)
  and the heavily extended [Benchmarking](benchmarking.md) page (#18…#150).
- [api-reference-v1.md](api-reference-v1.md),
  [openwebui-integration.md](openwebui-integration.md), and two proposals under
  `docs/docs/proposals/`.
- `docs/decisions/` — ADR directory (3 hierarchical-rerank ADRs).
- `docs/eval/rubric.md` and `docs/eval/preregs/_template.md`.
- `genai-api-companion-system-prompt.md` — system prompt for a HUIT GenAI API
  onboarding assistant.
- README title simplified to "Archi".

---

## Part II — fasrc/archi-config (private)

Everything in this repo is fasrc-added; there is no upstream equivalent. It is
provisioned into deployments at a SHA-verified pin (see §8). High-level
inventory — details live in the repo's own READMEs and specs:

### Deployment config & environments

- **`environments/dev.yaml`** — the authoritative config for the live fasrc-dev
  deployment, kept reconciled with the running instance: FASRCDocsAgent chat app;
  FASRC-hosted vLLM (Qwen) as default provider in `openai_compat` mode with an
  Anthropic standby provider for failover; Qwen "thinking" disabled so
  chain-of-thought can't bleed into answers; pgvector backend; HTML→Markdown +
  LLM categorization ingest (6 categories); sentence chunking +
  hierarchical-rerank enabled. `environments/production.yaml` and `staging.yaml`
  are placeholders.
- **`compose.yaml`** — docker-compose for the GPU host's vLLM server
  (tensor-parallel across 4 GPUs, 32k context).
- **Secrets policy** — no env files tracked anywhere in the repo; deploy secrets
  live outside the repo and are injected at deploy time.
- **`chatmusings/config-management-options.md`** — design memo that led to the
  `environments/` directory convention.

### KB source lists (`lists/`)

- **`sources.list`** — primary ingest list for dev: ~370 URLs, nearly all
  `docs.rc.fas.harvard.edu/kb/*` pages.
- **`dsrf_user_docs.list`** (~196 FASRC user-docs URLs) and **`slurm.list`**
  (~148 slurm.schedmd.com URLs, derived from `slurm-sitemap.xml`).

### Nightly automation (systemd, `scripts/systemd/`)

Five daily runs plus one weekly, ordered with `After=` dependencies so a long
run delays rather than collides. **No run ever merges** — a PR is the
deliverable; a human merges in daylight.

| Schedule | Unit | Role |
|---|---|---|
| 00:00 | `archi-triage-nightly` | Labels every untriaged issue (`auto-ok`/`explore`/`needs-human`/`needs-deploy`, type, exactly one P1/P2/P3, optional effort tier). Labels only. |
| 01:00 | `archi-explore-nightly` | Drains the oldest `explore` issue into an exploration note. Timer intentionally disabled pending rework (archi-config#3). |
| 02:00 | `archi-nightly` | The drain: supervisor turns the top `auto-ok` issue into an OpenSpec change, runs the Ralph loop (implement → gate → PR), then posts a per-run token-cost comment. 5-hour budget. |
| 04:00 | `archi-nightly-review` | Works reviewer feedback on the night's PRs — verify, fix test-first, reply in-thread, push. |
| 07:00 | `archi-triage-report` | Relays the night's journals to email via a GitHub Actions dispatch (below). |
| Thu 08:00 | `archi-weekly-report` | Weekly team update (below). |

Design points worth knowing:

- **Layered context flow**: issue → OpenSpec change → implementation loop; the
  loop never reads the GitHub issue — the OpenSpec change is the single source
  of truth.
- **Effort tiers**: triage prices each `auto-ok` issue as `sonnet` (mechanical,
  cheaper model; never on a P1 — enforced), default, or `ultracode`
  (plausible-but-wrong risk → multi-agent adversarial verification brackets the
  run).
- **Hardening** (OpenSpec change `harden-nightly-automation`, live-validated):
  scoped fine-grained token with a start-time capability probe (the unit aborts
  unless the token is *denied* admin endpoints); a deterministic deny-hook that
  blocks merging, pushes to trunk, `--no-verify`, destructive scripts, and
  writes to secrets/control-plane files; systemd owns the loop's lifetime with a
  supervisor that asserts a deliverable (open PR or clean halt) or exits
  non-zero; failure alarms on every unit relay the journal to the operator; a
  night-window guard keeps boot catch-up from running next to an interactive
  session. Hermetic bash fixture tests cover the supervisor, deny-hook, window
  guard, and token report.

### Weekly reporting

- **`archi-weekly-report`** + retry wrapper (3 attempts, 5-minute backoff —
  added after a transient provider 529 ate a Thursday run): generates a
  self-contained HTML team update (skip-level TL;DR, work done, completed Asana
  tasks, merged PRs, open issues grouped P1→P2→P3), runs a no-secrets scan, and
  uploads to Google Drive without ever overwriting prior weeks. Spec'd in
  `openspec/specs/weekly-report/spec.md` (degraded modes still ship; no invented
  figures; secret scan blocks upload).

### Triage-report relay (journal → email)

- **`send-triage-report.sh`** concatenates the night's journals (tail-truncated)
  and fires a `repository_dispatch`; **`.github/workflows/triage-report.yml`**
  comments the payload on a pinned issue as `github-actions[bot]`, whose
  comments email the operator (own-token comments don't). Injection-safe:
  payload passed via `env:` only, body fenced. Spec:
  `openspec/specs/report-relay/spec.md`.

### Per-run token-cost reporting

- **`nightly-token-report.sh`** — after the drain's PR exists, sums per-message
  token usage from the loop's dedicated transcripts filtered to the run window,
  adds the bounded steps' authoritative costs, and posts one Markdown breakdown
  comment with an estimated dollar figure on the PR. Best-effort by contract — a
  reporting failure never fails the drain. archi-config#4, turn-count fix
  archi-config#5. Motivated by an invisible token blowup that exhausted the
  shared usage window and broke a later run.

### Ops scripts

- **`scripts/prune-build-cache.sh`** — deep-cleans Docker's build cache with
  before/after disk usage; renamed from `nuke.sh` so it can't be confused with
  the destructive data-volume script the nightly rails forbid. Conventions
  codified in `openspec/specs/ops-scripts/spec.md`.

### Agents (chatbot system prompts, `agents/`)

- **`fasrc-archi-v12.md`** — the current production FASRC agent prompt: hybrid
  vector search first, exactly one search per question, answer only from
  retrieved chunks with inline citations, refuse-and-refer when the docs don't
  cover it, verbatim closing disclaimer pointing to FASRC office hours/rchelp.
- **`fasrc-inline-v1.md`** — benchmarking variant citing `[title](url)` inline.
- **`agents/archive/`** — 14 prior prompt iterations preserved for sweeps and
  history (v5 through v11, the cannon variants, test agents).

### Benchmarking assets (`benchmarking/`)

- **`fasrc_ragas_queries.json`** — the main RAGAS golden-set bank: 105 questions
  in the RAGAS 0.3.5 schema, each tagged with an `anchor_type` difficulty
  (easy_retrieve / reasoning / should_refuse); schema and draft-row semantics
  documented in the adjacent README.
- **`ragas.yaml`** — master benchmark deployment config: same corpus/agent/model
  as dev, port-isolated to run beside the primary deployment, git-source
  suffixes extended for HPC files (`.sbatch`, `.slurm`, Fortran, CUDA, R,
  MATLAB, notebooks), RAGAS judge pinned to HUIT Bedrock Claude — independent of
  the system under test to break self-style bias — and Argilla dual-grader
  minimums.
- Additional question banks with paired configs (`queries.json`,
  `ragas-basic-pt1`, `ragas-jeopardy-master`, `ragas-snow-pt1-clean`) and
  **`prompt_sweep.yaml`** for the prompt-sweep generator.

### Process

- OpenSpec adopted in this repo too: specs for `ops-scripts`, `weekly-report`,
  `report-relay`, and `issue-priority` (the P1/P2/P3 taxonomy the nightly triage
  maintains), plus archived and in-flight changes.

---

## Part III — inherited from upstream branches

Upstream-authored work the fork carries that is **not yet** in `upstream/main`
(arrived via merges of upstream branches):

- **Indico scraper** — CERN Indico events/materials, with MarkItDown-based
  PPTX→markdown slide conversion (upstream PR #550; fork applied functional
  review fixes). `src/data_manager/collectors/scrapers/integrations/indico_scraper.py`,
  `src/data_manager/collectors/utils/slide_converter.py`.
- **ELOG scraper** + FNAL dCache example deployment (upstream PR #456); the fork
  later defaulted its SSL verification ON.
- **`mid` → `message_id` schema rename** + migration (upstream PR #501).
- **Secrets guide + adding-providers tutorial** (upstream PR #528).
- **PG env vars for redmine/mailbox/piazza/mattermost** and the
  **`mcp_servers_config` column** in `static_config`.

---

## Appendix — caveats for anyone re-deriving this list

- A large share of the *modified* (not added) files in the raw diff carry **no
  behavioral change**: they were touched only by the whole-tree black/isort
  normalization (PR #69, listed in `.git-blame-ignore-revs`). Don't read
  provider/RBAC/interface file diffs as features without checking their history
  past that commit.
- The four files the fork *deleted* were upstream's OpenSpec prompt scaffold,
  replaced by the fork's own OpenSpec setup (§11) — no capability was lost.
- Comparisons here are against `upstream/main` as fetched on the snapshot date;
  upstream has moved since divergence (83 commits), so a future re-run may show
  items upstream has independently adopted.
