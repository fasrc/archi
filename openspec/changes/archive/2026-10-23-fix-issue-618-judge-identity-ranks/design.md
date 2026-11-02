## Context

The RAGAS judge for a benchmark arm comes from `ragas_judge_identity(config)` (`src/bin/service_benchmark.py:161`): `evaluator_provider` or the SUT `provider`, and `evaluator_model` or the SUT `model`. `get_ragas_llm_evaluator` (`service_benchmark.py:1791`) then picks the endpoint and the client:

| provider | client | endpoint |
|---|---|---|
| `openai` | `ChatOpenAI` | none (library default) |
| `ollama` | `ChatOllama` JSON | `evaluator_ollama_url` or `ollama_url` |
| `local` | `ChatOllama` JSON or `get_model("local", ..., mode="openai_compat")` | `evaluator_ollama_url` or `ollama_url` |
| `huggingface` | `get_model("local", ..., mode="openai_compat")` | that URL, else `http://localhost:8000/v1` |
| `anthropic` | `ChatAnthropic` | none |
| `huit_bedrock` | `get_model("huit_bedrock", ...)` | `benchmarking.base_url` or the HUIT default |
| other | `ChatOpenAI` | none |

For `local`, the mode is `resolve_local_mode(url, explicit_mode)`, where `explicit_mode` is `evaluator_provider_mode` when it is not `None` or `""`, else the SUT `provider_mode`.

The run record already carries `ragas_effective_settings` (judge pressure, `service_benchmark.py:711`). It is `None` when no judge ran or the judge never scored (`ragas_scored is False`, #518). `arms_incomparability_reason` skips an absent key and treats `None` as a value.

## Goals / Non-Goals

**Goals:**
- Record the judge identity the run used, per arm.
- Report identity drift in the leaderboard shared context.
- Withhold ranks when two judged arms used different judges.

**Non-Goals:**
- No change to which client is built, or with which arguments, for any provider. The builder now reads its provider, model, endpoint, and local mode from the helper, but the values are the same as today.
- No change to `ragas_judge_identity`, `UsageRecorder`, or the configuration digest basis.
- No backfill of old artifacts.

## Decisions

1. **One pure helper, used by the builder.** Add `ragas_judge_effective_identity(config) -> Dict[str, Optional[str]]` next to `ragas_judge_identity`. It returns `{"provider": <lowercased provider>, "model": <model>, "base_url": <endpoint per the table, or None>, "mode": <resolve_local_mode result for local, else None>}`. `get_ragas_llm_evaluator` calls it once at the start and stores the dict on `self._ragas_judge_identity`. Then it takes `provider_key`, `model_name`, and the endpoint only from that dict (`identity["provider"]`, `identity["model"]`, `identity["base_url"]`), and its own `ragas_judge_identity` call and endpoint fallbacks are removed. The `ollama`, `local`, `huggingface`, and `huit_bedrock` arms pass `identity["base_url"]` where they now use `ollama_url`, the huggingface fallback, or the bedrock fallback; the `local` and `huggingface` arms keep their `normalize_base_url(...)` keyword, built from `identity["base_url"]`. The `local` arm branches on `identity["mode"] == "openai_compat"` instead of calling `resolve_local_mode` again. The fallback defaults live only in the helper. This is what "effective" means here: the values the builder used, not a second computation from the config that can drift. Each value equals what the builder computes today, so the built clients do not change. A test pins the helper output for each provider row, and a second test checks that each built client receives the helper's endpoint.
   - Alternative: compute the identity in `handle_results` from the config. Rejected: the issue requires the effective values, and a recompute is exactly what `ragas_effective_settings` warns against.
   - `base_url` is recorded as the raw configured string (not `normalize_base_url`), so two arms that wrote the same URL compare equal.

2. **Record field.** `ResultHandler.handle_results` gets keyword-only `judge_identity: Optional[Dict[str, Any]] = None`. The record key `ragas_judge_identity_effective` is `judge_identity` when `ragas_effective_settings` is not `None`, else `None`. The key is always present on new records, as `judge_usage` is. `judge_identity` does not enter the configuration digest.

3. **Plumbing.** `run()` sets `self._ragas_judge_identity = None` next to `self._judge_usage = None` (`service_benchmark.py:2455`) and passes `judge_identity=getattr(self, "_ragas_judge_identity", None)` to `handle_results` next to `judge_usage=`.

4. **Predicate.** In `arms_incomparability_reason`, collect `tuple(sorted(identity.items()))` for each record whose `ragas_judge_identity_effective` is present and not `None`. If more than one distinct tuple exists, return `"the arms were scored by different RAGAS judges (provider, model, endpoint, or client mode), so their scores are not comparable"`. Check order: after the judge-pressure check and before the corpus-fingerprint check, so a pressure difference keeps its own reason. `None` is skipped (not a value) because an unjudged arm is already caught by the pressure predicate (`None` pressure); counting it twice adds nothing.

5. **Shared context.** In `build_leaderboard`, add `ctx_fields["judge_identity"]`, filled with a stable string form (`json.dumps(identity, sort_keys=True)`) for a non-null record identity. The existing reduction then reports one value when all arms agree and a "differs across swept configs" warning when they do not. `ctx_fields["judge_identity"]` must exist in the `ctx_fields` initializer (`service_benchmark.py:1160`).

## Risks / Trade-offs

- [A sweep that once ranked now withholds] → This is the intended behavior change; name it in the PR body and the docs.
- [Two arms with the same judge but URLs written differently, for example a trailing slash] → They withhold. Safe direction; the operator sees the two values in the warning.
- [The helper and the builder drift] → Cannot happen for the recorded fields: the builder reads provider, model, endpoint, and mode from the helper. The endpoint test in task 2.1 fails if an arm goes back to its own value.
