## Why

Issue #618. A benchmark sweep ranks its arms even when a different RAGAS judge scored each arm. `ResultHandler.arms_incomparability_reason` (`src/bin/service_benchmark.py:220`) withholds ranks for corpus instability, configuration divergence, judge pressure, and corpus fingerprint, but not for the judge itself. The leaderboard's shared context (`service_benchmark.py:1296`) records only the raw `ragas_settings.evaluator_model`. It does not record the judge provider, the judge endpoint, or the client mode of a `local` judge.

The client mode can differ across arms even when the judge settings match: `get_ragas_llm_evaluator` (`service_benchmark.py:1791`) inherits the SUT `provider_mode` when `evaluator_provider_mode` is empty, and `resolve_local_mode` picks `ollama` (ChatOllama, JSON format) or `openai_compat` (ChatOpenAI). These two clients score differently. A rank across two judges is a claim the run cannot support. Raised by the Codex review of PR #616 (#469).

## What Changes

- Add a pure helper that returns the effective judge identity `{"provider", "model", "base_url", "mode"}` for a configuration. `mode` is `None` unless the provider is `local`. `get_ragas_llm_evaluator` calls the helper once, stores the result on the benchmarker, and reads its provider, model, endpoint, and `local` mode from the helper. As a result, the recorded identity and the built client cannot disagree.
- `ResultHandler.handle_results` takes a new keyword `judge_identity` and writes it to the record as `ragas_judge_identity_effective`. The field is `None` when `ragas_effective_settings` is `None` (no judge ran, or the judge never scored).
- `run()` resets the stored identity before each arm and passes it to `handle_results`.
- `build_leaderboard` adds the identity to `ctx_fields`, so the shared context reports judge-identity drift.
- `arms_incomparability_reason` returns a reason that names the judge when the records that carry a non-null identity hold more than one identity. A record without the key (older artifacts) or with `None` is skipped.
- Update `docs/docs/benchmarking.md` ("Reading the leaderboard" and "Judge/SUT split").

**Behavior change:** a sweep whose arms used different judge identities (provider, model, endpoint, or client mode) now withholds its ranks. Before this change it ranked them.

## Capabilities

### New Capabilities

### Modified Capabilities

- `benchmark-judge-provenance`: the run record carries the effective judge identity, and a sweep withholds ranks when the identities differ. (The capability is not yet archived; it lives in `openspec/changes/fix-issue-518-unjudged-ragas-settings/`. The delta uses ADDED requirements.)

## Impact

- `src/bin/service_benchmark.py`: new helper, `get_ragas_llm_evaluator`, `ResultHandler.handle_results`, `arms_incomparability_reason`, `build_leaderboard`, `run()`.
- `tests/unit/test_prompt_sweep_leaderboard.py`, `tests/unit/test_benchmark_report_records_running_config.py`, and a new or existing unit test file for the helper.
- `docs/docs/benchmarking.md`.
- Old result files still rank: the new predicate skips a record without the field.
