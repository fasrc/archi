## Context

Issue #730 is PR 1 of `openspec/changes/offload-ragas-judge-to-slurm/` (tasks 1.1–1.4,
design D2 and D6). That change is the design of record. This document records only the
facts that make the move safe, measured on `origin/dev` `7145888d` (2026-10-21). The
anchors in the issue body (at `7c7d7450`) still hold, except that `callbacks=[recorder]`
is at `:2269` and `recorder.snapshot()` is at `:2279`.

What moves (`src/bin/service_benchmark.py`):

- `get_ragas_llm_evaluator` `:1913-1984`. It sets `self._ragas_judge_identity` at
  `:1915` (#618), then builds the judge in a provider `match`.
- `get_ragas_embedding_model` `:1986-1998`. An unknown value falls back to
  `OpenAIEmbeddings()` at `:1997-1998`.
- `get_ragas_results` `:2200-2279`: the lazy `ragas` imports `:2219-2225`, the enabled
  metrics `:2227-2233`, `batch_size` `:2241`, `RunConfig` `:2246`, the judge and the
  embeddings `:2247-2248`, `UsageRecorder` `:2249-2250` (#585), `_ragas_scored`
  `:2253-2256` (#518), the per-metric `evaluate` `:2262-2272`, and the `finally` that
  stores `self._judge_usage` `:2278-2279`.
- The only caller is `_process_config` `:2366-2369`. It does not catch an exception from
  `get_ragas_results`, and neither does `run()` (`:2550-2582`), so a raise aborts the run.

On 2026-10-21, black 24.10.0 and isort 6.0.1 report `src/bin/service_benchmark.py` clean,
so an in-place edit does not reflow the file.

## Goals / Non-Goals

**Goals:** move the scoring into `src/utils/ragas_scoring.py` with no change in
behavior; keep `Benchmarker`'s three methods as thin wrappers; add the Unix-socket judge
builder; keep every existing test green with no edit.

**Non-Goals:** `judge_mode`, bundles, publish, scorer, merge, Slurm (parent groups 2–5);
any change to result JSON keys, `handle_results`, `ResultHandler`, `run()` or
`_process_config`; any change to `LocalProvider`.

## Decisions

### D1. This folder is a driver; the parent change is the design of record

The nightly supervisor reads its tasks from
`openspec/changes/fix-issue-<n>-<slug>/tasks.md`. Issue #730 forbids a new design. As a
result, this change holds the task list and two requirements that pin PR 1 behavior, and
nothing else. The PR ticks tasks 1.1–1.4 in the parent `tasks.md` and corrects the parent
design D2 to the narrower scope of the issue (the pure helpers stay in
`src/utils/benchmark_schema.py`; the three side effects stay on `Benchmarker`).

The issue's commands name the branch `feat/ragas-scoring-extract`. The supervisor
requires `fix/issue-730-ragas-scoring-extract`, so the PR uses that name.

### D2. The test patch points stay in `service_benchmark`

The existing tests patch names in the `service_benchmark` namespace, and replace two
methods on the instance:

| Test | What it replaces |
|---|---|
| `test_benchmark_judge_identity.py:309` | `src.bin.service_benchmark.ChatOpenAI` |
| `test_benchmark_judge_identity.py:319`, `:333`, `:375`, `:402`, `:418`, `:434`, `:448`, `:505`, `:530`, `:550`, `:567`, `:584`, `:601` | `src.bin.service_benchmark.get_model` |
| `test_benchmark_judge_identity.py:480`, `:501`, `:525`, `:547`, `:564`, `:581`, `:598` | `src.bin.service_benchmark.ragas_judge_effective_identity` |
| `test_benchmark_ragas_dialect.py:91-92` | `bench.get_ragas_llm_evaluator`, `bench.get_ragas_embedding_model` (instance attributes) |
| `test_benchmark_ragas_dialect.py:329` | `sys.modules["ragas"].evaluate` |

A plain move would make the new module import its own `ChatOpenAI` and `get_model`, and
these patches would miss. As a result:

- The module's builders take the constructors as keyword arguments:
  `build_ragas_judge_llm(identity, *, chat_openai, get_model)` and
  `build_ragas_embeddings(embedding_model, *, openai_embeddings, huggingface_embeddings)`.
  The provider `match` and the embedding `match` move unchanged, except that they call
  the injected names.
- The wrappers pass the `service_benchmark` module globals (`ChatOpenAI`, `get_model`,
  `OpenAIEmbeddings`, `HuggingFaceEmbeddings`) at call time, so a patch on those names
  still applies.
- `get_ragas_llm_evaluator` keeps the call to `ragas_judge_effective_identity` and the
  assignment to `self._ragas_judge_identity` in `service_benchmark`.
- `get_ragas_results` passes the bound methods `self.get_ragas_llm_evaluator` and
  `self.get_ragas_embedding_model` as the judge factory and the embeddings factory. A
  lookup on `self` at call time finds an instance attribute that a test set.
- The module keeps the lazy imports inside the scoring function: `from ragas import
  EvaluationDataset, RunConfig, evaluate`, `importlib.import_module("ragas.metrics")`,
  and the two wrapper imports. The `sys.modules` stub depends on them.
- `ragas_judge_identity` and `ragas_judge_effective_identity` stay in
  `service_benchmark.py` (`:161`, `:177`). The wrapper calls `ragas_judge_identity`
  and passes the provider and model to the module for the `UsageRecorder`.

### D3. The judge and the embeddings are built before scoring, as today

Today `get_ragas_results` builds the judge and the embeddings (`:2247-2248`) before it
knows whether any metric has an eligible row. As a result, an arm whose rows are all
drafts still sets `_ragas_judge_identity`. The module calls both factories eagerly, at the
same point and in the same order: ragas imports, metric objects, `RunConfig`, judge,
embeddings, recorder, then scoring. A lazy factory call would change that arm's
`_ragas_judge_identity`.

### D4. The module reports its side effects through an outcome object

The module function fills a small mutable dataclass that the caller supplies, for
example:

```python
@dataclass
class RagasScoreOutcome:
    aggregates: Dict[str, Any] = field(default_factory=dict)
    scored: bool = False
    usage: Optional[Dict[str, Any]] = None
```

The module sets `scored = True` inside `score_fn` (where `:2256` sets it today) and sets
`usage = recorder.snapshot()` in a `finally` (where `:2279` does today). The wrapper
copies `outcome.scored` to `self._ragas_scored` and `outcome.usage` to
`self._judge_usage` in its own `finally`, and returns `outcome.aggregates`. After a normal
return the attributes have today's values. A raise aborts the run (see Context), so no
caller reads them on that path. The wrapper still assigns them in a `finally`, to keep the
shape of today's code.

The wrapper keeps reading each setting from its current source:
`enabled_metrics` from `self.benchmarking_configs[...]` (`:2227`), `ragas_settings` from
`self.config["services"]["benchmarking"]["mode_settings"]["ragas_settings"]` (`:2235`),
and `verbosity` from `self.config.get("global", {})` (`:2240`). The module takes the three
values as arguments.

### D5. The characterization test uses only names that exist before and after the move

`tests/unit/test_ragas_scoring_characterization.py` scores fixed rows through
`Benchmarker.get_ragas_results`. It must pass on the commit before the move and on the
commit after it, with no edit. As a result, it:

- never imports `src.utils.ragas_scoring` (absent before the move);
- builds the bench with `object.__new__(Benchmarker)` (the pattern of
  `test_benchmark_ragas_dialect.py:77` and `test_benchmark_judge_identity.py:298`), sets
  `config` and `benchmarking_configs`, and does NOT replace `get_ragas_llm_evaluator` or
  `get_ragas_embedding_model`, so the real judge path runs and sets
  `_ragas_judge_identity`;
- patches only `src.bin.service_benchmark.ChatOpenAI` and
  `src.bin.service_benchmark.OpenAIEmbeddings` (judge provider `openai`, embedding model
  `openai`), and installs its own copy of the `sys.modules` ragas stub of
  `test_benchmark_ragas_dialect.py:28-74` (copied, not imported from that test module);
- has a stub `evaluate` that returns fixed, different per-row scores and fires
  `on_llm_end` with fixed token counts on every callback (the pattern of
  `test_benchmark_ragas_dialect.py:307-357`), and that records the keyword arguments it
  received.

It has two cases:

1. Two metrics, one answer metric and one context metric, over three rows where one row
   is a draft (empty `reference`). Assert, with exact values: the returned aggregates
   dict (every `aggregate_<metric>` and `<metric>_scored`), the per-row scores written
   onto `results_by_key`, `_ragas_scored is True`, the full `_judge_usage` dict,
   `_ragas_judge_identity == ragas_judge_effective_identity(bench.config)` as a literal
   dict, and the `batch_size`, `run_config` fields and the single `UsageRecorder` in the
   `callbacks` that `evaluate` received.
2. Only a context metric, and every row is a draft. Assert the `n/a` aggregate,
   `_ragas_scored is False`, `_judge_usage is None`, that `evaluate` was never called,
   and that `_ragas_judge_identity` is set (D3).

### D6. The Unix-socket judge builder lives in the new module

`LocalProvider` has no transport hook (`src/archi/providers/local_provider.py:163-181`;
it sends `api_key = self._api_key or "not-needed"` at `:176`). The new module builds the
client directly, and `LocalProvider` does not change:

```python
def build_uds_judge_llm(model, socket_path, *, api_key_env="VLLM_API_KEY",
                        chat_openai=ChatOpenAI):
```

- It reads the key from `os.environ[api_key_env]`. The function has no parameter for the
  key value, so the key cannot come from a command line or a config file.
- An unset or empty variable raises `ValueError` that names the variable (never a value),
  before any client exists. With no key, the client cannot carry one, and a silent
  keyless client would remove the second guard of parent D6.
- It returns `chat_openai(model=model, base_url="http://localhost/v1", api_key=key,
  http_client=httpx.Client(transport=httpx.HTTPTransport(uds=socket_path)),
  http_async_client=httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=socket_path)))`.
  The host in `base_url` is a placeholder: the transport connects to the socket and opens
  no TCP connection.

The test starts a small threaded HTTP server on a Unix socket (`socketserver.
UnixStreamServer` with an `http.server.BaseHTTPRequestHandler`). The handler records the
method, the path and the `Authorization` header, and returns a minimal `chat.completion`
JSON body. The test sets `VLLM_API_KEY` with `monkeypatch.setenv`, calls
`llm.invoke(...)`, and asserts one `POST /v1/chat/completions` with
`Authorization: Bearer <the env value>`, and the returned content. A second test unsets
the variable, expects `ValueError`, and asserts that the server got no request.

- Put the socket under `tempfile.mkdtemp(prefix="rj")`, not `tmp_path`: an `AF_UNIX`
  path has a limit of 108 bytes, and a pytest `tmp_path` with a long test name can pass
  it. Remove the folder in teardown.
- Bound every wait: give the client a `timeout` of 10 seconds and `max_retries=0`, call
  `server.shutdown()` and `server.server_close()` in a `finally`, and join the thread with
  `timeout=5`. `pytest-timeout` is not installed, so a hang would stall the whole loop.

### D7. Moved lines are new lines for the coverage gate

The gate measures patch coverage with `--cov=src` at `--fail-under=80`. Every line of
`src/utils/ragas_scoring.py` is new to `diff-cover`. Today no test reaches the
`anthropic`, `huit_bedrock` and default (`_`) branches of the judge `match`, or any branch
of the embedding `match`. `tests/unit/test_ragas_scoring.py` calls
`build_ragas_judge_llm` and `build_ragas_embeddings` directly with stub constructors (and
`unittest.mock.patch("langchain_anthropic.ChatAnthropic")` for the lazy import), one test
for each branch.

## Risks / Trade-offs

- [A wrapper reads a moved global from the wrong namespace, and a judge-identity test
  then builds a real client] → D2 lists every patch point. The three existing test files
  must pass with no edit (task 2.1 checks `git diff` on them).
- [The characterization test asserts too little to catch a change] → D5 asserts exact
  values for every output and side effect, and the kwargs that `evaluate` receives.
- [The threaded socket server hangs pytest] → D6 bounds every wait.
- [A reviewer reads the driver folder as a second design] → D1 and the proposal say that
  the parent change is the design of record. The PR body says it again.

## Migration Plan

One PR into `dev`. No config, schema or result-format change, so there is no migration
and no deploy step. Revert the PR to roll back.

## Open Questions

None. The issue body answers task 1.1 and the scope; the code answers the rest.
