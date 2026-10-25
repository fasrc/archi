## Why

In `ollama` mode, `LocalProvider._get_ollama_model` spreads the caller's `**kwargs` into
`model_kwargs` and then overwrites `model_kwargs["base_url"]` with `self.config.base_url`
(`src/archi/providers/local_provider.py:158-159`). An explicit `base_url=` keyword on the
model call therefore loses. In `openai_compat` mode, `_get_openai_compat_model` spreads
`**kwargs` last, so the keyword wins. The two builders disagree (fasrc/archi#458).

No `src/` caller passes `base_url=` in `ollama` mode today, so this is a consistency fix,
pinned by a test. The operator decided the scope on 2026-09-26: "the keyword wins".

## What Changes

- `_get_ollama_model` uses `self.config.base_url` only when the call did not pass a
  `base_url` keyword. A `base_url` keyword on the call wins, as in `openai_compat` mode.
- With no keyword, behavior does not change: `self.config.base_url` (which `__init__`
  already resolved from `OLLAMA_HOST`, the config, or the default) reaches `ChatOllama`.
- `docs/docs/models_providers.md` gets one sentence after the two precedence lists: a
  `base_url=` keyword on the model call beats all of them, in both modes.

## Out of scope

- `__init__` precedence (`OLLAMA_HOST` over the configured `base_url` in `ollama` mode) stays.
- An environment escape hatch, and the compose template line the issue names.
- The benchmark judge's own `ChatOllama` construction.

## Impact

- Code: `src/archi/providers/local_provider.py` (`_get_ollama_model` only).
- Tests: new tests in `tests/unit/test_local_provider_env_override.py`.
- Docs: `docs/docs/models_providers.md`.
