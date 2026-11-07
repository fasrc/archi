## Why

A RAGAS-mode benchmark arm whose every answer failed or was degraded never calls the judge: `_process_config` (`src/bin/service_benchmark.py:2154-2174`) takes the `build_ragas_aggregates(None, ...)` branch and builds no `RunConfig`. But `ResultHandler.handle_results` decides `ragas_ran` from `modes_executed` alone (`:528-535`), so the record still writes a `timeout` and a `max_workers` in `ragas_effective_settings` (`:664-675`) for a judge that never started. The benchmark record is dishonest for that arm (issue #518).

## What Changes

- `_process_config` records whether the judge scored anything (a metric called the judge while `RAGAS` is in `modes_being_run`; input rows with no eligible row for any enabled metric do not count) on the instance, beside the existing `_judge_usage` attribute.
- `run()` resets that attribute before each arm and passes it to `handle_results` as a new keyword argument `ragas_scored: Optional[bool] = None`.
- `handle_results` writes `ragas_effective_settings: null` when `ragas_ran` is true and `ragas_scored is False`. `None` (a caller that does not know) keeps today's behavior.
- `modes_executed` and the digest basis (`with_effective_ragas_settings(config, modes_executed=modes_executed)`) do not change, so the configuration digest of an unjudged RAGAS arm does not change.
- **Accepted behavior change:** the sweep leaderboard check (`:262-284`) reads a `null` pressure as a distinct value. A sweep with one judged and one unjudged arm now withholds its ranking with the "one arm was not judged" reason. The operator accepted this on 2026-09-26; a test pins it and the PR body states it.

## Capabilities

### New Capabilities
- `benchmark-judge-provenance`: the benchmark record claims judge settings only for an arm that the judge actually scored.

### Modified Capabilities
- (none)

## Impact

- `src/bin/service_benchmark.py` — `handle_results`, `_process_config`, `run()` call site.
- Tests under `tests/unit/` (`test_benchmark_report_records_running_config.py`, `test_benchmark_resilience.py`, `test_prompt_sweep_leaderboard.py`).
- Out of scope: `modes_executed`, the digest basis, the leaderboard check itself, `judge_usage`, and `src/utils/benchmark_schema.py` (#521).
