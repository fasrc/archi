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

### D7 — `base_url` is passed twice, as a dictionary entry and as a keyword

Review round 1 found that the positional dictionary alone does not reach `ChatOpenAI`.
`load_new_configuration` exports the system-under-test URL as `OLLAMA_HOST`, and
`LocalProvider.__init__` overwrites `config.base_url` from that variable — so a correct
dictionary still builds a judge pointed at the system under test, which is the original
defect wearing a different mask. `get_model` forwards `**kwargs` to the client after the
provider has resolved its configuration, so the keyword lands last and wins.

The `local` arm sends the keyword conditionally and the `huggingface` arm sends it always.
That asymmetry is deliberate: the `huggingface` arm computes
`base_url = ollama_url or "http://localhost:8000/v1"` and therefore always holds a value,
while the `local` arm's `ollama_url` can be `None`. A `None` keyword would not be a no-op —
it lands last and erases the provider's own local default, sending the judge to the public
OpenAI endpoint. An override with nothing to override with is not an override.

Rejected alternative: stop exporting `OLLAMA_HOST`, or teach `LocalProvider` to ignore it
when a caller supplied an explicit `base_url`. Both change shared provider behaviour that
the system-under-test path depends on, for a judge-construction defect. That belongs in its
own change with its own evidence.

### D8 — `_normalize_base_url` is promoted to a module-level function

The keyword from D7 bypasses `LocalProvider._normalize_base_url`, which prefixes a
scheme-less base URL with `http://`. An operator who writes `evaluator_ollama_url:
judge-host:8001/v1` would get a client whose transport cannot resolve the address, failing
on the first judge request — a new defect introduced by the fix for the old one. Found in
review round 3.

The rule now lives once, as the module-level `normalize_base_url`, and the static method
delegates to it. The alternative — inlining the scheme check at both call sites in
`service_benchmark.py` — puts three copies of one rule in the tree and invites exactly the
drift this change exists to remove. The static method is kept as a delegating wrapper rather
than deleted, because it is the provider's own internal call path and removing it would
widen the diff into `LocalProvider` for no behavioural gain.

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
- **The fix is verified over a socket, not against a model server.** Review round 4 replaced
  the original in-process-only evidence. `test_huggingface_judge_answers_over_a_real_socket`
  binds a standard-library HTTP server that speaks the OpenAI `/v1/chat/completions` dialect
  on an ephemeral loopback port, points the arm at it with `OLLAMA_HOST` set to a dead port,
  and asserts the round trip: the judge sends `POST /v1/chat/completions` carrying an
  `Authorization` header and the configured model, and the streamed reply parses back to a
  message. That covers the four things in-process construction could not — URL dialect,
  authentication, request payload, and response handling.

  What it still does not cover, stated exactly, because review asked twice and the
  boundary is structural rather than a choice:

  - **The RAGAS wrapper seam.** `evaluate_ragas` wraps the judge at
    `service_benchmark.py:1726` — `LangchainLLMWrapper(self.get_ragas_llm_evaluator())`.
    No unit test can reach it: `ragas` is **not installed in the gate environment**, which
    is why the import at `:1691` is lazy and carries the comment "ragas (and its transitive
    `datasets` dep) is benchmark-only and absent from the unit-test environment".
    `pip show ragas` in the gate env reports "Package(s) not found". So the seam is
    untestable here for *every* evaluator provider, not only this one — the shipped
    `huit_bedrock`, `local` and `anthropic` arms have no such coverage either.
  - **A named GPU deployment.** No vLLM or TGI endpoint is reachable from the gate or from
    CI; probed for one (`:11434`, `:8000`, an `ollama` process) before writing the socket
    test and found none. Whether a given endpoint answers is a property of the endpoint an
    operator configures, not of this call site.

  Residual risk is low and bounded: `LangchainLLMWrapper` adds prompt formatting and retry
  configuration around a LangChain chat model and nothing client-type-specific, and the
  socket test proves this client completes a real streamed exchange. And the blast radius
  stays latent — no shipped configuration selects `huggingface`
  (`docs/docs/benchmarking.md:449` and both files under
  `examples/benchmarking/hierarchical_rerank_ab/` use `huit_bedrock`), so there is no
  deployment whose judge this change alters. A full benchmark run against a named
  deployment remains a `needs-deploy` activity for whoever first configures the provider.
