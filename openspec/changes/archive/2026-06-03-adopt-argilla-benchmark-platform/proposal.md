## Why

Archi's existing `archi evaluate` benchmark dumps a JSON + HTML report and stops there. It produces no mechanism for human evaluation, no inter-rater reliability data, no A/B grading workflow, and the embedded RAGAS scoring has two `# TODO this is likely broken now` markers (`src/bin/service_benchmark.py:503,548`) that have not been verified working since the local-judge code path landed. The result is that we cannot answer the question we actually care about — "is the v2-lean prompt better than the v1-strict prompt for FASRC users?" — with data we trust.

The intended evaluation workflow involves multiple Harvard staff (HPC facilitators and AI experts) independently grading archi's responses to a curated question set, with their grades collated for inter-rater reliability and config-vs-config comparison. Building this from scratch is weeks of UI work.

Upstream's `feature/add-offline-ab-benchmarking` branch already contains a clean Argilla integration (`src/utils/benchmark_argilla.py`, 584 lines, plus 759 lines of tests), an A/B preference grading flow, a judge/SUT config split for RAGAS, a `archi grade` CLI subcommand, parallel question execution, and checkpoint-resumable runs. A code audit confirms the Argilla path is **not entangled** with upstream's copilot SDK migration — it is a pure consumer of the benchmark output dict, whose shape matches what our fork already produces.

The bottleneck is therefore not building a platform but porting one and completing two specific gaps: registering Harvard's HUIT Bedrock as a proper archi provider, and using HUIT Bedrock Claude as the RAGAS judge (independent of the local Qwen system under test, Harvard-compliant on data residency).

## What Changes

- **Lift the Argilla integration from upstream** `feature/add-offline-ab-benchmarking`: `src/utils/benchmark_argilla.py`, the Argilla call sites in `src/bin/service_benchmark.py`, the `ABResult` dataclass and pairing/dump helpers, `_create_chain_pool` + `_prefetch_questions_parallel` for parallel execution, the `--argilla` / `--argilla-server` flags on `archi evaluate`, the new `archi grade --serve` / `--export` subcommand, the `pip install 'argilla>=2.5,<3'` line in `Dockerfile-benchmarks`, and `tests/unit/test_benchmark_argilla.py`.
- **Skip the copilot SDK migration.** Our fork continues to use `CMSCompOpsAgent` on the LangChain ReAct pipeline. None of the lifted code references the copilot SDK; the benchmark machinery runs whatever pipeline the config points at.
- **Adopt the upstream RAGAS judge/SUT config split.** A new `mode_settings.ragas_settings.evaluator_provider` (and `evaluator_model`, `evaluator_ollama_url`) cleanly decouples the RAGAS judge from the system under test. This unblocks the "RAGAS scores are biased because the judge is the same model being judged" failure mode.
- **Complete HUIT Bedrock as a proper provider** in `src/archi/providers/huit_bedrock_provider.py`, replacing the `src/bin/huit_bedrock_llm.py` stub that lived only in the RAGAS-judge case statement. Both the SUT path and the RAGAS-judge path can then select HUIT Bedrock by name.
- **Deploy Argilla + ElasticSearch** as additional docker-compose services on the archi host. Argilla on port 6900, opened to staff via iptables (same convention as 7861/7891 — INSERT at position 12).
- **Establish scientific-rigor conventions** as repo artifacts: a pre-registration template at `docs/eval/preregs/_template.md`, an anchor-questions list distinct from the test bank, and an analysis notebook scaffold that computes per-config win rates, pairwise Cohen's kappa and Fleiss' kappa, per-grader bias distributions, and RAGAS↔human correlation.
- **Verify RAGAS works end-to-end** before depending on its scores. A small smoke test (`tests/smoke/ragas_smoke.py`) runs the benchmarker on 3 questions with HUIT Bedrock as judge and asserts that every record receives finite float values for all four RAGAS metrics.

## Capabilities

### New Capabilities

- `argilla-benchmark-grading`: how archi exports benchmark results to a self-hosted Argilla instance for independent human grading, how RAGAS metrics are computed with an independent judge LLM (HUIT Bedrock), how A/B preference grading and absolute-quality grading coexist, how blinding and counterbalancing are enforced, how grades are pulled back for analysis, and the scientific-rigor conventions (pre-reg, anchors, IRR thresholds) that surround the platform.

### Modified Capabilities

_None._ Existing benchmark capability (`SOURCES` mode and `RAGAS` mode in `archi evaluate`) continues to function with the same config schema. This change adds capability around it; the only existing behavior touched is the `get_ragas_llm_evaluator` function, which gains the judge/SUT config split as an additive change (falls back to current behavior when the new config keys are absent).

## Impact

- **Operational:** running an eval round becomes a four-step loop: (1) update questions in `config/benchmarking/queries.json`, (2) `archi evaluate --argilla -cd <configs/>` (sweeps all configs in one corpus snapshot), (3) email evaluators the Argilla URL, (4) once grading is done, `archi grade --export` to JSON and run the analysis notebook.
- **Infrastructure:**
  - New docker-compose services on the archi host: `argilla-server`, `argilla-elasticsearch` (Argilla's required backend)
  - New iptables INPUT rule at position 12 for tcp/6900 from the staff source range
  - New pip dependency in the benchmarks Dockerfile: `argilla>=2.5,<3`
  - New secrets: `huit_api_key.txt`, `argilla_api_key.txt`
- **Code:**
  - `src/utils/benchmark_argilla.py` lifted from upstream (~584 lines, ports cleanly)
  - `src/archi/providers/huit_bedrock_provider.py` (~80 lines, completes the partial stash work)
  - `src/bin/service_benchmark.py` gains the Argilla integration block, `ABResult` dataclass, parallel chain pool, and judge/SUT config split (~300 lines added)
  - `src/cli/cli_main.py` gains `--argilla` flag and `archi grade` subcommand (~110 lines added)
- **CI:** `tests/unit/test_benchmark_argilla.py` lifted from upstream (~759 lines) gives substantial regression coverage on the integration. New smoke test for RAGAS end-to-end.
- **Documentation:**
  - Updated `docs/docs/benchmarking.md` covering the Argilla workflow, judge/SUT split, pre-reg template, and grading conventions
  - New `docs/eval/preregs/_template.md`
  - New `docs/eval/anchor_questions.md`
  - New `docs/eval/rubric.md` describing the four annotation widgets (winner, quality, failure-mode tags, notes) and the binary-vs-Likert rationale
- **Risk that warrants flagging:**
  - **Parallel-chain safety.** Upstream's `_create_chain_pool` instantiates N `archi()` instances and runs them concurrently in a ThreadPoolExecutor. If our fork's `archi()` constructor or any pipeline mutates shared global state (vectorstore client, postgres pool, MCP session), parallel chains can collide. Must be audited before enabling `n_workers > 1`.
  - **Voice leaks defeat full blinding.** Model "voice" (Claude vs Qwen cadence) remains detectable to graders even with config metadata hidden. The A/B preference design partially mitigates by putting two answers side by side; full mitigation is not possible without distortion. The writeup must acknowledge this.
  - **Argilla acquired by HuggingFace mid-2024.** Open source and maintained, but long-term roadmap sits with HF. Acceptable risk for the value delivered now.
