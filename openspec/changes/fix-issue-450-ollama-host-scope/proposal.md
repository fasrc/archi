## Why

`OLLAMA_HOST` rewrites the `base_url` of every `local` provider, in both local modes
(`src/archi/providers/local_provider.py:58-76`). An operator who exports the variable to
point at an Ollama daemon silently redirects every vLLM `openai_compat` chat request to
that daemon, which does not serve the OpenAI route. Issue #450.

Verified at `origin/dev` @ `143ee248`. A declared `base_url=http://gpu-vllm:8000/v1` with
`local_mode=openai_compat` resolves to `http://ollama-box:11434` when `OLLAMA_HOST` is set.
The mode stays `openai_compat`, so the provider still builds a `ChatOpenAI` client and
points it at an Ollama daemon. The configured vLLM host never sees the request.

## What Changes

- Read `local_mode` from the incoming `ProviderConfig` before the environment override
  applies, and apply the `OLLAMA_HOST` override only in `ollama` mode.
- Make the "no configured `base_url`" fallback mode-aware: `openai_compat` falls back to
  `DEFAULT_OPENAI_COMPAT_BASE_URL`, not to the Ollama default or to `OLLAMA_HOST`.
- **BREAKING (test-visible, one benchmark path):** two existing tests in
  `tests/unit/test_ragas_evaluator_local_mode.py` pin the old endpoint values and change
  with this fix. Both cover a `local`/`openai_compat` RAGAS judge with no configured judge
  URL, which is the only code path that reads the constructor fallback directly:
  - `test_local_openai_compat_judge_without_a_url_keeps_the_provider_default`
    `http://localhost:11434` → `http://localhost:8000/v1`
  - `test_local_openai_compat_judge_without_a_url_still_inherits_ollama_host`
    `http://sut-host:9000/v1` → `http://localhost:8000/v1`
  The second one pins the defect itself. Its own docstring elsewhere in that file calls
  the behavior a hijack: "without the kwarg the judge scores its own answers against the
  system under test". After this change a URL-less judge uses its own default endpoint
  instead of the system under test, so the suite stops encoding the self-scoring path.
- Keep `OLLAMA_HOST` fully effective in `ollama` mode. The CI smoke path depends on it
  (`scripts/dev/run_smoke_preview.sh:252-253,414`, `tests/smoke/combined_smoke.sh:48,61`).
- Correct two comments in `src/bin/service_benchmark.py` that state the overwrite as
  current behavior. Comment text only; the double-`base_url` call shape stays.

## Capabilities

### New Capabilities

- `local-provider-endpoint-resolution`: how a `local` provider resolves its `base_url`
  from the configured value, the `OLLAMA_HOST` environment variable, and the per-mode
  defaults — and which of those wins in each local mode.

`openspec/specs/` holds no provider capability today, so this is ADDED, not MODIFIED.

### Modified Capabilities

None.

## Impact

- `src/archi/providers/local_provider.py` — the `__init__` endpoint-resolution branch
  (lines 53-77). 255 lines, clean under black 24.10.0 and isort 6.0.1, so a small edit
  will not reflow the file.
- Production reach: the constructor is entered through the provider registry, not a direct
  call. `get_provider()` instantiates it at `src/archi/providers/__init__.py:136`, and
  `get_provider_with_api_key()` at line 346. `get_model()` translates a config `mode` key
  into `extra_kwargs["local_mode"]` at lines 269-274, before construction — so the mode is
  readable from the incoming config on the production path.
- `tests/unit/test_ragas_evaluator_local_mode.py` — two assertions updated, with the
  reason recorded in each docstring. The other 12 tests in the file pass an explicit
  `base_url` keyword that already outranks the environment, so they do not change.
- New `tests/unit/test_local_provider_env_override.py`.
- No deployment is broken today: nothing under `deploy/` sets `OLLAMA_HOST`, and
  `src/cli/templates/base-compose.yaml:234` renders an unset variable as the empty string,
  which `normalize_base_url` returns falsy. Each host is protected only by the variable
  staying unset.
- Out of scope (separate defects in the same file, left alone): the ignored per-call
  `base_url` in Ollama mode, `list_models()` never querying `/v1/models` in
  `openai_compat` mode, and `ChatOllama` dropping `extra_body`.
