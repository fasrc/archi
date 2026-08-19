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
  advancing under a clean merge), the trial reruns on the current pair before
  merge — stale evidence never merges, and head-only tracking is not enough
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

## v2026.09.0 — Benchmark integrity (feature: numbers you can cite)

Target: end of September. Must precede retrieval quality — it is the evidence rig for
any "answers improved" claim.

| Gate | Why it gates | Size |
|---|---|---|
| [#269](https://github.com/fasrc/archi/issues/269) record true config/code version per result | An 8192-context arm recorded `context_window: 32768`; 6 runs share one `last_commit` — no number is attributable to its arm | M |
| [#279](https://github.com/fasrc/archi/issues/279) valid JSON artifacts, honest denominators | 10 of 18 committed artifacts are rejected by a strict JSON parser (bare `NaN`); a "109 of 109 scored" report had only 108 finite values | M |
| [#213](https://github.com/fasrc/archi/issues/213) drift tripwire actually checks rows | Nightly drift reports "0 checked / 105 skipped" — 0 of 105 goldenset rows carry `source_hashes`, so the bank's gold answers are unverified | L |
| [#119](https://github.com/fasrc/archi/issues/119) warm NLTK before the thread pool (UX too) | 8 files randomly fail per re-ingest (thread race) — a nondeterministic corpus corrupts arm comparisons **and** silently makes topics unanswerable | S |

## v2026.10.0 — Retrieval quality (feature: measurably better answers)

Target: end of October. Depends on the v2026.09.0 rig.

| Gate | Why it gates | Size |
|---|---|---|
| [#216](https://github.com/fasrc/archi/issues/216) embedding A/B (MiniLM vs gte/nomic) | The feature itself. Measured defect: 68% of chunks (4,684/6,854) exceed MiniLM's 256-token window and are silently truncated today | L |
| [#215](https://github.com/fasrc/archi/issues/215) GPU TEI embedding | Named enabler in #216: 40 min → ~1 min re-embeds make the A/B (and adopting a winner) feasible | M |

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
| [#294](https://github.com/fasrc/archi/issues/294) render the replacement deployment before destroying the existing one | #293 closes the port route; `mkdir`, `write_secrets_to_files`, `create_required_volumes`, the nine stages of `prepare_deployment_files` and `start_deployment` all still run after the teardown, and most can raise on deterministic config input. Three review rounds on #292 found four such routes by inspection with no argument the list was complete — the per-route fix does not close the class | L |

---

## Closed during planning (already fixed or superseded — evidence on each issue)

| Issue | Evidence |
|---|---|
| [#244](https://github.com/fasrc/archi/issues/244), [#239](https://github.com/fasrc/archi/issues/239) | Fixed by merged PR #240 (commit `80765cb4`): `postgres_vectorstore.py:411` normalizes `score = 1.0 - distance` for every metric, with in-code monotonicity proof |
| [#148](https://github.com/fasrc/archi/issues/148) | `goldenset-report.timer` active on the fasrc-dev host, firing 06:15 daily (verified via `systemctl --user list-timers`) |
| [#63](https://github.com/fasrc/archi/issues/63) | Core deliverable shipped as `archi sources build` (PR #37, `cli_main.py:909`); the issue self-describes as superseded |

## Parked — 39 issues, labeled `parked`

Query: `gh issue list --repo fasrc/archi --label parked`. An issue leaves the parking
lot by gating a future feature release, not by aging.

| Issue | One-line reason |
|---|---|
| #280 | Headless `/v1` strip-down scoping — track is not a release driver; go/no-go unmade |
| #156, #157 | Incremental re-ingest PR-2/PR-3 — track not a driver; both blocked on an unmerged PR-1 enabler |
| #276 | lastmod hand-list vs sitemap ownership — metadata-only, both outcomes small and opposite |
| #166 | CRUD for source list — exploration; deliverable is a note, must not change behavior |
| #278, #253, #254 | duckdb-guard completeness gaps — the guard is already a net improvement with zero live failures; #254 is artifact reconciliation |
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
| #290 | `archi evaluate --force` teardown ordering — destructive and real, but `corpus_fingerprint` (`benchmark_provenance.py:288`) is content-derived and per-arm, so a rebuilt corpus is detectable in the artifact; September's numbers stay attributable without it |
| #288 | Keep `fasrc_archi.md` truthful as its dependencies land — docs-only, same disposition as #190/#115 |
| #291 | Reformat `secrets_manager.py` to black — enabler; no milestone issue edits this file |

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
