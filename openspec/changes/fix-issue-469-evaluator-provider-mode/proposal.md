# Render the judge's own local mode into the RAGAS block

## Why

PR #467 taught the RAGAS judge to read a judge-specific local mode.
`src/bin/service_benchmark.py:1424` now reads `evaluator_provider_mode` and only falls
back to the SUT's `provider_mode` when the judge's own key is absent or the empty string:

```python
evaluator_mode = ragas_configs.get("evaluator_provider_mode")
explicit_mode = (
    evaluator_mode
    if evaluator_mode not in (None, "")
    else benchmark_cfg.get("provider_mode")
)
if resolve_local_mode(ollama_url, explicit_mode) == "openai_compat":
```

Nothing writes that key. `src/cli/templates/base-config.yaml:98-100` renders three sibling
keys into the RAGAS block — `evaluator_provider`, `evaluator_model` and
`evaluator_ollama_url` — and omits `evaluator_provider_mode`. A repository-wide search
finds no other render path: every hit is the consumer itself, a unit test that builds
`ragas_configs` in memory, or spec text.

So on the CLI deployment path `ragas_configs.get("evaluator_provider_mode")` is always
`None`. Three consequences follow:

- A judge-specific mode an operator configured is dropped, and the judge silently inherits
  the SUT's mode instead. The judge scores in a dialect nobody asked for.
- An unusable judge mode is never refused, because `resolve_local_mode` never receives it.
- The branch added by commit `d9798fb6` is dead code for every CLI-deployed benchmark.

The unit tests do not catch this. `tests/unit/test_ragas_evaluator_local_mode.py` injects
`ragas_configs` directly and never renders the template, so the consumer is proven correct
against a config the CLI cannot produce.

The judge/SUT split is documented at `docs/docs/benchmarking.md:436-450` as the supported
way to break self-evaluation bias. The mode half of that split is unreachable.

## What Changes

- **Add** `evaluator_provider_mode` to the RAGAS block of
  `src/cli/templates/base-config.yaml`, beside `evaluator_ollama_url`.
- **Copy the SUT key's two render rules**, rather than inventing new ones. The sibling
  `services.benchmarking.provider_mode` at `base-config.yaml:46-61` already solved this
  exact defect class twice on PR #467:
  - `11be4409` — emit **by presence, not by truthiness**:
    `{%- if X is defined and X is not none %}`. A truthiness guard dropped a configured
    `false` and `0` before the validator saw them.
  - `d37afb3a` — render through **`| tojson`, not a bare interpolation**. A JSON scalar is
    valid YAML and carries its own type, so `"null"`, `"Null"`, `"NULL"` and `"~"` no
    longer round-trip to `None` and auto-detect, and `"on"`, `"off"`, `"yes"` and `"no"`
    no longer become booleans and get refused for the wrong reason.
- **Document** the key in the judge/SUT split section of `docs/docs/benchmarking.md`.

### The one asymmetry with the SUT key

For the judge, the empty string means "inherit the SUT's mode", and the consumer at
`service_benchmark.py:1426` already implements that. An absent key means the same thing.
So omitting the key when it is absent or null is correct here for the same reason it is
correct for the SUT key, and the empty string keeps its existing inherit meaning. The
presence guard preserves all three behaviors unchanged.

### Behavior change

A deployment that configures an unusable judge mode and today runs anyway — with the judge
quietly inheriting the SUT's mode — will now fail with a named `ValueError` from
`resolve_local_mode`. That is the point of the change. Such a benchmark is already
producing scores in a dialect the operator did not ask for, and a named error beats a
silently mis-dialected judge.

### Out of scope

`src/bin/service_benchmark.py` is not touched. The consumer is already correct; only the
render is missing.

## Impact

- Affected specs: `local-provider-endpoint-resolution` (**ADDED** — the capability is not
  in `openspec/specs/`; it lives only in the unarchived
  `fix-issue-463-local-mode-canonicalization` and `fix-issue-450-ollama-host-scope` change
  directories)
- Affected code: `src/cli/templates/base-config.yaml`
- Affected tests: `tests/unit/test_base_config_benchmark_render.py` (new tests appended;
  no existing test changes)
- Affected docs: `docs/docs/benchmarking.md`
- Closes #469
