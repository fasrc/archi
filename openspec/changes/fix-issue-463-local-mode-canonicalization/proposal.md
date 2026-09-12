# Canonicalize and validate the local provider's mode value

## Why

`LocalProvider` selects its client dialect by an exact string comparison. On `origin/dev`
today, `src/archi/providers/local_provider.py:64` is `return mode == "openai_compat"`, and
that one predicate drives both the constructor's endpoint resolution
(`local_provider.py:84`) and the client choice in `get_chat_model` (`local_provider.py:122`).

Every other spelling falls through to the Ollama branch. An operator who writes
`mode: OpenAI_Compat` gets a `ChatOllama` client pointed at `http://localhost:11434`, with
no log line and no error. The failure surfaces much later as a request-time 404 on an
Ollama route. The same is true for `OPENAI_COMPAT`, `openai-compat`, and `vllm`.

No seam validates the value before it reaches the provider. Four seams copy the operator's
`mode` into `extra_kwargs["local_mode"]`, and none of them lower-cases, strips, or
whitelists it:

- `src/interfaces/chat_app/app.py:182`, inside `_build_provider_config_from_payload`
- `src/archi/providers/__init__.py:275`, inside `get_model`
- `src/archi/pipelines/agents/base_react.py:1341`, inside `_build_provider_config`
- `src/data_manager/collectors/processing.py:1014`, inside `_resolve_provider_config`

Only `resolve_local_mode` at `src/bin/benchmark_sut.py:27` lower-cases, and that
normalization serves the benchmark runner alone — it never reaches the four seams above.

The documentation only ever shows the two canonical lower-case values
(`docs/docs/models_providers.md:105,120`, `docs/docs/configuration.md:441,858`,
`docs/docs/quickstart.md:79`), so a non-canonical value is always operator error. The only
question is whether it fails loudly or silently. Today it fails silently.

This is pre-existing. It is not caused by #450 or PR #462 — `git show
origin/dev:src/archi/providers/local_provider.py` carries the same exact comparison.

## What Changes

- **Add** `src/utils/local_mode.py`: the single whitelist, a `canonical_local_mode`
  function, and an `apply_local_mode` seam helper. It goes under `src/utils/` rather than
  next to the provider because importing any `src.archi.providers.*` submodule runs that
  package's `__init__.py`, which pulls LangChain — and `src/data_manager/collectors/
  processing.py:775-784` deliberately imports the provider lazily to stay importable where
  LangChain is absent. `src/utils/` is LangChain-free and `processing.py` already loads it.
- **Canonicalize** the mode: strip surrounding whitespace and lower-case it, then accept
  only `ollama` and `openai_compat`.
- **Refuse** an explicitly set mode outside that pair with a `ValueError` that names both
  the value it got and the valid values, mirroring the `provider_type` precedent at
  `src/archi/providers/__init__.py:260-266`.
- **Keep** an absent or null mode working: it stays the documented `ollama` default and
  never raises. This closes a second, pre-existing hole: `LocalProvider.local_mode`
  (`local_provider.py:99`) defaults to `ollama` only when the key is **missing**, so a key
  present holding `None` reads as `None` and sends `list_models` (`:177`) and
  `validate_connection` (`:266`) down their non-Ollama branches.
- **Wire** all four config seams and `LocalProvider.__init__` to the shared helper, and
  re-point `_is_openai_compat` at the canonical constant so one string test serves both
  the endpoint rule and the client choice.
- **Delegate** the explicit-value branch of `resolve_local_mode` to the same whitelist, so
  the repository holds one whitelist rather than two.
- **Update** the docs that show `mode:` and reconcile the two now-superseded scenarios in
  the unarchived `fix-issue-450-ollama-host-scope` change.

### Behavior change

A deployment carrying an unrecognized `mode` that silently ran Ollama will now fail at
construction with a named error. This is deliberate and is the point of the change: such a
deployment is already broken, because it serves an Ollama client to an operator who asked
for an OpenAI-compatible server. A named error at construction beats a request-time 404.

### Out of scope

The four seams also disagree about **precedence**: `app.py:182` and `base_react.py:1341`
overwrite an existing `extra_kwargs["local_mode"]`, while `__init__.py:274` and
`processing.py:1013` guard with `"local_mode" not in extra_kwargs` and let the explicit
`extra_kwargs` value win. That divergence is pre-existing and orthogonal to spelling. This
change preserves each seam's current precedence exactly and does not unify it. See
`design.md` for why, and file a separate issue if the divergence should be settled.

## Impact

- Affected specs: `local-provider-endpoint-resolution` (ADDED — the capability is not in
  `openspec/specs/`; it lives only in the unarchived `fix-issue-450-ollama-host-scope`
  change directory)
- Affected code: `src/utils/local_mode.py` (new),
  `src/archi/providers/local_provider.py`, `src/archi/providers/__init__.py`,
  `src/archi/pipelines/agents/base_react.py`,
  `src/data_manager/collectors/processing.py`, `src/interfaces/chat_app/app.py`,
  `src/bin/benchmark_sut.py`
- Affected tests: `tests/unit/test_local_provider_env_override.py` — five predating tests
  assert that `mode: vllm` resolves an Ollama endpoint at construction. That is the
  behavior this change removes, so they are restated against a null mode. They are named
  in `tasks.md` 2.1 and must be listed in the PR body.
- Affected docs: `docs/docs/configuration.md`, `docs/docs/models_providers.md`
- Closes #463
