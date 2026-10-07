## Context

`run()` (`src/bin/service_benchmark.py` ~2340-2375) calls `self._process_config(modes_being_run)` per arm and then `ResultHandler.handle_results(...)` with `modes_executed=modes_being_run`. `_process_config` returns a 2-tuple `(question_wise_results, total_results)`; unit tests unpack that tuple (`tests/unit/test_benchmark_resilience.py:392-443`). Judge token usage already travels from `_process_config` to `handle_results` through an instance attribute: `run()` sets `self._judge_usage = None` before the arm, the judge path sets it, and the call site passes `judge_usage=getattr(self, "_judge_usage", None)`.

## Goals / Non-Goals

**Goals:** `ragas_effective_settings` is `null` for a RAGAS-mode arm that the judge did not score; unchanged for every other case.

**Non-Goals:** change `modes_executed`, the digest basis, the leaderboard predicate, `judge_usage`, or the schema validators (#521).

## Decisions

1. **Carry the flag on the instance, not in the return tuple.** `_process_config` sets `self._ragas_scored`; `run()` resets it to `None` before each arm (next to `self._judge_usage = None`) and passes `ragas_scored=getattr(self, "_ragas_scored", None)`. Reason: the return shape stays a 2-tuple, so existing callers and tests do not change, and it mirrors `_judge_usage`.
   - In `_process_config`, inside `if "RAGAS" in modes_being_run:` set `self._ragas_scored = bool(ragas_input)`. When RAGAS is not a mode, leave it `None`.
2. **Tri-state keyword.** `handle_results(..., ragas_scored: Optional[bool] = None)`. Only `False` changes behavior: `ragas_effective_settings` becomes `None` when `ragas_ran and ragas_scored is False`. `True` and `None` keep today's output.
3. **Digest basis unchanged.** `config_version(... effective_selected=with_effective_ragas_settings(config, modes_executed=modes_executed))` still reads `modes_executed`, so the digest of an unjudged RAGAS arm equals the digest the same arm had before this change.
4. **Leaderboard effect accepted, not coded.** `arms_incomparability_reason` already adds `None` for a null pressure. No change there; a test pins the withheld ranking.

## Risks / Trade-offs

- `judge_usage` stays gated on `ragas_ran` only. For an unjudged arm it is already `None` in practice (no recorder ran), so no change is needed.
- A sweep with one all-failed arm now loses its ranking. This is the correct outcome and the operator accepted it (issue #518, 2026-09-26).
