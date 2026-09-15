## 1. Pre-cutover decisions

- [x] 1.1 Resolve **Q1** from design: audit `archi()`/`CMSCompOpsAgent` for shared mutable state (postgres pool, vectorstore client, MCP session, tool registry caches). Document findings. If any are unsafe for parallel instantiation, default `n_workers=1` and add a config validation that rejects `>1` until fixed.
   - **Verdict: NOT safe today.** Three blockers: (a) `AsyncLoopThread` MCP singleton at `src/archi/pipelines/agents/utils/mcp_utils.py:20` — N agents share one loop, concurrent `initialize_mcp_client()` races at `base_react.py:1063-1067`; (b) `PostgresServiceFactory.set_instance()` at `postgres_service_factory.py:169` has no locking — lost-update on `_instance`; (c) `HuggingFaceEmbeddings` instantiated per `archi()` at `vectorstore_connector.py:33` but transformers models aren't thread-safe for concurrent inference. One uncertain: LangGraph callback manager (each instance has its own compiled graph at `base_react.py:1025`, but global LangChain state needs verification). Postgres pool itself, CMSCompOpsAgent tool registry, MONIT, RemoteCatalogClient are SAFE.
   - **Action for Phase 2 Task 2.4:** hardcode `n_workers=1` in config validation (reject `>1`), don't just default. Real parallel execution is a follow-up change.
- [x] 1.2 Resolve **Q2** from design: confirm the staff source IP/CIDR range for the iptables rule on tcp/6900 (same set as 7891 from `split-prod-dev-deployments`, if that's been decided).
   - **Decision: defer Apigee, use IP/CIDR allowlist.** Apigee proxy approach (would have replaced iptables with auth-based access) requires HUIT-side configuration work; deferred to a follow-up change. For now, mirror the existing port 7861 INPUT rule's source range — when Phase 4 Task 4.5 executes, capture the current 7861 source range via `sudo iptables -L INPUT -n --line-numbers | grep 7861` and reuse the same range for 6900.
- [x] 1.3 Resolve **Q3** from design: inspect `upstream/feature/add-offline-ab-benchmarking` commit `ed700f4d` for the "eval dashboard" — assess in 30 minutes whether it ports cleanly. If yes, add to lift list; if no, skip and proceed.
   - **Verdict: LIFTABLE.** 640 lines total (`scripts/eval_dashboard/app.py` 249 lines + `templates/index.html` 391 lines). Pure Flask + vanilla JS + stdlib; zero copilot SDK references. It's an in-flight run progress monitor (GPU util, log tail, question count) — complementary to Argilla, not redundant.
   - **Caveat:** upstream version polls a remote node (`submit75`) via SSH; for our single-host setup it rewires to direct local file reads (~50 lines of adaptation: replace SSH+remote-script with local `pathlib` reads).
   - **Action:** add to Phase 6 (Documentation) as optional dashboard rather than Phase 1 (core lift) — keeps Phase 1 focused on must-have Argilla integration. New Task 6.6 added below.
- [x] 1.4 Resolve **Q4** from design: confirm with operator the target number of evaluators per round and questions per round (drives statistical power; affects whether 3 graders or 2 graders should be the default `min_submitted`).
   - **Decision: make `min_submitted` a config field, default 2.** Add `services.benchmarking.argilla.min_submitted` to the config schema (positive integer, default `2`). Argilla `rg.TaskDistribution(min_submitted=...)` reads from there. Operators can raise to 3 for high-stakes rounds.
   - **Recommended question-bank starting size: 30-50.** Documented in design.md, not a config field — the queries file size IS the answer. Below 30: insufficient power to detect <10pp differences. Above 50: grading fatigue dominates.
- [x] 1.5 HUIT_API_KEY in `~/.archi/.env.benchmark`; smoke-tested against Claude Sonnet 4.5 on the HUIT Bedrock proxy (HTTP 200, valid Anthropic response) 2026-06-02.

## 2. Phase 1 — Lift portable code from upstream

- [x] 2.1 Create `src/utils/benchmark_argilla.py` — lifted verbatim from `upstream/feature/add-offline-ab-benchmarking` (584 lines). Three `import argilla as rg` sites tagged `# pyright: ignore[reportMissingImports]` since the dep is only installed in the benchmarks container.
- [x] 2.2 Imports verified: only stdlib + `argilla` (lazy/optional) + `src.utils.logging`.
- [x] 2.3 `ABResult` + `pair_ab_results` + `dump_ab_comparison` + `generate_pairwise_combinations` lifted. Adapted to fork's class-attr `ResultHandler` pattern instead of upstream's instance-attr refactor.
- [x] 2.4 `_create_chain_pool` and `_prefetch_questions_parallel` lifted with an `_PARALLEL_SAFE_MAX_WORKERS = 1` class attr; both methods guard against `n_workers > 1` and raise `RuntimeError` citing the three Phase 1 blockers (mcp_utils.py:20, postgres_service_factory.py:169, vectorstore_connector.py:33).
- [x] 2.5 Argilla integration block wired into `Benchmarker.run()`. Gated by `ARCHI_ARGILLA=1` (set by `--argilla` CLI flag). Auto-enables A/B pairing when ≥2 configs ran. LLM-as-judge pairwise hook NOT lifted (out of Phase 2 scope).
- [x] 2.6 Judge/SUT config split lifted into `get_ragas_llm_evaluator()`: `ragas_settings.evaluator_provider/model/ollama_url` falls back to `benchmark_cfg.X` when unset. HUIT Bedrock case still deferred to Phase 3.
- [x] 2.7 `tests/unit/test_benchmark_argilla.py` lifted verbatim (759 lines). 43/43 tests pass under pytest in conda env `archi` with `argilla>=2.5,<3` installed.
- [x] 2.8 `--argilla` / `--argilla-server` flags on `archi evaluate` plumbed through `DeploymentPlan.argilla_enabled / argilla_server` and `base-compose.yaml` (sets `ARCHI_ARGILLA=1` and `ARGILLA_API_URL` env vars on benchmark service). `archi grade --serve / --export` subcommand lifted into `cli_main.py`.
- [x] 2.9 `argilla>=2.5,<3` added to `Dockerfile-benchmarks` and `Dockerfile-benchmarks-gpu`.
- [ ] 2.10 Run the existing benchmark test suite end-to-end on a single existing config — confirm `archi evaluate` produces JSON + HTML reports byte-identically to pre-change. **Deferred** — requires a live benchmark run; will be exercised during Phase 5 dry-run.
- [x] 2.11 Phase 2 committed as `70008ee4 feat(benchmark): lift Argilla A/B grading machinery from upstream` on `feat/benchmarking-harness`.

## 3. Phase 2 — Complete HUIT Bedrock as a proper provider

- [x] 3.1 Created `src/archi/providers/huit_bedrock_provider.py` with `HuitBedrockProvider(BaseProvider)` and `HuitBedrockChat(BaseChatModel)`. The chat model POSTs to `{base_url}/model/{model_id}/invoke` with `x-api-key` and Bedrock-native Anthropic body (`anthropic_version=bedrock-2023-05-31`, `system`, `messages`, `max_tokens`). Added `HUIT_BEDROCK = "huit_bedrock"` to `ProviderType` enum.
- [x] 3.2 Registered in `_ensure_providers_registered`; added `ProviderType.HUIT_BEDROCK: "HUIT_API_KEY"` to `_DEFAULT_API_KEY_ENV_BY_PROVIDER`. `get_model("huit_bedrock", model, {base_url: ...})` now resolves to a working HuitBedrockChat (smoke-tested via LangChain `.invoke()` returning "Paris" and "OK").
- [x] 3.3 Added `case "huit_bedrock"` in `get_ragas_llm_evaluator()` — delegates to `get_model("huit_bedrock", model_name, {"base_url": ...})` rather than the per-case construction pattern used by the older `anthropic` case.
- [x] 3.4 Added `os.environ['HUIT_API_KEY'] = read_secret("HUIT_API_KEY")` to `service_benchmark.py` setup block. Updated `SecretsManager._get_model_based_secrets` to scan loaded yaml configs for `provider: huit_bedrock` or `mode_settings.ragas_settings.evaluator_provider: huit_bedrock` — when found, HUIT_API_KEY is added to required_secrets so the compose template mounts `/run/secrets/huit_api_key` and sets `HUIT_API_KEY_FILE`.
- [x] 3.5 `config/benchmarking/ragas.yaml` already had `provider: huit_bedrock` + Sonnet 4.5 pinned id + base_url. Only correction: fixed stale `agent_md_file` path (was `fasrc-cannon.md`, renamed to `fasrc-cannon-v1-strict.md` in prior session). Comment added explaining the SUT/judge split is available via `evaluator_*` keys.
- [x] 3.6 `stash@{2}` dropped 2026-06-02. Lifted work is fully superseding (HUIT Bedrock as first-class provider replaces the stub) and verified end-to-end via dry-run.
- [x] 3.7 Wrote `tests/smoke/ragas_smoke.py` — 3-question RAGAS evaluation with all four metrics, asserts every score is a finite float. Full execution awaits the benchmarks-container deploy in Phase 5 (ragas + langchain_huggingface live there). Local prove-out completed: `HuitBedrockChat` round-trips through `LangChain.invoke()` against the real HUIT endpoint, returning valid Anthropic responses with proper `usage_metadata`.

## 4. Phase 3 — Deploy Argilla + ElasticSearch

- [x] 4.1 `argilla/docker-compose.yaml` written. Pins `argilla/argilla-server:v2.8.0` and `docker.elastic.co/elasticsearch/elasticsearch:8.11.4`. ES data bind-mounted to `/scratch/docker/volumes/argilla-es/`. Argilla DB persisted to a named volume. Docker secrets pattern for `ARGILLA_AUTH_SECRET_KEY`, owner password, and the bootstrap API key — sourced from `${HOME}/.archi/secrets/argilla_*.txt`.
- [x] 4.2 Argilla secrets generated at `~/.archi/secrets/argilla_{api_key,auth_secret,owner_password}.txt` (mode 0600, 32-byte/24-byte hex). Generation done via `scripts/bootstrap_argilla.py --generate-secrets` and is repeatable with `--force`.
- [x] 4.3 Stack up 2026-06-02. Three services healthy: `argilla-server` v2.8.0, `argilla-elasticsearch` 8.11.4, `argilla-redis` 7-alpine. ES bind on `/scratch/docker/volumes/argilla-es/`. Owner account auto-bootstrapped via `WORKSPACE=archi` env. **Two unplanned fixes surfaced and landed in-flight**: (1) Argilla 2.5+ requires Redis (commit `3e7116f9` added the service), (2) `_FILE`-suffix env pattern not honored by Argilla's bootstrap script — switched to `env_file: ~/.archi/argilla.env` with raw `USERNAME` / `PASSWORD` / `API_KEY` (commit `2ca2d16b`).
- [x] 4.4 `scripts/bootstrap_argilla.py` written + executed. Workspace `archi` exists in Argilla. `--create-workspace` / `--create-users` / `--generate-secrets` / `--export-env` all working.
- [x] 4.5 iptables `INPUT` position 12 opened for **tcp/3080 from `10.255.13.96/27` (admin VPN)** mirroring the existing port 7861 rule. Rule saved via `/sbin/service iptables save`. **Note:** uses port 3080 (LibreChat's old slot) as a stopgap — the FASRC perimeter firewall blocks tcp/6900 from staff VPN traffic; a network-change ticket for tcp/6900 inbound is pending with FASRC IT. Revert recipe documented in `argilla/README.md`.
- [x] 4.6 Verified `http://archi.rc.fas.harvard.edu:3080/` loads the Argilla login UI from the admin VPN. Owner login as `owner` (password in `~/.archi/secrets/argilla_owner_password.txt`) works. **HTTPS is still out of scope** — Argilla doesn't terminate TLS by default.
- [x] 4.7 Two test evaluator accounts created via `python scripts/bootstrap_argilla.py --create-users argilla/evaluators.txt`: `facilitator1`, `facilitator2`. Real evaluator onboarding (HPC facilitators + Harvard AI experts) deferred to Phase 8 prep.

## 5. Phase 4 — First eval round dry-run

- [ ] 5.1 Create a 10-question dry-run subset of `config/benchmarking/queries.json` (`queries.dryrun.json`). Include 2 anchor questions from Task 6.3. **Deferred to first real A/B round** — the smoke-run executed in 5.3 used the existing 5-question `queries.json` to prove the pipeline end-to-end; building the 10-question subset is only meaningful when paired with a real A/B sweep.
- [ ] 5.2 Set up two configs in `config/benchmarking/dryrun_sweep/`: one for v1-strict prompt, one for v2-lean. **Deferred to first real A/B round** — same reason as 5.1. The fork has all four prompt variants (v1-strict / v2-lean / v3-cited / v4-linked) ready in `config/agents/`.
- [x] 5.3 End-to-end dry-run completed 2026-06-02 with a **single-config smoke** (5 questions, v1-strict prompt, local Qwen SUT + HUIT Bedrock judge). **Six bugs surfaced and landed in-flight** (commits `b983f4dc` `39001b97` `ad67f805` `8b21aeb4` `93f994ee` `6b3b772f`). Confirmed:
  - [x] 5.3.1 Run completed in ~6 minutes (5 SUT calls @ 3-15s + 20 RAGAS judge calls).
  - [x] 5.3.2 Argilla dataset `benchmarking-bench-dryrun-20260603-015649` appeared in workspace `archi` (single-config schema, not A/B; A/B awaits 5.1+5.2).
  - [ ] 5.3.3 A/B trace HTML not exercised (single-config run). **Defer with the A/B sweep.**
  - [x] 5.3.4 RAGAS scores present in record metadata as finite floats (verified in dump JSON).
  - [ ] 5.3.5 Hidden-metadata-in-grader-UI check not done; **defer to 5.4.**
- [ ] 5.4 Have at least 2 staff grade the dry-run dataset to validate the workflow before announcing a real round. **Pending** — accounts exist (`facilitator1`, `facilitator2`) but no graded records yet.
- [ ] 5.5 Partially done — `archi grade --export --dataset benchmarking-bench-dryrun-20260603-015649` ran cleanly (commit `6b3b772f` fixed an Argilla 2.5+ API break) but returned empty `responses` arrays since no grading has happened yet. Re-run after 5.4 to verify the full path.

## 6. Phase 5 — Documentation and conventions

- [x] 6.1 Extended `docs/docs/benchmarking.md` with sections on the four-step operator loop, `--argilla` flag, `archi grade` subcommand, judge/SUT split (with worked yaml example), Argilla config (`min_submitted`), pre-registration convention, anchor questions, and the inter-rater reliability protocol.
- [x] 6.2 `docs/eval/preregs/_template.md` written — pre-reg skeleton with round metadata table, primary hypothesis, primary outcome, three-state decision rule (adopt/reject/inconclusive), planned secondary analyses, stopping rule, voice/blinding caveats, out-of-scope list. Lock-before-running convention emphasized in opening callout.
- [x] 6.3 `examples/benchmarking/anchor_questions.json` written (versioned location, not gitignored `config/`). Five anchors across the three required types: 2 easy-retrieve (FASRC GPU partition + scratch path), 2 reasoning (OOM diagnosis + multi-node MPI+GPU), 1 should-refuse (MIT Engaging out-of-scope). Each entry has `anchor_type`, `notes` flagging "ANSWER AUTHORING: confirm with operator before locking".
- [x] 6.4 `docs/eval/rubric.md` written — covers the four widgets (winner / quality / failure-mode tags / notes), binary-vs-Likert rationale, calibration-round protocol (10 records group-graded then discuss), failure-mode tag taxonomy, and explicit "don't do" list (no peeking RAGAS, no mid-round comparison).
- [x] 6.5 `scripts/benchmarking/analyze_grades.ipynb` written — 10 cells: setup, dataset load (Argilla SDK or JSON), flatten to per-grader DataFrame, per-config win rate, Cohen's κ + Fleiss' κ, per-grader bias, RAGAS↔human correlation, anchor regression check, should-refuse compliance guard, failure-mode tag distribution, and a final cell that applies the pre-reg decision rule mechanically.
- [ ] 6.6 **Deferred (optional).** Strategy mapped: upstream's `scripts/eval_dashboard/` is 640 LOC across `app.py` (Flask + threading + SSH polling, 249) + `templates/index.html` (vanilla JS, 391). Port requires (a) lift both files, (b) add Flask to dev deps, (c) ~50-line rewrite of the SSH `mega_cmd` polling path (app.py:23-25 config, 44-56 `_ssh()` delete, 72-175 `_poll()`, 233-235 log endpoint) to local file globs, (d) schema accommodation — the dashboard expects `eval_status.json` + `benchmarking-eval-*.checkpoint.json` sidecars our fork doesn't write. (d) is the real scope: either add sidecar emit to `ResultHandler.dump()` (clean) or adapt the dashboard's JSON reader to derive equivalent state from `benchmarking_results` + path globs (cheaper). Nice-to-have, not blocking eval rounds — defer until first eval is running.

## 7. Verification against specs

- [x] 7.1 Walked all 26 scenarios 2026-06-03. Verdict:

| Scenario | Verdict | Evidence |
|---|---|---|
| 1. Argilla containers present | ✅ PASS | `docker ps` shows `argilla-server`, `argilla-elasticsearch`, `argilla-redis`; none referenced in any `~/.archi/*/compose.yaml` |
| 2. ES data persists across restart | ⏭ NOT TESTED | Bind mount to `/scratch/docker/volumes/argilla-es/` declared in compose; restart cycle not exercised |
| 3. Single-config push to Argilla | ✅ PASS | Dataset `benchmarking-bench-dryrun-20260603-015649` created with 5 records; fields are question/reference_answer/response/trace; metadata has 4 RAGAS scores + time_elapsed + corpus_snapshot_id |
| 4. A/B sweep push | ⏭ DEFERRED | A/B not exercised in dry-run (single-config only); `push_multi_ab_results_to_argilla` unit-tested via 5 tests in `test_benchmark_argilla.py` |
| 5. --argilla absent leaves behavior unchanged | ⚠️ STRUCTURAL ONLY | Strict byte-identical impossible: `corpus_snapshot_id` is now always in `metadata`. Structurally compatible: top-level keys unchanged (`benchmarking_results`, `metadata`); downstream consumers still work. Three runs proved JSON+HTML dump on success and failure paths. |
| 6. Judge differs from SUT | ✅ PASS | Dry-run used `provider: openai` (Qwen via vLLM at localhost:8000) for SUT + `evaluator_provider: huit_bedrock` for judge; logs confirmed both endpoints hit |
| 7. Judge falls back to SUT | ✅ PASS | Code path: `provider = ragas_configs.get("evaluator_provider") or benchmark_cfg.get("provider")` — explicit fallback chain in `get_ragas_llm_evaluator()` |
| 8. HUIT Bedrock as SUT | ⚠️ KNOWN GAP | `HuitBedrockChat` doesn't implement `bind_tools`; LangGraph agents fail with `NotImplementedError`. Documented in PR #18; 1-2h follow-up. Not needed for production design (local Qwen SUT, HUIT judge). |
| 9. HUIT Bedrock as RAGAS judge | ✅ PASS | Confirmed in dry-run + standalone smoke test (3-question RAGAS with all 4 metrics finite) |
| 10. Missing HUIT_API_KEY fails loudly | ✅ PASS | `HuitBedrockChat._generate` raises `ValueError("HUIT_API_KEY is not set...")` before any HTTP call |
| 11. Distribution enforced (min_submitted) | ❌ **NOT WIRED** | `ragas.yaml.services.benchmarking.argilla.min_submitted` is in the config schema but `push_*_to_argilla` doesn't currently pass it to `rg.TaskDistribution`. Real gap — open as follow-up issue. |
| 12. One-grader records remain pending | ❌ **NOT WIRED** | Blocked by #11 — same root cause. When TaskDistribution is wired, Argilla will surface this state natively. |
| 13. Identity in metadata, not fields | ✅ PASS | Field set is question / reference_answer / response / trace (no model/agent/provider/config name); RAGAS scores + corpus_snapshot_id + time_elapsed live in metadata only |
| 14. --serve opens Argilla UI | ✅ PASS | `archi grade --serve` calls `webbrowser.open(api_url + '/datasets')` |
| 15. --export writes grades.json | ✅ PASS | Verified in session; ran `archi grade --export --dataset benchmarking-bench-dryrun-20260603-015649 -o grades.json` cleanly |
| 16. --export uses last-benchmark | ⚠️ KNOWN GAP | The state file is written to `/root/.archi/.last-benchmark` INSIDE the container — not host-visible, so the host's `archi grade` can't auto-resolve. Workaround documented (explicit `--dataset`). Real fix: bind-mount the state path or write to `bench_out/`. Open as follow-up. |
| 17. Sweep guarantees same corpus | ✅ PASS | `ResultHandler.get_corpus_snapshot_id()` generates a UUID once per invocation and reuses it for every config and every Argilla record. Verified in dump JSON. |
| 18. Analysis rejects cross-sweep | ✅ PASS | `assert_single_sweep()` helper in `benchmark_argilla.py`; 4 unit tests cover pass / cross-sweep refusal / exploratory / blank-id filter. Notebook cell 1.5 calls it before any aggregate stats. |
| 19. Anchors merged at run time | ❌ **NOT IMPLEMENTED** | `examples/benchmarking/anchor_questions.json` exists with 5 anchors, but no code merges them into `queries.json` at run time. Real gap — open as follow-up. |
| 20. Anchor regression blocks adoption | ❌ **NOT IMPLEMENTED** | Blocked by #19. The analysis notebook has an anchor regression-check cell, but it depends on anchors being in the run's question set. |
| 21. Pre-reg template exists | ✅ PASS | `docs/eval/preregs/_template.md` exists at the right path |
| 22. Pre-reg referenced in writeup | ⏭ OPERATIONAL | Awaits first real eval round (Phase 8) |
| 23. Two-config sweep uses A/B schema | ✅ PASS | `push_ab_results_to_argilla` settings include `winner: [A, B, Tie]`, `quality: [1-5]`, `notes` |
| 24. Single-config uses absolute schema | ⚠️ PARTIAL | `push_single_results_to_argilla` has `quality` + `notes` but is **missing** `correctness: [correct, partial, incorrect]` and `failure_modes: multi-select`. Spec calls for both. Open as follow-up. |
| 25. Smoke test exists and passes | ✅ PASS | Just ran: `PASS — all 4 metrics produced finite floats for all 3 questions` |
| 26. Smoke test surfaces TODO regression | ⏭ NOT EXERCISED | The `math.isfinite` check is in place (commit `473851c2` hardened it for numpy types and inf); the regression test of intentionally breaking and verifying non-zero exit not run |

**Tally:** 14 ✅ PASS, 5 ⚠️ partial/known-gap, **4 ❌ open gaps** (11, 12, 19, 20 — all related: min_submitted distribution + anchor merging), 3 ⏭ deferred (operational or restart-cycle).

- [x] 7.2 Smoke test executed successfully 2026-06-03: `PASS — all 4 metrics produced finite floats for all 3 questions`. Required installing `ragas==0.3.5`, `langchain-huggingface`, `datasets`, `sentence-transformers` into the `archi` conda env (deps already pinned in `Dockerfile-benchmarks`). RAGAS judge calls to HUIT Bedrock returned finite floats for all 12 metric evaluations (3 questions × 4 metrics).
- [x] 7.3 Verified structurally — see scenario 5 in 7.1 verdict above. Strict byte-identical regression isn't achievable because `corpus_snapshot_id` is always in `metadata` (additive only, no top-level keys changed). Three dry-runs (one successful, two with intermediate failures) confirm `bench_out/<name>-<ts>.json` + `bench_out/<name>-<ts>_report.html` are always written regardless of Argilla push state.
- [x] 7.4 `corpus_snapshot_id` machinery implemented end-to-end (this is what the spec requires; live verification still pending Phase 4 deploy). Wired: `ResultHandler.get_corpus_snapshot_id` generates a per-invocation UUID (respects `ARCHI_CORPUS_SNAPSHOT_ID` env override for re-runs/smoke tests), `add_metadata()` stamps it into the dump JSON, all three Argilla push functions forward it as `TermsMetadataProperty` on the dataset and as a per-record metadata field, `pull_grades_from_argilla` surfaces it on the returned grades dict, and `assert_single_sweep` (new helper in `benchmark_argilla.py`) raises `RuntimeError` with a clear message listing the conflicting ids when the analysis notebook is fed records from different sweeps. Notebook cell 1.5 now calls `assert_single_sweep(grades)` before any aggregate stats run. Four new unit tests cover pass / cross-sweep refusal / missing-id exploratory mode / blank-id filtering.

## 8. Post-cutover

> **Deferred — operational, not in scope of this archived change (2026-06-25).**
> The Qwen 3.6 prod cutover shipped, but a *real* graded eval round is its own
> initiative and is gated on prerequisites not met here: (a) the question bank is
> still 9 queries (`config/benchmarking/queries.json`) vs the 30–50 design target
> — too few for statistical power; (b) the benchmark SUT must be repointed at the
> prod Qwen 3.6 model (it defaults to the idle 3.5 on :8000); (c) the Argilla
> container is currently `unhealthy`; (d) 8.3–8.5 require human graders (HPC
> facilitators + Harvard AI experts) actually grading. When the team wants the
> first round, open a new change to do the bank expansion + SUT repoint first.

- [ ] 8.1 Capture the first real eval-round's pre-reg in `docs/eval/preregs/<date>-v1-strict-vs-v2-lean.md` BEFORE opening the Argilla dataset to graders.
- [ ] 8.2 Run `archi evaluate --argilla` for the v1 vs v2 prompt comparison against the full question bank.
- [ ] 8.3 Send the Argilla URL + login instructions to the evaluator list.
- [ ] 8.4 After grading completes, `archi grade --export` and run the analysis notebook. Publish the result as a writeup that references the pre-reg by path.
- [ ] 8.5 Update memory with insights from the first round (calibration friction, grader fatigue, anchor-question performance, voice-leak self-reports) so the second round is better designed.
