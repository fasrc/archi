# Build a real judge client for the huggingface RAGAS evaluator

## Why

`Benchmarker.get_ragas_llm_evaluator` (`src/bin/service_benchmark.py:1363`) selects the RAGAS
judge client from the configured evaluator provider. Its `huggingface` arm never returns a
client. At `origin/dev` `c6112167`, `src/bin/service_benchmark.py:1415-1419`:

```python
case "huggingface":
    base_url = ollama_url or "http://localhost:8000/v1"
    return get_model(
        "local", model_name, base_url=base_url, local_mode="openai_compat"
    )
```

`get_model` declares `provider_config` as a required positional parameter
(`src/archi/providers/__init__.py:241`):

```python
def get_model(
    provider_type: str | ProviderType, model_name: str, provider_config: dict, **kwargs
):
```

The arm passes `base_url` and `local_mode` as keyword arguments and supplies no
`provider_config`, so Python raises before the function body runs.

**Measured on this branch** (`origin/dev` `c6112167`, 2026-09-10). Three configuration routes
reach the arm, and all three raise:

```
configured url:   TypeError: get_model() missing 1 required positional argument: 'provider_config'
no url (default): TypeError: get_model() missing 1 required positional argument: 'provider_config'
SUT key fallback: TypeError: get_model() missing 1 required positional argument: 'provider_config'
```

There is **one** reachable route: `mode_settings.ragas_settings.evaluator_provider:
huggingface`. It is not dead code — `src/cli/templates/base-config.yaml:64` renders
`evaluator_provider` into every generated deployment config, so it is a supported operator
surface.

The arm at `service_benchmark.py:1371-1377` also falls back to the system-under-test key
`services.benchmarking.provider`, and an earlier draft of this proposal counted that as a
second route. It is not one, for `huggingface`. `ProviderType` has no such member and
`BaseReactAgent.__init__` calls `get_model` at construction, so
`services.benchmarking.provider: huggingface` raises while `load_new_configuration` builds
the system under test — before a judge exists. Caught in review round 1; the shared fallback
stays as it is, because it is correct for every other evaluator provider.

**The blast radius is latent, not live.** No shipped configuration selects `huggingface`:
`docs/docs/benchmarking.md:449` and both files under
`examples/benchmarking/hierarchical_rerank_ab/` use `evaluator_provider: huit_bedrock`
(checked 2026-09-10). The first operator who writes `huggingface` gets the crash, and gets it
at judge-construction time — after the deploy and the ingest, with the benchmark run already
under way.

**The intended shape is two `case` arms above.** The `local` arm at
`service_benchmark.py:1397-1405` calls the same function correctly:

```python
return get_model(
    "local",
    model_name,
    {"base_url": ollama_url, "mode": "openai_compat"},
)
```

The dictionary key is `mode`, not `local_mode`. `get_model` translates `mode` into
`extra_kwargs["local_mode"]` at `src/archi/providers/__init__.py:270-276`, and only for LOCAL
providers. So the broken arm already names the right provider, the right mode, and the right
base URL; only the call shape is wrong.

## What Changes

- **One call in `src/bin/service_benchmark.py`.** The `huggingface` arm passes its
  configuration dictionary positionally, in the form the `local` arm two arms above already
  uses: `get_model("local", model_name, {"base_url": base_url, "mode": "openai_compat"})`.
  The local `base_url` variable stays, because it carries the arm's
  `http://localhost:8000/v1` default; `ollama_url` is not copied from the `local` arm, since
  it can be `None` here and supplying that default is the reason the variable exists.
- **Four tests appended to `tests/unit/test_ragas_evaluator_local_mode.py`**, covering the
  configured judge URL, the `http://localhost:8000/v1` default, the SUT `provider` fallback
  route, and the inherited SUT `ollama_url`. That file already builds a `Benchmarker` through
  `object.__new__` and a hand-made `self.config`; its `_bench` helper is reused unchanged.
- **One paragraph in `docs/docs/benchmarking.md`**, in the "Judge/SUT split" section
  (`:435`), recording that `huggingface` is a valid `evaluator_provider` naming an
  OpenAI-compatible endpoint and that it defaults to `http://localhost:8000/v1`.
  `AGENTS.md:53` asks for the docs update in the same change as user-visible configuration
  behaviour, and the section today names only `huit_bedrock`.

Not in this change, each with a reason in `design.md`:

- The arm keeps forcing `openai_compat` rather than calling `resolve_local_mode`. Routing it
  through that helper would resolve to native `ollama` whenever no URL is configured, which
  inverts the arm's own meaning (D3).
- No change to `get_model`'s signature, and no keyword-argument compatibility shim (D2).
- No new validation of a judge URL that points at a native Ollama port (D4).

## Capabilities

### New Capabilities

- `ragas-judge-client`: how the RAGAS judge LLM client is built from the `evaluator_*`
  configuration block. No capability in `openspec/specs/` owns this today. The nearest
  candidates were read and rejected: `retrieval-benchmarking` specifies the two-arm A/B
  protocol and names the judge only as a variable to hold constant across arms
  (`openspec/specs/retrieval-benchmarking/spec.md:11`), and `benchmark-run-resilience`
  specifies per-question failure isolation inside a run, not client construction before it.

### Modified Capabilities

None. The archived change `2026-06-03-adopt-argilla-benchmark-platform` carries a requirement
for the `evaluator_*` keys in its own directory
(`openspec/changes/archive/2026-06-03-adopt-argilla-benchmark-platform/specs/argilla-benchmark-grading/spec.md:44`),
but that capability was never promoted — `openspec/specs/argilla-benchmark-grading` does not
exist and `openspec spec list` does not report it (checked 2026-09-10). A MODIFIED delta
against it would not validate, so this change uses ADDED against a new capability instead.

## Impact

- `src/bin/service_benchmark.py` — one line inside one `case` arm. No signature, no control
  flow, and no other arm changes.
- `tests/unit/test_ragas_evaluator_local_mode.py` — four tests appended. The file has 3 tests
  and 64 lines today; no existing test changes.
- `docs/docs/benchmarking.md` — one paragraph added to "Judge/SUT split". Prose only.
- **Measured behaviour after the fix**, taken by patching the arm, importing the module, and
  reading the built client (2026-09-10; the patch was reverted and `git status` is empty):

  | route | before | after |
  |---|---|---|
  | `evaluator_ollama_url: http://judge-host:8001/v1` | `TypeError` | `ChatOpenAI` base `http://judge-host:8001/v1`, model `judge-x` |
  | no judge URL and no SUT URL | `TypeError` | `ChatOpenAI` base `http://localhost:8000/v1`, model `judge-x` |
  | SUT `ollama_url: http://sut-host:9000/v1` inherited, judge key set | `TypeError` | `ChatOpenAI` base `http://sut-host:9000/v1`, model `qwen-x` |

  The three existing tests in `tests/unit/test_ragas_evaluator_local_mode.py` pass unchanged
  against the patched file (`3 passed`), so no other arm moves.

  A fourth row measured in the first draft — the system-under-test key on its own — has been
  removed rather than corrected. It was taken through `object.__new__(Benchmarker)`, which
  skips the construction that rejects the provider, so the number was real but the
  configuration behind it was not.
- **Patch coverage does protect this change.** The gate measures `--cov=src`
  (`scripts/gate.sh`) and the changed line is under `src/`, so `diff-cover` scores it. Every
  changed line is executed by the tests added in task 1.1.
- **Formatting is stable.** `src/bin/service_benchmark.py` and
  `tests/unit/test_ragas_evaluator_local_mode.py` are both black 24.10.0 and isort 6.0.1 clean
  today. The replacement line is exactly 88 characters, black's limit, and
  `black --check` leaves the patched copy unchanged (measured 2026-09-10) — so the edit does
  not reflow the file and the diff stays at one line.
- **No open pull request touches any file this change edits.** Checked by file list on
  2026-09-10 against the three open PRs: #453 (`bd2ee627`, OpenTelemetry), #455 (`6d4550ef`,
  issue #442), #456 (`2e1d9c35`, issue #448). The nearest neighbour is #456, which edits
  `src/cli/templates/base-config.yaml` — the template that renders `evaluator_provider` — but
  its diff does not touch the three `evaluator_*` lines and it shares no file with this
  change.
