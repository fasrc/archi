# Release Plan 2026

**Author:** Austin Swinney, FASRC — Harvard University
**Date:** August 2026
**Status:** Adopted (milestones live on the tracker)
**Baseline:** `origin/dev` @ `52c102f6`, 2026-08-18 — 56 open issues, 3 open PRs, `main` 261 commits behind `dev`

---

## TL;DR

This fork has never cut its own release: `main` last moved 2026-06-24, there are zero
GitHub releases, and the newest tag (v2.3.0) is inherited from upstream. Meanwhile 56
open issues carry no scheduling signal beyond P2/P3 labels.

This plan turns the backlog into four **feature-anchored releases**. An issue enters a
release only if that release's feature is **broken, wrong, or dishonest without it** —
each gate carries measured evidence. Everything else is explicitly **parked** (labeled
`parked`, reasons below) so we push for the releases we need and decline sprawl.

Result: **17 issues gate the four releases. 4 were closed as already fixed or
superseded (with evidence). 35 are parked.** Anything impacting end-user chat
experience outranks track membership and rides the earliest feasible release.

One further state exists since the 2026-08-19 amendment: **`evidence-trial`** —
operator-driven trial work, milestone-exempt while the trial runs (see "The
invariant" below). Such issues are neither gating nor parked; do not park them.

---

## Versioning

**CalVer git tags: `v2026.MM.N`** (month of release, N for same-month follow-ups).

- Upstream (`archi-physics/archi`) owns the semver line — its v2.4.0/v2.5.0 tags are
  already in every clone that fetches upstream, so continuing semver collides
  immediately. CalVer structurally cannot collide and is Docker-tag safe.
- `pyproject.toml` `version` is bumped to the PEP 440 form (`2026.8.0`) in the release
  PR. It currently says `1.2.4` and has never tracked reality.
- Upstream's 77 unmerged commits (v2.3.0→v2.5.0): **cherry-pick only**, no wholesale
  sync release. Nominate candidates from `git log --oneline v2.3.0..v2.5.0` on the
  upstream remote; port a commit when it fixes something we ship, decline the rest.
- **Evidence trials** are the one other upstream-intake path (amendment, 2026-08-19).
  An operator may port a **pinned upstream branch snapshot** (exact SHA recorded in
  every port commit) as a targeted, hunk-classified port, to trial a capability
  before any adoption decision. The trial PR merges only after the trial passes
  from the PR branch **and** a human records the adopt decision on the trial's
  tracking issue; a rejected trial merges nothing. The trial record on the tracking
  issue MUST name the **tested PR-head SHA and the tested base (`origin/dev`) SHA**;
  if either differs at merge time (review fixes, hunk adjustments, or the base
  advancing under a clean merge), the trial reruns before merge — and the rerun
  head MUST contain the current base (`git merge-base --is-ancestor <base>
  <head>`: merge or rebase the base into the branch first), because rerunning an
  unchanged head against a newly recorded base never tests the pair the merge
  will produce. Stale evidence never merges, and head-only tracking is not enough
  because a non-conflicting base change can still alter runtime behavior. The
  trial PR stays a **draft** until the adoption record completes: the hourly
  PR-readiness reconciler advertises any non-draft, clean, green PR as
  `ready-to-merge`, and draft status is the one input its predicate already
  respects — so an unadopted trial can never be advertised as mergeable. Adoption itself still enters a milestone through the
  normal gate bar — the trial produces the evidence, never the schedule. Wholesale
  copies of files that exist on the fork stay forbidden; every file in the
  candidate diff needs a recorded disposition.

**Release mechanics** (per release):

1. All milestone issues closed; `bash scripts/gate.sh` green on `dev`.
2. PR `dev` → `main` (CI compares release PRs vs `origin/dev` with coverage advisory —
   see `scripts/gate.sh` preamble).
3. Dispatch `.github/workflows/test-and-build-tag.yml` with the ref (`main`) and the
   tag name — it builds/publishes images and creates the tag.
4. Create the GitHub release from the tag; notes = the milestone's closed issues.

---

## Method

- **Feature-anchored:** each release exists to ship one feature the team is pushing.
  Selected drivers: benchmark integrity, retrieval quality, agent robustness (folded
  into production readiness after classification), production hardening.
  Explicitly **not** drivers: headless `/v1` external frontends, incremental re-ingest.
- **Gate bar — broken-without-it:** an issue is in a milestone only if the release's
  feature is broken/wrong/dishonest without it, evidenced by a file:line, a measured
  number, or a repro from the issue itself.
- **UX overrides:** anything an end chat user experiences (leaked reasoning, wrong
  citations, overflow apologies, silently stale knowledge base) is highest priority
  regardless of track.
- All 56 open issue bodies were read and classified against `origin/dev` @ `52c102f6`;
  stale claims were re-verified against merged PRs before scheduling.

---

## v2026.08.0 — First cut (feature: 8 months of dev ships; chat stops leaking)

Target: end of August. The release that ends the drought — plus the four live UX bugs,
which ride per the UX-first rule.

| Gate | Why it gates | Size |
|---|---|---|
| PR [#251](https://github.com/fasrc/archi/pull/251) merge | done, open — closes #246 (dead duckdb pin + guard) | — |
| PR [#230](https://github.com/fasrc/archi/pull/230) merge | done, open — closes #181 (sitemap lastmod refresh) | — |
| PR [#261](https://github.com/fasrc/archi/pull/261) merge | done, open — docs for the second model server | — |
| [#266](https://github.com/fasrc/archi/issues/266) clean-host image build | All 15 service Dockerfiles pull `docker.io/a2rchi/a2rchi-python-base:latest` — upstream's published image, Python 3.10.20 — and `archi create` never builds the fork's own 3.11 base, so on a clean host `pip install .` fails `requires-python >=3.11` (reproduced in the issue). The in-repo base Dockerfiles are already 3.11; the defect is the pulled tag. Release builds must not depend on CI's pre-build side step | M |
| [#122](https://github.com/fasrc/archi/issues/122) think-leak (UX) | Chain-of-thought still streams to users before an orphan `</think>` arrives — live at `base_react.py:600/:911`, visible in the native UI and `/v1` | M |
| [#245](https://github.com/fasrc/archi/issues/245) `/v1` double source list (UX) | `openai_compat.py:420` appends a second, contradictory source list; the native-UI half was already fixed by PR #240, shrinking this to S | S |
| [#262](https://github.com/fasrc/archi/issues/262) model switch drops context bound (UX) | On fasrc-dev, a dropdown model switch installs no in-loop bound at all — the overflow apology #235 just fixed comes back | S |
| [#277](https://github.com/fasrc/archi/issues/277) sitemap failure halts scheduled crawl (UX) | Goes live the moment PR #230 merges: one failed sitemap silently stops the whole scheduled crawl, so answers rot on a stale corpus | M |
| [#339](https://github.com/fasrc/archi/issues/339) release workflow's base-image retarget is a no-op | `test-and-build-tag.yml:154` passes no `--orig-tag`, so the script's `latest` default skips every template — the 15 service templates have carried `:dev-4314ac4` since `5e168b00`. Measured with the workflow's exact argv: the release argv rewrites **0 of 15**, the PR-preview argv (`--orig-tag all`) rewrites 15 of 15. So the release smoke test builds `FROM` the in-tree dev pin rather than the versioned bases `build-images` just pushed, and "Commit Dockerfile base image updates" finds an empty diff and exits 0 — the step this plan and `CLAUDE.md` both describe as pushing that update. The release act is dishonest about what it validated and what it tagged | S |
| [#340](https://github.com/fasrc/archi/issues/340) runtime-validate the #122 thinking-gate on fasrc-dev (UX) | Carries task group 5 of the merged #122 plan verbatim. The #122 unit tests drive a fake agent; only a real OpenAI-compatible endpoint with a Qwen-style template emits the inline `reasoning ... </think>` stream the fix targets, and the PR-preview stack's Ollama reports through `reasoning_content` and never reaches the fixed branch. "Chat stops leaking" is unvalidated on the release's own terms until this runs. Operator-driven (`needs-deploy`): two fasrc-dev redeploys, the second restoring `enable_thinking: false` | S |

## v2026.09.0 — Benchmark integrity (feature: numbers you can cite)

Target: end of September. Must precede retrieval quality — it is the evidence rig for
any "answers improved" claim.

| Gate | Why it gates | Size |
|---|---|---|
| [#269](https://github.com/fasrc/archi/issues/269) record true config/code version per result | An 8192-context arm recorded `context_window: 32768`; 6 runs share one `last_commit` — no number is attributable to its arm | M |
| [#279](https://github.com/fasrc/archi/issues/279) valid JSON artifacts, honest denominators | 10 of 18 committed artifacts are rejected by a strict JSON parser (bare `NaN`); a "109 of 109 scored" report had only 108 finite values | M |
| [#213](https://github.com/fasrc/archi/issues/213) drift tripwire actually checks rows | Nightly drift reports "0 checked / 105 skipped" — 0 of 105 goldenset rows carry `source_hashes`, so the bank's gold answers are unverified | L |
| [#119](https://github.com/fasrc/archi/issues/119) warm NLTK before the thread pool (UX too) | 8 files randomly fail per re-ingest (thread race) — a nondeterministic corpus corrupts arm comparisons **and** silently makes topics unanswerable | S |
| [#347](https://github.com/fasrc/archi/issues/347) literal `</think>` in an answer truncates it (UX) | Enters by the UX override, not this track. `base_react.py:287-296` keys the orphan-tag rule on literal characters, so an answer that quotes the tag is truncated to what follows it — on every provider, thinking on or off, before and after the #122 gate; pinned by `test_a_literal_closing_tag_in_an_answer_truncates_as_it_did_before`. Not `v2026.08.0`: the fix needs the provider's `reasoning_content` or a first-tag-only rule, an open design question, so this is the earliest **feasible** release | M |
| [#378](https://github.com/fasrc/archi/issues/378) benchmark aborts a healthy ingest at an absolute deadline | `wait_for_ingestion_completion` (`service_benchmark.py:2116`) holds one absolute `BENCH_INGEST_WAIT_TIMEOUT` (default 7200s) that never resets on healthy progress, then reports the failure as a connection error against a URL it never used. A rig that kills advancing ingests on the clock, and misattributes why, yields neither numbers nor a diagnosis | M |
| [#394](https://github.com/fasrc/archi/issues/394) run the base-image preflight before `archi evaluate --force` tears the runtime down | `base_image_preflight.py` exists for one ordering guarantee — refuse before the destructive step — and that guarantee holds on the `create` path only. `archi evaluate --force` calls `remove_existing_deployment()` first, so a benchmarking run destroys an existing runtime and only then fails on a base image the preflight would have refused | M |

## v2026.10.0 — Retrieval quality (feature: measurably better answers)

Target: end of October. Depends on the v2026.09.0 rig.

| Gate | Why it gates | Size |
|---|---|---|
| [#216](https://github.com/fasrc/archi/issues/216) embedding A/B (MiniLM vs gte/nomic) | The feature itself. Measured defect: 68% of chunks (4,684/6,854) exceed MiniLM's 256-token window and are silently truncated today | L |
| [#215](https://github.com/fasrc/archi/issues/215) GPU TEI embedding | Named enabler in #216: 40 min → ~1 min re-embeds make the A/B (and adopting a winner) feasible | M |
| [#396](https://github.com/fasrc/archi/issues/396) feature-matrix benchmark campaign | "Measurably better answers" is this release's feature, and #216 measures one axis of it. Every other retrieval and ingest toggle ships on an inherited default with no measurement behind it: `hierarchical_rerank` is default-on carrying ADR 0003's "+19% RAGAS" from the pre-sentence-chunking #32 A/B, and `categorization` is code-default `false` (`processing.py:689`) but `true` in the shipped example config (`config.example.yaml:150`) at one LLM call per document. Claiming measured improvement while most of the retrieval config is set by unmeasured default is dishonest. Adds the Time-to-Ingest metric the rig discards today (`service_benchmark.py:2178`) | L |

Parked from this track: #130 (rerank already default-on with +19% RAGAS shipped;
residual is below judge noise and awaits a human data-egress decision), #241 (the
similarity floor is disabled by default — latent).

## v2026.11.0 — Production readiness (feature: safe to run in prod; archi doesn't fall over)

Target: end of November. Absorbs the agent-robustness remainder — after
classification, only one live robustness gate was left, too thin for its own release.

| Gate | Why it gates | Size |
|---|---|---|
| [#81](https://github.com/fasrc/archi/issues/81) the four non-negotiables: auth/SSO + admin lock, real WSGI server + debug off, LLM failover + tested DB restore, automated eval gate | `service_chat.py:47-48` runs Flask with `debug=True` and auth off in the prod path — a prod deployment today is indefensible | L |
| [#139](https://github.com/fasrc/archi/issues/139) remaining PR3: honest overflow message, non-explosive retry, failed-run traces (UX) | An 85,316-token turn drew a vLLM 400; the retry re-overflowed at 85,408; users see "conversation history has grown too large" on turn 1 | M |
| [#260](https://github.com/fasrc/archi/issues/260) clamp `api_catalog_document` `max_chars` | `uploader_app/app.py:762-770` honours an unbounded value; `0` returns the whole document — enters via #81's checklist | S |
| [#143](https://github.com/fasrc/archi/issues/143) SSRF/DNS-rebinding hardening on sitemap fetch | Hostname is validated but never resolved; redirects unchecked — enters via #81's checklist | M |
| [#286](https://github.com/fasrc/archi/issues/286) version-control the FASRC vLLM launchers and provision them at a verified pin | Nine launcher/patch/systemd files exist only on `archi.rc.fas.harvard.edu` and are committed nowhere. Two were found missing from disk in August 2026 while `vllm-qwen36.service` was still active — `Restart=on-failure` or a reboot would have left production down with no way to start it, and both had to be rebuilt by hand. Calling a release production-ready while the model servers cannot be restored from version control is dishonest | L |
| [#293](https://github.com/fasrc/archi/issues/293) validate port configuration before `archi create --force` tears the deployment down | `_check_ports_available()` runs inside `prepare_deployment_files()`, after the teardown #287 moved, so a nonnumeric, out-of-range or duplicated port destroys a working deployment and then fails on config that was knowable in advance. `--dry --force` returns before the check and reports success on a config the real run refuses | M |
| [#319](https://github.com/fasrc/archi/issues/319) close the host-mode falsy `external_port` preflight hole neither #310 nor #311 closes alone | #293's shipped validation is wrong without it: host mode with `external_port: 0` passes preflight and renders `port: 0` — measured on `origin/dev` `2c404822` and on both merged fix heads (PR #316 `2776f1de`, PR #317 `b1f85d98`); the remaining work is the merge-resolution of the two halves plus a regression test | S |
| [#294](https://github.com/fasrc/archi/issues/294) render the replacement deployment before destroying the existing one | #293 closes the port route; `mkdir`, `write_secrets_to_files`, `create_required_volumes`, the nine stages of `prepare_deployment_files` and `start_deployment` all still run after the teardown, and most can raise on deterministic config input. Three review rounds on #292 found four such routes by inspection with no argument the list was complete — the per-route fix does not close the class | L |
| [#320](https://github.com/fasrc/archi/issues/320) activate the QA evaluation console safely | Milestoned 2026-08-23 out of the #304 adopt decision, never rowed here. #81's fourth non-negotiable is an automated eval gate and the console is its operator surface, so activating it needs SSO/RBAC on the routes, a redacted `agent_config.resolved.yaml` snapshot, and VIEW-role responses carrying no oracle truth or gold atoms. PR #352 delivered only the fail-closed storage guard (#328) | L |
| [#335](https://github.com/fasrc/archi/issues/335) pin the 15 service Dockerfile templates to ghcr digests | Milestoned 2026-08-24 out of #333, never rowed here. The templates carry mutable tags (`ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4`, verified on `origin/dev` `5a26b5a3`), so a rebuilt-and-repushed tag silently changes what a release builds `FROM`. Calling a build reproducible on a mutable tag is dishonest | M |
| [#326](https://github.com/fasrc/archi/issues/326) crash-safe QA-eval continue/overwrite | Gates #320: the console always continues with `overwrite=True` (`worker.py:113`). `workflow.py:352-380` and `:796-803` write a pruned `manifest.json` under a still-terminal status that `RunManifest.from_dict` refuses, and `:414` unlinks the paused run's only staged `live_checks.jsonl`. The invalid window spans the whole scoring phase, so a redeploy or an OOM leaves the workspace and its paid-for LLM artifacts unrecoverable without hand-editing JSON | M |
| [#327](https://github.com/fasrc/archi/issues/327) retry verifier accepts what scoring produces | Gates #320: retry is a console feature. `_iter_terminal_plan` (`workflow.py:704-740`) stamps `live_validation_failed` over `execution_failed` attempts too, while `RetryParentStore._load` (`workspace.py:253-258`) demands each pair with `answer_ready` — so a cleanly `scored` run returns 400 forever from console and CLI alike, on an uncorrupted workspace, in the two cases retry exists for | S |
| [#330](https://github.com/fasrc/archi/issues/330) evaluations `agent_config_path` default the seam accepts | Gates #320's enable-on-dev step by that issue's own terms. `base-config.yaml:124` renders exactly `LIVE_AGENT_CONFIG_PATH` (`evaluation_console.py:33`) — the one value `build_evaluation_service` refuses by file identity — so no rendered default ever works, and `configuration.md:171,183-185` presents the refused path as the working example. The console silently never appears | S |
| [#360](https://github.com/fasrc/archi/issues/360) cut the release tag from the tree the smoke deployment validated | `test-and-build-tag.yml` resolves the dispatched **branch name** independently in `build-images` (`:35-38`), `smoke-test` (`:103-106`) and `release` (`:221-224`) — three `actions/checkout` calls, no shared SHA. A push landing between them tags code no job in the run ever exercised. A release that cannot name the tree it validated is dishonest about its own tag | M |
| [#372](https://github.com/fasrc/archi/issues/372) preserve the evaluation catalog across `archi create --force` | The chatbot mounts `./data/evaluations` (`base-compose.yaml:255`) and the redeploy path destroys it, taking datasets, human atom approvals, job records and run history with it. Verified P1 from the #368 review; #368 corrected the documentation and left the destructive path live. Losing paid-for human review on a routine redeploy is the opposite of production-ready | M |
| [#371](https://github.com/fasrc/archi/issues/371) guarantee the evaluations `agent_config_path` exists in the container at create time | Gates #320 alongside #326/#327/#330. An accepted `agent_config_path` is validated on the host and never proven present inside the chatbot container, so an enabled console registers and then fails every run on a missing file. #81's fourth non-negotiable is an automated eval gate, and this is its operator surface | M |
| [#389](https://github.com/fasrc/archi/issues/389) refuse a deployment whose service templates pin the same base at different references | `base_reference` (`base_image_preflight.py:123-135`) returns the **first** matching reference and stops, so two templates naming one `a2rchi-*-base` at different refs let the preflight probe whichever sorts first and report the deployment ready — before `archi create --force` tears anything down. PR #391 is open, not merged: the guard is not on `dev` | M |

---

## Closed during planning (already fixed or superseded — evidence on each issue)

| Issue | Evidence |
|---|---|
| [#244](https://github.com/fasrc/archi/issues/244), [#239](https://github.com/fasrc/archi/issues/239) | Fixed by merged PR #240 (commit `80765cb4`): `postgres_vectorstore.py:411` normalizes `score = 1.0 - distance` for every metric, with in-code monotonicity proof |
| [#148](https://github.com/fasrc/archi/issues/148) | `goldenset-report.timer` active on the fasrc-dev host, firing 06:15 daily (verified via `systemctl --user list-timers`) |
| [#63](https://github.com/fasrc/archi/issues/63) | Core deliverable shipped as `archi sources build` (PR #37, `cli_main.py:909`); the issue self-describes as superseded |

## Parked — 61 issues, labeled `parked`

Query: `gh issue list --repo fasrc/archi --label parked`. An issue leaves the parking
lot by gating a future feature release, not by aging.

| Issue | One-line reason |
|---|---|
| #280 | Headless `/v1` strip-down scoping — track is not a release driver; go/no-go unmade |
| #369 | `validate_evaluations_root` raises a bare `AttributeError` instead of its `ValueError` on a non-dict block — a worse refusal message, not a missed refusal |
| #373 | Re-pin released Dockerfiles to digests rather than the CalVer tag; #370 shipped the narrower remedy, so this is the better alternative to a closed gap, not an open one |
| #374 | `agent_config_path` interpolated raw inside quotes in `base-config.yaml`; latent behind a path containing quote characters, and the sibling key was already fixed by #368 |
| #375 | Three base-image pin-guard gaps, including an exact-digest assertion covering 2 templates of 15, plus a spec contradiction. The guards hold for what the repo ships today |
| #382 | Preflight over-promises coverage for a base outside its placeable set, or one left behind by a final stage — the issue body states it is not a live defect |
| #383 | `service_templates` misses nested Dockerfiles; the issue body states it is a gap in the guarantee, not a current fault, and the two nested files today are correctly excluded |
| #390 | The release rewriter's `verify_base_tags`/`update_base_tags` are not recursive; an enabler for removing the nested-template guard, which is still in place |
| #392 | Six Dockerfile-parser divergences from Docker, all failing open — latent on syntax (heredocs opened on a continuation) that no template in the repo uses |
| #393 | One instance of #392's class: a continuation dropped across an empty physical line. Same latency, and the class is the right unit to fix |
| #395 | The divergent-pin guard compares the first `FROM` rather than the shipping stage — cites `_base_reference_sources`, which exists only on the unmerged PR #391 |
| #156, #157 | Incremental re-ingest PR-2/PR-3 — track not a driver; both blocked on an unmerged PR-1 enabler |
| #276 | lastmod hand-list vs sitemap ownership — metadata-only, both outcomes small and opposite |
| #166 | CRUD for source list — exploration; deliverable is a note, must not change behavior |
| #278 | duckdb-guard completeness gap — the guard is already a net improvement with zero live failures |
| #250 | Template-mismatch check misses `migrations/` — issue itself states developer-facing only, no runtime risk |
| #255, #234, #231 | Merge-hygiene reconciler/branch-protection — settings or nearest-miss issues; gate neither chat nor the release build |
| #224 | Loop-container toolchain preflight — nightly harness, outside `src/`, needs a human session |
| #200 | Network-guard classifier rot — protects smoke-suite skip logic, not shipped behavior |
| #243 | Smoke fixture schema drift — `tests/smoke/` is not run by the gate; behavior already unit-pinned |
| #275 | Nightly executor stale task pointer — loop-harness bug, no product surface |
| #190, #115 | Docs accuracy work — docs-only; #190 also collides with any future `app.py` extraction |
| #88 | Layered config design — design-first; the port-drift symptom is already fixed |
| #271, #212, #257 | Benchmark supports — rig already refuses to overclaim (`arms_comparable`), digest noise, CI-Postgres test infra |
| #258, #227 | hybrid_search fallback correlation ID — observability duplicate pair, diagnosability not correctness |
| #263 | Real tokenizer for the in-loop bound — 25% margin already covers the measured p99 (1.35x vs 1.42x) |
| #264 | Non-text tool-result blocks bypass ceiling — unreachable: no multimodal MCP tools enabled |
| #267 | Tool-budget race — measured 0 lost updates in 2000 trials at default switch interval |
| #204, #193 | client_timeout telemetry columns — no shipped consumer reads them; `/v1` timestamps land in 1970 with zero user effect |
| #60, #62 | Argilla loop / ServiceNow eval-bank explorations — headline defect already fixed (#226); PII boundary decision unresolved |
| #61 | Sources sidebar / hover-highlight — feature exploration, not a defect |
| #206 | Upstream bind-order report — FASRC side already fixed and merged; remaining work is upstream citizenship |
| #130 | Bedrock reranker — rerank already default-on (+19% RAGAS); residual below judge noise, awaits egress decision |
| #241 | Citation-floor semantics on non-cosine scales — latent while the shipped default disables the floor |
| #285 | Non-branch ref in `test-and-build-tag.yml` — real defect on the advertised commit-SHA input, but the release mechanics above prescribe dispatching `main`, and that path publishes and tags cleanly |
| #288 | Keep `fasrc_archi.md` truthful as its dependencies land — docs-only, same disposition as #190/#115 |
| #309 | Nightly loop halts on a phantom CI failure (queued check read as failed) — loop-harness observability, no product surface; parked 2026-08-20 but never rowed here |
| #300 | `show_service_urls` port-walk consolidation — divergence is success-banner-only, operator-cosmetic; the validated path already uses `extract_port_config` |
| #312 | Pre-teardown port availability probe — robustness beyond #293's recorded decision D3; the shipped probe still refuses the create, one teardown later than ideal |
| #313 | `.gitignore` `*secrets*` masks `secrets_manager.py` from CI's black walk — gate hygiene; the drifted instance was fixed by #308, the structural hole gates no feature |
| #314 | Dead model-name loop in `_get_model_based_secrets` — `get_models_configs()` returns a constant `[]`; latent trap, no wrong runtime behavior today |
| #338 | Pin GitHub Actions references to commit SHAs — supply-chain hygiene across workflows; parked 2026-08-24, never rowed here |
| #331 | Custom `evaluations.root` escapes the fixed compose mount — latent trap behind a knob no deployment sets; the default root deploys correctly, and it needs the console active (#320) |
| #332 | pr-preview base-image detection diffs against `main` — CI-only: wasted preview minutes and a smoke that validates a throwaway `pr-<n>` tag; the release-workflow twin is #339, scheduled |
| #344 | Provider-scoped `context_windows` keys — config-surface decision with won't-fix on the table; no deployment has two providers sharing a model id, and it is not a regression against pre-#262 |
| #345 | A scheduled crawl un-deletes an operator's delete — the primary reading is undecided (transient-by-design vs defect); the narrow mid-crawl race is real under both but unmeasured |
| #348 | Held reasoning reaches no surface on an early stream error — latent: needs `enable_thinking: true`, which no deployment sets; what the panel claims about unclassifiable content is undecided |
| #350 | `stream()`/`astream()` duplicate the reasoning-phase invariant — explicitly not a defect; maintainability on code inert at `enable_thinking: false` |
| #351 | Nightly loop runs the wrong task list — loop-harness control plane, no product surface (cf. #275, #309) |
| #353 | Nightly loop cannot push or open a PR (the ambient PAT beats the OAuth token) — loop-harness credential plumbing, no product surface (cf. #322) |
| #355 | No way to clear an inherited `context_windows` entry — declined as YAGNI on PR #343; clearing installs *no* bound, a worse posture than the override workaround |
| #356 | Storage validation runs after the stale-job sweep — the sweep's write is accurate, not corrupting, and probing before construction cannot work as-is |
| #357 | No recovery for a run whose worker outlived the chat app — recovery is a feature with its own contract; rerun is the supported answer and the console is inactive |

---

## Accounting

**At baseline (2026-08-18):** 17 milestone-assigned + 4 closed + 35 parked = 56 open
issues. ✓ Milestones `v2026.08.0`–`v2026.11.0` live with due dates; open-item counts
10 (7 issues + 3 PRs) / 4 / 2 / 4.

**Tracker state (2026-08-19):** 18 milestone-assigned + 39 parked = 57 open issues. ✓
Milestone open counts 5 / 4 / 2 / 7. #181 and #246 closed via PRs #230 and #251. The
seven issues filed 2026-08-18 — out of the #287 review and the #261 documentation
round — were triaged here: #286, #293 and #294 into `v2026.11.0`; #285, #288, #290 and
#291 parked.

**Tracker state (2026-09-01):** 24 milestone-assigned + 61 parked = 85 open
issues. ✓ Milestone open counts 1 / 6 / 4 / 13. Sixteen issues had drifted into
neither state — the largest single drift recorded here — and every one but #360
and #378 was a review follow-up filed by the automated rounds on PRs #367, #368,
#370, #380, #387 and #391. Six were scheduled: #378 and #394 into `v2026.09.0`,
#360, #371, #372 and #389 into `v2026.11.0`. Ten were parked, with reasons in
the table above. #396 was filed the same day and entered `v2026.10.0`.

Nine of the ten parks are the base-image preflight cluster, on the same ground
each time: the issue's own body states the defect is latent, or names the merged
PR that already took the narrower remedy. #392, #393 and #395 describe a single
class — a line-oriented Dockerfile parser that fails open — on syntax no
template in the repo uses; they park together and should be fixed as a class if
any one of them is ever scheduled. #395 additionally cites a symbol that exists
only on the unmerged PR #391, so it could not be checked against `dev` at all.
**Severity moved none of them:** #375 and #389 are both P2 base-image guard
defects, and only #389 is scheduled — because only #389 lets a deployment be
reported ready and then torn down.

#396 is the one gate here that is not a defect. It enters `v2026.10.0` because
that release's feature is measured improvement, and #216 measures one axis while
every other ingest and retrieval toggle rides an unmeasured default. Its own
first task is a power calculation: the August 2026 goldenset work established
that RAGAS mean deltas sit inside the run-to-run spread and need roughly 40 runs
per arm to carry a claim, while count-type metrics were decisive far cheaper
(overflow apology rows 11 → 0, p = 8.1e-5). An under-powered arm reports no
measurable difference rather than a direction. It is blocked by #279 and #213,
both `v2026.09.0`: invalid JSON artifacts with inflated scored counts, and an
unlocked bank drifting under a multi-day campaign, each turn a cross-arm
comparison into a confident wrong answer.

Two adversarial review rounds moved one of the seven. #294 was parked as design-first
and is now a gate: `remove_existing_deployment()` is still followed by `mkdir`,
`write_secrets_to_files`, `create_required_volumes`, the nine stages of
`prepare_deployment_files` and `start_deployment`, so #293 closes one route of the
class rather than the class. The rounds also argued for scheduling #285 and #290 and
then argued the reverse; both were re-checked against the code and stayed parked —
the prescribed `main` dispatch publishes and tags cleanly, and `corpus_fingerprint`
already makes a rebuilt corpus detectable. **A defect being real is not the bar; the
bar is whether that release's stated feature is broken, wrong, or dishonest without
it.**

**Tracker state (2026-08-23):** 17 milestone-assigned + 41 parked = 58 open issues. ✓
Milestone open counts 4 / 3 / 2 / 8. Six drifted issues (neither milestoned nor
parked) were reconciled: #319 into `v2026.11.0` — it completes #293's port-validation
feature, which measurably accepts a host-mode `external_port: 0` and renders
`port: 0` on `origin/dev` `2c404822` and both merged fix heads — and #300, #312,
#313, #314 and #322 parked (reasons in the table above). #269 was closed as
delivered: PR #272 (`48fb4f99`) records `running_configuration` with an asserted
divergence list, and PR #270 (`9e899848`) records `code_version` computed from the
running code (`src/bin/service_benchmark.py:345-430,460`). Rows for #253, #254, #290
and #291 — parked issues closed between 2026-08-19 and this entry — were pruned from
the parked table, and a missing row was added for #309 (parked 2026-08-20, never
rowed), so the table matches its own query.

**Tracker state (2026-08-25):** 19 milestone-assigned + 52 parked = 71 open issues. ✓
Milestone open counts 2 / 4 / 2 / 11. Seventeen drifted issues were reconciled — the
largest drift yet, and all seventeen were review follow-ups and nightly-run findings
filed between 2026-08-24 and 2026-08-25.

**Six scheduled.** #339 and #340 into `v2026.08.0`, whose five original gates are all
closed — these two are the only open items between `dev` and the first cut. #339 because
the release workflow's retarget call rewrites 0 of 15 service templates, measured with
that workflow's exact argv, so the release smoke-tests a base it does not ship and the
Dockerfile-update push both this plan and `CLAUDE.md` describe has been silent since
`5e168b00`; the release act is dishonest about what it validated and what it tagged.
#340 because it carries task group 5 of the merged #122 plan verbatim and the leak fix
has never run against a real reasoning endpoint — the unit tests drive a fake agent, and
the PR-preview stack's Ollama never reaches the fixed branch. #347 into `v2026.09.0` by
the UX override rather than the benchmark track: a literal `</think>` inside a genuine
answer truncates it on every provider, live today, but the fix needs `reasoning_content`
or a first-tag-only rule — an open design question — so August is not feasible and
September is the earliest that is. #326, #327 and #330 into `v2026.11.0`, each gating
#320's safe console activation on its own file:line evidence: a manifest no validator
accepts, a retry path that refuses healthy workspaces, and a rendered default the runtime
seam always refuses.

**Eleven parked** (reasons in the table above): #331, #332, #344, #345, #348, #350, #351,
#353, #355, #356, #357. Three recurring grounds, none of them severity: latent behind a
knob or a config no deployment sets (#331, #344, #348, #355 — and #348 in particular is
the twin of scheduled #347, separated only by needing `enable_thinking: true`); loop-harness
and CI paths with no product surface (#332, #351, #353 — #332 is likewise the twin of
scheduled #339, separated by acting on a preview tag rather than on the release tag); and
issues whose own bodies record the answer as a decision rather than a defect (#350, #356,
#357). Several are high-severity — #331 is silent data loss and #351 can open a PR whose
diff belongs to a different change — but a high-severity defect on a path the release does
not take is parked.

**#319 closed as delivered.** `_resolve_ports_from_config`
(`templates_manager.py:243-256`) now derives the host-mode port with `external is not
None`, matching the render side at `:1128-1130`, so `external_port: 0` reaches
`_normalize_port` and is refused at `:192-195` before the teardown. Delivered by PR #316
(`b79a5a8a`) and PR #317 (`6aafd9f0`) and pinned by
`test_validate_port_config_host_mode_falsy_external_port_raises`
(`tests/unit/test_templates_port_checks.py:599`), verified green on `origin/dev`
`5a26b5a3`.

**Table repairs**, so each table matches its own query: rows added for #320 and #335
(milestoned 2026-08-23 and 2026-08-24, never rowed) and for #338 (parked 2026-08-24,
never rowed); #322's parked row pruned after that issue closed.

The eight other scheduled issues were re-read against `origin/dev` `5a26b5a3` and every
one stays. `service_chat.py` still runs Flask with `debug=True` (#81). `base_react.py:2036`
still tells the user the conversation history has grown too large — the exact message
#139's acceptance criteria forbid. The sitemap fetcher's own docstring still defers
DNS-resolve and connection pinning to v2/H1, which is #143's whole scope. 10 of 18
committed artifacts are still rejected by a strict JSON parser (#279). The teardown at
`cli_main.py:294` is still followed by `write_secrets_to_files`,
`create_required_volumes`, `prepare_deployment_files` and `start_deployment` (#294). No
TEI service exists and the embedding default is still MiniLM (#215, #216). PR #352
delivered only the fail-closed storage guard, not #320's RBAC and redaction work. The
file-overlap screen flagged all eight as candidates; reading the code cleared them, which
is the screen working as designed — it narrows, it does not decide. **A defect being real
is not the bar; the bar is whether that release's stated feature is broken, wrong, or
dishonest without it.**

**The invariant:** every open issue carries exactly one of {a milestone, the `parked`
label, the `evidence-trial` label}, so
`milestone-assigned + parked + evidence-trial == open`. Anything in none of these
states is a scheduling decision nobody has made, and it is invisible to every report
that reads the milestones. Re-check this section against the tracker on every
classification transition — an issue scheduled, parked, or an evidence-trial opened
or closed.

**`evidence-trial`** (amendment, 2026-08-19) marks operator-initiated evidence work —
currently upstream capability trials (see "Evidence trials" under Versioning). These
issues are milestone-exempt while the trial runs, and nightly automation must never
schedule, triage, or drain them — like `parked`, but with active operator-driven
work. **The label stays on the issue until the issue closes**, so the invariant
holds through the decision-to-merge interval. Adopt → **in this order**: first file
the adoption's follow-on work into a milestone through the normal gate bar, and the
adoption record **names the release the capability ships in** — on this single
trunk, code merged to `dev` rides the next release regardless of its milestone, so
either that named release is the next one, or the capability must be **dark**
(off-by-default toggle, no user-visible surface) in every release before it; a
capability that cannot ship dark waits unmerged for its release. Then mark the
draft trial PR ready and merge it, then close the issue; the label stays until
closure. Reject → **close the trial PR first, then the issue** (a green open PR
must never outlive its rejection), with the writeup as the record.

Mirrored to Asana: project *p-Search-Engine-LLM* › Milestones, one task per release
with gating issues as subtasks.
