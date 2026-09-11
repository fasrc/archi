## Context

`LocalProvider.__init__` resolves the endpoint for both local modes in one branch
(`src/archi/providers/local_provider.py:53-77`). It reads `OLLAMA_HOST` and applies it
before it knows which mode the caller declared, so an Ollama address reaches an
`openai_compat` provider. Measured at `origin/dev` @ `143ee248`:

| `OLLAMA_HOST` | configured `base_url` | mode | resolved `base_url` |
|---|---|---|---|
| set | set | `openai_compat` | the environment value — **the defect** |
| set | set | `ollama` | the environment value — correct, keep |
| set | absent | `openai_compat` | the environment value — the same defect, via the fallback |
| unset | absent | `openai_compat` | `http://localhost:11434` — the Ollama default, wrong dialect |

Two existing tests in `tests/unit/test_ragas_evaluator_local_mode.py` pin rows 3 and 4.
They are green today. Any correct fix reddens them, so this change has to decide what
they should assert instead — that decision is the reason this document exists.

The mode is readable before construction. `get_model()` copies a config `mode` key into
`extra_kwargs["local_mode"]` (`src/archi/providers/__init__.py:269-274`) and only then
calls `get_provider()`, which instantiates the class at line 136.

## Goals / Non-Goals

**Goals:**

- Stop `OLLAMA_HOST` from retargeting an `openai_compat` provider, whether the caller
  configured a `base_url` or left it absent.
- Keep `OLLAMA_HOST` authoritative in `ollama` mode, so the CI smoke path is unaffected.
- Keep the endpoint rule and the client dialect reading `local_mode` through one predicate,
  so an unrecognized or null mode cannot resolve one dialect's endpoint and then get the
  other dialect's client.
- Give `openai_compat` a fallback in its own dialect and on its own port.
- Leave the suite honest: update the two tests that pin the old values, and say why in
  their docstrings.

**Non-Goals:**

- Canonicalizing or rejecting a non-canonical `local_mode` spelling (`OpenAI_Compat`,
  `openai-compat`, `vllm`). The four config seams copy the operator value verbatim and
  none validates it, so a mixed-case spelling of the compat mode builds an Ollama
  client. That predates this change and belongs at the config seam; filed as issue #463.
- The three other defects in the same file (per-call `base_url` ignored in Ollama mode,
  `list_models()` never querying `/v1/models`, `ChatOllama` dropping `extra_body`).
- Removing the double-`base_url` call shape in `src/bin/service_benchmark.py`. The fix
  makes the keyword redundant, not wrong. Removing it is a behavior change in the judge
  path and belongs in its own change.
- The in-place mutation of the caller's `ProviderConfig`. The constructor writes
  `config.base_url` on the object it was handed. That is pre-existing and unchanged here.
- `src/bin/service_benchmark.py:1245` exporting `OLLAMA_HOST` process-wide. After this
  change that export stops leaking into `openai_compat` providers, which is the point;
  the export itself stays.

## Decisions

**Read the mode from the local `config` variable, not from `self.local_mode`.**
`local_mode` is a property over `self.config`, and `self.config` does not exist until
`super().__init__(config)` runs on line 77. Use
`config.extra_kwargs.get("local_mode", "ollama")` on the incoming object. Default to
`"ollama"`, which matches the property's own default, so a config that declares no mode
keeps today's behavior exactly.

**Gate the override and the fallback in one edit, not two.**
Rows 1 and 3 of the table are the same defect reached by two paths, and rows 3 and 4 are
the same fallback expression (`default_ollama_host = env_ollama_host or
DEFAULT_OLLAMA_BASE_URL`). Splitting them across two tasks would let the first task
resolve the second task's failing case as a side effect, leaving a task with no red step
to write. One edit to the whole `else` branch keeps each task's test honest.

**An `openai_compat` provider with no configured endpoint falls back to
`DEFAULT_OPENAI_COMPAT_BASE_URL`.**
Alternative considered: leave the fallback alone and gate only the override. Rejected —
`default_ollama_host` folds the environment value into the fallback, so the environment
would keep reaching `openai_compat` callers through row 3, and row 4 would keep handing an
OpenAI-dialect client an Ollama port. Gating only the override fixes the headline repro
and leaves the same class of bug live.

**Update the two pinning tests; do not delete or weaken them.**
`test_local_openai_compat_judge_without_a_url_still_inherits_ollama_host` asserts row 3,
which is the defect. The same file already calls that behavior a hijack — "without the
kwarg the judge scores its own answers against the system under test" — so the suite
contradicts itself today. Both tests keep their shape and gain a new expected endpoint
(`http://localhost:8000/v1`) plus a docstring line recording that #450 moved it.
Alternative considered: keep both green by leaving the fallback on the Ollama default.
Rejected for the reason above, and because it preserves a RAGAS judge that scores a system
under test against itself whenever no judge URL is configured.

**The `config is None` branch keeps the environment override.**
That branch builds its own config and hard-codes `local_mode: "ollama"`, so the
mode-aware rule already admits the override there. No edit needed, and a reader can
confirm that from the branch itself.

## Risks / Trade-offs

- [A RAGAS run with no configured judge URL changes endpoint, from the system under test
  to `http://localhost:8000/v1`] → That path was self-scoring, so the old endpoint
  produced invalid scores rather than useful ones. An operator who wants a specific judge
  sets `evaluator_ollama_url`, which is unaffected. Called out in the PR body so a human
  sees it before merge.
- [An operator somewhere steers an `openai_compat` provider with `OLLAMA_HOST` on purpose]
  → Nothing under `deploy/` sets the variable, and
  `src/cli/templates/base-compose.yaml:234` renders it empty when unset. Such an operator
  sets `base_url` instead, which now wins.
- [The fix looks right and is vacuous, because the mode key never arrives] →
  `get_model()` translates `mode` to `local_mode` before construction, and the four new
  unit cases build the config directly. Both paths are covered, so a vacuous fix reddens
  a test.
- [The comment edits in `src/bin/service_benchmark.py` overclaim] → Scope them to the one
  factual claim that changes (the provider no longer overwrites `config.base_url` in
  `openai_compat` mode). Do not claim the keyword became unnecessary; it stays.

## Migration Plan

None. No schema, config, or deployment change. `OLLAMA_HOST` keeps its documented meaning
for Ollama. Rollback is a revert of the one source commit.

## Open Questions

None. The issue fixes the fallback choice explicitly ("Pick
`DEFAULT_OPENAI_COMPAT_BASE_URL` rather than the Ollama host"), which settles the only
decision the two pinning tests raised.
