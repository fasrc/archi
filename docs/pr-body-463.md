fix(#463): canonicalize and validate the local provider's mode at the config seams

Closes #463

## Summary

- Introduces `src/utils/local_mode.py` with `canonical_local_mode` / `apply_local_mode` / `MODE_OLLAMA` / `MODE_OPENAI_COMPAT` — the single source of truth for the accepted mode whitelist
- Wires all four config seams and `LocalProvider.__init__` to canonicalize the raw `mode` value before routing, so case variants and surrounding whitespace resolve correctly and unrecognized values raise at construction
- Fixes a pre-existing bug where a present-but-null `local_mode` key sent `list_models` and `validate_connection` down their non-Ollama branches

## Before / After

**Before (dev tip)** — reproduction probe (`LocalProvider` constructed directly):

| mode | client | raises? |
|---|---|---|
| `openai_compat` | ChatOpenAI (compat) | no |
| `OpenAI_Compat` | ChatOllama | no — **wrong client** |
| `OPENAI_COMPAT` | ChatOllama | no — **wrong client** |
| ` openai_compat ` | ChatOllama | no — **wrong client** |
| `ollama` | ChatOllama | no |
| `Ollama` | ChatOllama | no |
| `openai-compat` | ChatOllama | no — **silently wrong** |
| `vllm` | ChatOllama | no — **silently wrong** |
| `""` | ChatOllama | no — **silently wrong** |
| `None` | ChatOllama | no |

**After (this branch)**:

| mode | client | raises? |
|---|---|---|
| `openai_compat` | ChatOpenAI (compat) | no |
| `OpenAI_Compat` | ChatOpenAI (compat) | no |
| `OPENAI_COMPAT` | ChatOpenAI (compat) | no |
| ` openai_compat ` | ChatOpenAI (compat) | no |
| `ollama` | ChatOllama | no |
| `Ollama` | ChatOllama | no |
| `openai-compat` | — | YES: `ValueError` |
| `vllm` | — | YES: `ValueError` |
| `""` | — | YES: `ValueError` |
| `None` | ChatOllama | no |

## Behavior change — named explicitly

A deployment configured with an unrecognized `mode` (e.g., `mode: vllm`, `mode: openai-compat`) that previously started silently and served an Ollama client now **fails at construction with a `ValueError` naming the rejected value and the two valid values** (`ollama`, `openai_compat`). This applies at all four config seams and directly when constructing `LocalProvider`.

This is intentional and required by issue #463. Such a deployment is not working correctly — it is serving an Ollama client to an operator who asked for something else.

## Deviation from the `grep -c` acceptance criterion

Issue #463 asks that `grep -c 'openai_compat"' src/archi/providers/local_provider.py` show the literal in exactly one predicate. With the constant defined in `src/utils/local_mode.py` and imported, that count is **0**, not 1.

The criterion's stated purpose is that "the whitelist and `_is_openai_compat` agree rather than duplicating the string test". Zero duplicates satisfies this strictly better than one: `_is_openai_compat` compares against the `MODE_OPENAI_COMPAT` constant imported from `src/utils/local_mode.py`, and across `local_mode.py` and `local_provider.py` together the literal `openai_compat"` appears exactly **once** — on the `MODE_OPENAI_COMPAT` assignment. See `design.md` Decision 1.

## Why `src/utils/local_mode.py`, not `src/archi/providers/`

The obvious home for the whitelist is beside `LocalProvider`. It is wrong for a measured reason: importing any submodule of `src.archi.providers` executes `src/archi/providers/__init__.py`, whose line 21 imports `src/archi/providers/base.py`, whose line 13 is `from langchain_core.language_models.chat_models import BaseChatModel`. A module under `src/archi/providers/` that imports nothing but `typing` still drags LangChain in behind it.

Measured on the branch tip:

```
import src.data_manager.collectors.processing  -> 'langchain_core' in sys.modules == False
import src.archi.providers.base                -> 'langchain_core' in sys.modules == True
import src.utils.logging                       -> 'langchain_core' in sys.modules == False
```

`src/data_manager/collectors/processing.py:784` imports the provider inside a function on purpose — its docstring at lines 775-783 says why: a module-scope import "would make `processing.py` ... unimportable wherever langchain is absent". `src/bin/benchmark_sut.py` has only `typing` at module scope. Both would be broken by placing the canonicalizer under `src/archi/providers/`. `src/utils/` is LangChain-free and both files already import from it. See `design.md` Decision 1.

## Amendment to the unarchived #450 change directory

`openspec/changes/fix-issue-450-ollama-host-scope/specs/local-provider-endpoint-resolution/spec.md` held two scenarios and a rationale paragraph that asserted behavior this change removes:

- "Scenario: An unrecognized mode resolves the Ollama endpoint" (mode `vllm`) — `vllm` now raises
- "Scenario: An unrecognized mode still yields to the environment" (mode `vllm`) — `vllm` now raises
- Rationale at lines 61-64: "`ChatOllama` for every other value, including `None` and a misspelling" and "no seam validates or canonicalizes it" — both sentences are now false

Both scenarios are restated against a **null** mode, which still resolves the Ollama endpoint and still yields to `OLLAMA_HOST`, so the requirement keeps its point — one predicate serves both sites — without asserting behavior the tree no longer has. The requirement's SHALL line ("only the exact mode `openai_compat`") is unchanged. See `design.md` Decision 7.

## Out-of-scope: the seams' differing precedence

The four config seams disagree about what wins when a config sets both `mode` and `extra_kwargs.local_mode`: `app.py` and `base_react.py` overwrite the `extra_kwargs` value; `__init__.py` and `processing.py` let it win. This change preserves each seam's existing precedence exactly via `apply_local_mode`'s `overwrite` parameter — the divergence is now visible in one parameter rather than hidden across four files. Unifying it would change behavior for configs that set both keys, something #463 never asked for. See `design.md` Decision 3.

## Five restated tests in `test_local_provider_env_override.py`

The file held five tests that built a provider with `local_mode="vllm"` and asserted it resolves an Ollama endpoint. These tests correctly pinned the behavior #450 shipped; #463 removes that behavior, so they are restated (not deleted) against a **null** mode, which still resolves Ollama and still yields to `OLLAMA_HOST`. Each test keeps its original point — the endpoint rule and the client dialect agree; `OLLAMA_HOST` still applies; a per-call mode cannot promote.

| original test | restated as |
|---|---|
| `test_unknown_mode_falls_back_to_the_ollama_default` | `test_null_mode_falls_back_to_the_ollama_default` |
| `test_unknown_mode_still_honors_ollama_host` | `test_null_mode_still_honors_ollama_host` |
| `test_unknown_mode_configured_base_url_yields_to_ollama_host` | `test_null_mode_configured_base_url_yields_to_ollama_host` |
| `test_unknown_mode_endpoint_and_client_agree` | `test_null_mode_endpoint_and_client_agree` |
| `test_a_per_call_mode_cannot_promote_an_unrecognized_mode` | `test_a_per_call_mode_cannot_promote_a_null_mode` |

See `design.md` Decision 9.

## Second pre-existing defect fixed

`LocalProvider.__init__` now substitutes `MODE_OLLAMA` when canonicalizing a present-but-null `local_mode` key. The `local_mode` property is `self.config.extra_kwargs.get("local_mode", "ollama")` — the default fires only on a **missing** key. A key present holding `None` returns `None`, and both `list_models` and `validate_connection` compare `self.local_mode == "ollama"` — so a null key sent both down their non-Ollama branches. `validate_connection` probed an OpenAI-style path against the Ollama port and always returned False; `list_models` skipped Ollama's `/api/tags` discovery. Writing the canonical default at construction fixes all three readers at once without touching the property. See `design.md` Decision 4.

## Test plan

- [x] `bash scripts/gate.sh` exits 0 on a clean tree (4392 passed, 100% diff coverage on 39 changed lines)
- [x] Reproduction probe output matches the "After" table above
- [x] Five restated tests pass and remain in step with the restated #450 spec scenarios
- [x] `mkdocs build --strict -f docs/mkdocs.yml` exits 0 (run during task 4.1)
- [x] `openspec validate fix-issue-463-local-mode-canonicalization --strict` prints `is valid` (run during task 4.1)
