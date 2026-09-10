# Design — a working judge client for the huggingface evaluator

## Context

`Benchmarker.get_ragas_llm_evaluator` (`src/bin/service_benchmark.py:1363`) is the single seam
that turns the `evaluator_*` configuration block into the LangChain client RAGAS scores with.
It reads three values at `:1371-1377` — provider, model, and URL — each falling back from the
judge-specific `ragas_settings.evaluator_*` key to the system-under-test key. It then dispatches
on the provider name in a `match` statement.

Issue #73 added the `local` arm's OpenAI-compatible bridge: a judge endpoint ending in `/v1`
is a vLLM-style server, so the arm must build `ChatOpenAI` rather than `ChatOllama`, which
404s against `/v1`. That arm calls `get_model` with its configuration as a positional
dictionary (`:1401-1405`).

The `huggingface` arm one arm below (`:1415-1419`) was written to do the same thing and calls
`get_model` with keyword arguments instead. `get_model` requires `provider_config`
positionally (`src/archi/providers/__init__.py:241`), so the arm raises `TypeError` before any
client is built. It has never worked.

Two facts bound the work:

1. **The arm's intent is already recorded in its own code.** It names provider `local`, mode
   `openai_compat`, and a default of `http://localhost:8000/v1`. Nothing about the intent is
   ambiguous; only the call shape is wrong. So this is a repair, not a design decision about
   what `huggingface` should mean.
2. **Nothing in the repository selects it yet.** `huit_bedrock` is the judge in the docs and
   in both example configs. So the fix cannot regress a live run, and there is no existing
   behaviour to preserve beyond the other arms.

## Goals / Non-Goals

**Goals**

- The `huggingface` arm returns a `ChatOpenAI` client for every configuration route that
  reaches it, instead of raising.
- The judge base URL is the configured one when set, and `http://localhost:8000/v1` when not.
- The tests bind to the defect: reverting the source line makes at least one of them fail with
  the `TypeError`.
- The other arms of the `match` statement are untouched, and the three existing tests in
  `tests/unit/test_ragas_evaluator_local_mode.py` pass unchanged.

**Non-Goals**

- Redesigning what `huggingface` means as an evaluator provider. It means "an
  OpenAI-compatible endpoint" today and it keeps meaning that.
- Making `get_model` accept keyword arguments (D2).
- Validating that the judge URL is reachable, or that it is not a native Ollama port (D4).
- Any change to the SUT path, to `resolve_local_mode`, or to the `local` arm.
- Loading a HuggingFace model in-process. The arm never did that, and the name refers to the
  serving convention, not to the `transformers` library.

## Decisions

### D1 — Fix the call site, not the callee

The defect is one wrong call shape at one site. `get_model` is called correctly everywhere
else, including two arms above in the same function, so the callee is not at fault and the
repair belongs at the call site.

The target code is exactly:

```python
case "huggingface":
    base_url = ollama_url or "http://localhost:8000/v1"
    return get_model(
        "local", model_name, {"base_url": base_url, "mode": "openai_compat"}
    )
```

The dictionary key is `mode`, not `local_mode`. `get_model` reads `provider_config["mode"]`
and copies it into `extra_kwargs["local_mode"]` for LOCAL providers only
(`src/archi/providers/__init__.py:270-276`). Writing `local_mode` into the dictionary would
type-check, pass no value through, and silently build a native Ollama client — a plausible
wrong fix that the tests in task 1.1 catch, because they assert the client type and its base
URL rather than merely asserting no exception.

The local `base_url` variable stays. `ollama_url` is not copied from the `local` arm: it can
be `None` here, and supplying the `http://localhost:8000/v1` default is the only reason the
variable exists in this arm.

### D2 — No compatibility shim on `get_model`

An alternative repair is to give `get_model` a keyword-argument path, so the existing call
starts working without editing the arm. Rejected. `provider_config` is required positionally
by every other caller, a shim would add a second supported call shape to a function on the
provider seam, and it would leave the arm's `local_mode` key in the code — the exact spelling
that means nothing inside a `provider_config` dictionary. The narrow fix removes the wrong
spelling instead of legitimising it.

### D3 — The arm keeps forcing `openai_compat` instead of calling `resolve_local_mode`

The `local` arm decides its mode by calling `resolve_local_mode(ollama_url, explicit_mode)`
(`src/bin/benchmark_sut.py:18-29`): an explicit value wins, otherwise a URL ending in `/v1`
means `openai_compat` and anything else means native `ollama`. Making the `huggingface` arm
symmetric with it looks tidy and is wrong.

`resolve_local_mode(None, None)` returns `"ollama"` — the helper coerces a missing URL to
`""`, which does not end in `/v1`. The `huggingface` arm's whole purpose is to name an
OpenAI-compatible endpoint and to default to one at `http://localhost:8000/v1`, so an
unconfigured judge would resolve to a native Ollama client against no URL at all. That is a
worse failure than today's crash, because it fails at scoring time rather than at
construction time.

Resolving on `base_url` rather than `ollama_url` would return `openai_compat` for the default,
but it adds a branch whose only reachable answer for the default is the constant the arm
already hard-codes. The arm keeps the explicit mode.

### D4 — A judge URL pointing at a native Ollama port stays unvalidated

An operator who writes `evaluator_provider: huggingface` with
`evaluator_ollama_url: http://host:11434` gets a `ChatOpenAI` client against a native Ollama
server, which 404s. That follows from D3 and is pre-existing intent — the arm has always
hard-coded `openai_compat`. It is stated here rather than fixed, because deciding whether the
arm should warn, refuse, or auto-detect is a design question the issue does not answer, and
answering it unattended would be inventing a requirement.

### D5 — A new capability rather than a delta on an existing one

`openspec/specs/` has sixteen capabilities and none of them owns judge-client construction.
`retrieval-benchmarking` names the RAGAS judge once
(`openspec/specs/retrieval-benchmarking/spec.md:11`) and only as a variable to hold identical
across the two arms of an A/B comparison; `benchmark-run-resilience` covers per-question
failure isolation inside a run, which is a different phase.

The archived change `2026-06-03-adopt-argilla-benchmark-platform` does specify the
`evaluator_*` keys, in a capability named `argilla-benchmark-grading`. That capability was
never promoted into `openspec/specs/` — the directory does not exist and `openspec spec list`
does not report it (checked 2026-09-10). A MODIFIED delta against an unpublished capability
does not validate, so this change declares `ragas-judge-client` as a new capability and uses
ADDED. This is the same trap that the `dependency-pin-hygiene`, `cli-create-preflight`, and
`benchmark-run-provenance` deltas each hit before.

### D6 — The docs change is one paragraph in the Judge/SUT split section

`docs/docs/benchmarking.md:435` is the operator-facing section for the `evaluator_*` block. It
names `huit_bedrock` and nothing else, so an operator has no way to learn that `huggingface`
is accepted or what URL it defaults to. `AGENTS.md:53` requires the docs update in the same
change as the configuration behaviour. One paragraph after the `huit_bedrock` paragraph is
enough; no example, table, or procedure elsewhere on the page changes.

The section is not touched by any open PR — #455 edits
`docs/docs/interpreting_benchmark_results.md`, a different file, and #453 edits
`docs/docs/observability.md` and `docs/mkdocs.yml` (checked 2026-09-10).

## Risks / Trade-offs

- **A wrong fix passes the gate.** Writing `local_mode` into the dictionary, or dropping the
  `mode` key, returns a client and raises nothing — the call stops crashing while the judge
  quietly becomes a native Ollama client. Mitigation: the tests assert the concrete client
  type and its `openai_api_base`, not the absence of an exception. Task 1.1 also requires
  reverting the source line and watching the test fail with the `TypeError`, which is what
  proves the tests bind to this defect rather than to any client at all.
- **The `_bench` helper hides a spread order.** `_bench` builds
  `{"mode_settings": {"ragas_settings": {}}, **benchmarking}`
  (`tests/unit/test_ragas_evaluator_local_mode.py:21-25`), so a test that passes its own
  `mode_settings` replaces the empty default rather than merging into it. That is the correct
  way to set `evaluator_provider` through this helper, and the tasks spell the shape out so
  nobody adds a second helper.
- **Appending to the test file can swallow the last assertion.** The file's current final line
  is `    assert isinstance(llm, ChatOpenAI)` at line 64. New tests go strictly after it, and
  that line must survive as trailing context in the diff. Nightly runs have inserted tests
  above a file's final line before and silently deleted the previous test's last assertion.
- **A reused test name deletes a test.** The gate runs no linter, so a duplicated
  `def test_...` overwrites the earlier one while staying green. The file collects 3 tests
  today; task 1.1 and task 1.2 each require reading the collected count.
- **The fix is unverified against a real server.** Every measurement here is in-process client
  construction; no HTTP request is made, and no vLLM or TGI endpoint was contacted. The change
  proves the arm builds the right client at the right URL, and claims nothing about whether a
  given endpoint answers. A live judge run is a `needs-deploy` activity and is out of scope.
