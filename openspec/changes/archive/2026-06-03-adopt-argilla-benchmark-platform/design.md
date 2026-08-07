## Context

The benchmark today is a one-shot CLI that dumps JSON + HTML. It produces no human-grading workflow, has no inter-rater reliability mechanism, and its RAGAS scoring has two known `TODO this is likely broken now` markers. Upstream `archi-physics/archi` has a `feature/add-offline-ab-benchmarking` branch with a clean Argilla integration, A/B grading flow, judge/SUT split for RAGAS, a `archi grade` subcommand, and parallel question execution.

Reviewing the upstream code confirmed three things relevant to design:

1. **`benchmark_argilla.py` is architecturally clean** — it consumes a benchmark output dict and pushes/pulls to Argilla via its SDK. No coupling to any agent pipeline, no copilot SDK imports, no langgraph imports.
2. **The data shape required matches what our fork already produces** for single-config runs (`benchmarking_results[0].single_question_results`). A/B mode requires an additional `ab_comparison` shape built by upstream's `ABResult` / `pair_ab_results()` / `dump_ab_comparison()` (~150 lines), but those are also self-contained.
3. **Upstream's `get_ragas_llm_evaluator` already added the judge/SUT config split** (`ragas_settings.evaluator_provider` falls back to `benchmark_cfg.provider`). This is exactly what we need for HUIT Bedrock-as-judge, already designed.

That made cherry-picking the obvious path over building parallel infrastructure.

## Decisions

### 1. Cherry-pick from upstream rather than rewrite

We lift the Argilla integration from `upstream/feature/add-offline-ab-benchmarking` rather than implement our own. The portability audit (no copilot SDK coupling, data shape compatible, judge/SUT split already designed) makes this safer than reinventing. Estimated effort delta: weeks saved vs. a clean build.

**Rejected alternative:** _Build our own Argilla integration on current `service_benchmark.py`._ Would have avoided any future merge cost when upstream's branch merges to their `dev`, but doubles the engineering work today and means we have two implementations of the same idea in the ecosystem.

**Rejected alternative:** _Adopt upstream's full branch including the copilot SDK migration._ Reverses our deliberate drift decision; copilot SDK adoption is a large architectural shift unrelated to evaluation.

### 2. Skip the copilot SDK migration; keep CMSCompOpsAgent on LangChain ReAct

Our fork continues to use `agent_class: CMSCompOpsAgent` (the LangChain ReAct pipeline). None of the lifted code references the copilot SDK; the benchmark constructs `archi(pipeline=..., agent_spec=..., default_provider=..., default_model=...)` exactly as our fork does today. This decision is independent of the Argilla adoption and can be revisited separately.

### 3. HUIT Bedrock is a proper provider, also usable as RAGAS judge

The earlier stash work (`stash@{2}`) added `huit_bedrock` only as a case in `get_ragas_llm_evaluator()`, which would have made it judge-only. We complete the work: register `huit_bedrock` in `src/archi/providers/huit_bedrock_provider.py` (matching the existing `anthropic_provider.py`, `openai_provider.py`, etc. patterns), so it can be selected anywhere in archi — including SUT and judge. The primary motivation remains the RAGAS-judge use case (Claude judging Qwen, independent and Harvard-compliant), but the implementation is the general one.

**Why the judge/SUT split matters operationally:** today both fields share the same config key, so to use Bedrock Claude as the judge you'd have to also use it as the SUT — which defeats the point. With `mode_settings.ragas_settings.evaluator_provider: huit_bedrock` and `mode_settings.ragas_settings.evaluator_model: us.anthropic.claude-sonnet-4-5-20250929-v1:0`, the SUT can stay on local Qwen while the judge is Claude. This is the upstream design and we adopt it as-is.

**Why Sonnet 4.5 (pinned) rather than 4.6:** HUIT's Bedrock catalog lists Claude Sonnet 4.6 as bare `us.anthropic.claude-sonnet-4-6` with no date/version suffix, suggesting it's a rolling alias that resolves to whatever current 4.6 build Bedrock serves. For a benchmark judge, that's a reproducibility problem — the same prompt could be scored differently a month later when the alias rolls forward. Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) is explicit and pinned, more than capable for RAGAS grading, and stable across the multi-month comparisons we want to make. Revisit once 4.6 is published with a dated/pinned ID.

### 4. A/B preference is the primary grading mode; binary correctness + tags is the secondary

For comparing configs (v1-strict vs v2-lean prompts, Qwen vs Claude, etc.) — A/B preference (`rg.LabelQuestion(labels=["A", "B", "Tie"])`) gives higher inter-rater agreement than absolute scoring because humans are better at relative judgments. Argilla's upstream `push_ab_results_to_argilla` builds the A/B record schema natively.

For absolute quality measurement ("is archi correct on this question?") — a binary correctness label + multi-select failure-mode tags + free-text notes. This is `push_single_results_to_argilla` with our customizations: we add a `LabelQuestion(name="correctness", labels=["correct","partial","incorrect"])` and a `MultiLabelQuestion(name="failure_modes", labels=["hallucination","off_topic","incomplete","wrong_sources","too_long","too_short"])` to the upstream single-config schema.

Both modes coexist; a given run picks one based on whether configs are being compared.

**Rejected alternative:** _Likert (1-5) as primary._ Higher information per record but lower inter-rater agreement and well-documented center-bias. We adopt Likert only as a secondary "quality" rating within the A/B mode (rating the winning response), not as the primary outcome.

### 5. Argilla deploys alongside archi on the same host, separate docker-compose project

Argilla requires ElasticSearch as a backend. Both run as standalone docker containers, not as `archi deploy`-managed services (Argilla isn't an archi service; it's external infrastructure archi pushes to). We use Argilla's published docker-compose for the two services and expose port 6900 on the host.

Resource cost on the archi host: ~2 GB RAM for ElasticSearch + Argilla server combined, negligible CPU at rest, ~5 GB disk for the ES index of all eval datasets. Within budget on `holygpu7c0717`.

**Rejected alternative:** _Hugging Face Cloud (managed Argilla)._ Simplest operations, but FASRC question content + retrieved Cannon doc snippets would leave the host. Even though Cannon docs are public, the policy posture is cleaner if eval data stays on-host.

**Rejected alternative:** _Separate VM for Argilla._ Cleaner separation of concerns, but more infra and no real benefit at our scale.

### 6. Question bank starts in git; spreadsheet bridge is a follow-up

The questions live in `config/benchmarking/queries.json`, edited via PR (or, for now, direct on the host since `/config/` is gitignored on this fork). This is a deliberate MVP choice: it postpones building a question editor until we know it's actually the bottleneck.

If Harvard AI experts find git-editing too high-friction, a follow-up change adds a Google Sheets bridge: archi pulls the sheet via a service account, writes `queries.json`, then runs evaluate. The sheet's column structure is `question | expected_answer | expected_sources | source_match_field | tags | version`, and sheet history doubles as version log.

The question editor question is decoupled from this proposal so the platform can land without it.

### 7. Corpus snapshotting: log + within-run discipline first; volume snapshot later

A vectorstore that changes between runs invalidates cross-run comparisons. Three discipline levels were considered; we adopt the cheap two now:

- **Log:** every run records `last_ingestion_ts` and the SHA of the relevant source list in run metadata. This lets analysis flag invalidated comparisons after the fact.
- **Within-run only:** comparisons between configs MUST be done in a single `archi evaluate -cd <dir>` sweep. The corpus is necessarily identical across configs in one sweep. This becomes a hard rule in the analysis notebook (refuses to compare runs with different `corpus_snapshot_id`).
- **Volume snapshot (deferred):** `pg_dump` the vectorstore tables before a comparison run. Needed only when we want to replicate historical runs months later. Out of scope for this change.

### 8. Blinding: hide config metadata, randomize order, accept voice leak

Three layers:
1. **`agent_name`, `model`, `provider`, `config_name` go in Argilla metadata** (hidden from the grader UI by default). Free.
2. **Argilla `TaskDistribution` randomizes record order per grader.** Free.
3. **Model voice cannot be fully hidden.** Claude's cadence, Qwen's hedging tics, GPT's list-fetishism remain detectable. Mitigation: A/B preference reduces voice-recognition leverage (both answers visible at once). Post-grading, we ask graders "what fraction did you think you could guess the model on?" If >50%, scores are partly model-recognition.

The writeup acknowledges this honestly. We do not strip formatting or length-normalize — that destroys what's being measured.

### 9. Distribution: every record graded by ≥2 evaluators (configurable)

`rg.TaskDistribution(min_submitted=2)` is the floor. A record is "done" when 2 graders submit. For high-stakes decisions (changing the default agent in production) operators bump to 3.

The value is exposed as `services.benchmarking.argilla.min_submitted` in the config schema (positive integer, default `2`). The Argilla push step reads it and configures `rg.TaskDistribution(min_submitted=<value>)` when creating the dataset settings.

**Recommended question-bank starting size: 30-50.** Not a config field — the queries file size IS the answer. Below 30 gives insufficient power to detect <10pp differences with paired Wilcoxon at α=0.05. Above 50, grading fatigue dominates and IRR degrades. Bump to 100 only for confirmatory comparisons after exploratory work has narrowed which hypothesis matters.

### 10. Pre-registration is a repo artifact, not a runtime check

Before opening a grading round, the operator commits `docs/eval/preregs/<YYYY-MM-DD>-<study-slug>.md` with: primary hypothesis, primary outcome metric and how it's computed, statistical test, decision rule, secondary (exploratory) analyses flagged as such, stopping rule. The pre-reg is referenced in the eventual writeup so reviewers can verify the analysis wasn't fished.

No tooling enforces this — it's a convention, like commit messages. The template at `docs/eval/preregs/_template.md` makes it cheap to follow.

### 11. Anchor questions ride invisibly in every run

A small set (3-5) of curated questions with known-clear answers, distinct from the regular test bank. Three types: easy-retrieve, reasoning, and should-refuse. Stored in `config/benchmarking/anchor_questions.json` and merged into the queries set at run time. Graders see them as ordinary records — they are NOT marked as anchors in the UI. Two uses: (a) grader sanity check (an anchor marked wrong when verifiably right flags inattention); (b) regression detection (an anchor passing in v1 and failing in v2 is a hard fail of the new config).

## Migration plan

### Phase 1 — Lift portable code (no behavior change to current eval)

Cherry-pick the seven file regions from `upstream/feature/add-offline-ab-benchmarking` listed in the proposal, adapt names/imports to our fork's layout, run the lifted upstream unit tests. At this point `archi evaluate` works exactly as before; `--argilla` is a no-op until the server is deployed.

### Phase 2 — HUIT Bedrock provider

Build `src/archi/providers/huit_bedrock_provider.py` from the stash, register it. Add `huit_bedrock` case to `get_ragas_llm_evaluator` so it's selectable as judge. Smoke-test: a tiny RAGAS run (3 Qs) with `evaluator_provider: huit_bedrock` produces finite float metric values.

### Phase 3 — Deploy Argilla + ElasticSearch

Drop in Argilla's upstream docker-compose (lightly adapted: pin versions, mount a volume for ES data under `/scratch/docker/volumes/argilla-es/`, configure the API key from a secrets file). Open the firewall (iptables INPUT position 12, tcp/6900). Create the "archi" workspace + a service-account user for `archi evaluate` to push as.

### Phase 4 — First eval round, dry-run

Run `archi evaluate --argilla` against a small (10-question) subset of `queries.json`, with both v1-strict and v2-lean configs sweeping in a single call. Verify in Argilla UI that records appear with hidden metadata, A/B mode renders side-by-side, RAGAS scores show in metadata, traces are collapsible. Two staff grade the dry-run records to validate the workflow before a real round.

### Phase 5 — Documentation and conventions

Write the rubric, pre-reg template, anchor question seed list, analysis notebook scaffold. Document the four-step operator loop in `docs/docs/benchmarking.md`.

### Rollback

Each phase is independently revertible:
- Phase 1-2: revert the commits; `archi evaluate` reverts to pre-change behavior
- Phase 3: `docker stop argilla-server argilla-elasticsearch`; remove iptables rule; `archi evaluate --argilla` produces an Argilla-unreachable error but the JSON/HTML reports still write
- Phase 4-5: documentation; no rollback needed

## Open questions

### Q1: Is `archi()` safe for parallel instantiation?

Upstream's `_create_chain_pool` builds N independent `archi(pipeline=...)` instances and runs them concurrently in a `ThreadPoolExecutor`. Our fork's `archi()` constructor needs to be audited for shared global state — particularly:
- Postgres connection pool (`PostgresServiceFactory`)
- Vectorstore client / embedding model singleton
- MCP client/session (lives on a background async loop)
- Static tool registry caches in `CMSCompOpsAgent`

If any of these mutate cross-chain, parallel execution corrupts results. Default `n_workers=1` is the safe answer until audited. Resolution required before enabling parallel runs.

### Q2: What is the staff source range for the Argilla iptables rule?

Same question as the dev deployment's port 7891: which subnet (Harvard internal, FAS-RC VPN) gets `tcp/6900`? The previous OpenSpec change (`split-prod-dev-deployments`) deferred this. Resolve once for both ports.

### Q3: Does the upstream "eval dashboard" port cleanly?

Commit `ed700f4d` on the upstream branch adds an "eval dashboard". We deferred inspecting it in the audit. If it's a small Flask app reading benchmark JSON, it's a free win to lift. If it depends on copilot-SDK event streams or upstream-only data shapes, we skip. Worth a 30-minute look before Phase 1.

### Q4: How many graders, how many questions per round?

Drives statistical power. 50 questions × 2 configs × 3 graders = 600 grading events → detects ~10pp differences reliably. 100 × 2 × 3 = 1,200 → detects ~7pp. The right answer depends on the smallest effect we care about; needs operator input before the first real round.

### Q5: Is the Argilla-acquired-by-HuggingFace timeline a concern?

Argilla 2.x is open-source and maintained as of training-data cutoff (early 2026). Long-term roadmap sits with HF post-acquisition. Risk: HF could deprecate self-hosting in favor of HF Cloud. Mitigation: the data we put into Argilla (records, grades) is fully exportable via the SDK at any time — vendor lock-in is low even if Argilla goes commercial-only.

### Q6: Where do per-evaluator identities and Harvard SSO fit?

Argilla supports basic-auth and OAuth (Keycloak). For staff use, basic-auth with per-evaluator user accounts is the MVP. Integrating with the existing archi SSO (Harvard SAML, used by chat_app) is a follow-up if it becomes needed.
