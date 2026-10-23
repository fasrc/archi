# Design — canonicalizing the local provider's mode

## Decision 1: the whitelist lives in `src/utils/local_mode.py`, outside the providers package

`src/utils/local_mode.py` holds `MODE_OLLAMA`, `MODE_OPENAI_COMPAT`, `LOCAL_MODES`,
`canonical_local_mode`, and `apply_local_mode`. It imports only from `typing`.

The obvious home is next to `LocalProvider`, and the next-most-obvious is a new module
beside it under `src/archi/providers/`. **Both are wrong**, and for a reason worth stating
plainly because it is easy to get backwards: a module's own imports are not what costs —
its **package's `__init__.py`** is. Importing any `src.archi.providers.*` submodule
executes `src/archi/providers/__init__.py`, whose line 21 imports
`src/archi/providers/base.py`, whose line 13 is `from
langchain_core.language_models.chat_models import BaseChatModel`. A module under
`src/archi/providers/` that imports nothing but `typing` still drags LangChain in behind it.

Measured on the current tip:

```
import src.data_manager.collectors.processing  -> 'langchain_core' in sys.modules == False
import src.archi.providers.base                -> 'langchain_core' in sys.modules == True
import src.utils.logging                       -> 'langchain_core' in sys.modules == False
```

That matters because `src/data_manager/collectors/processing.py:784` imports the provider
**inside** a function on purpose. Its docstring at lines 775-783 says why: a module-scope
import "would make `processing.py` ... unimportable wherever langchain is absent (e.g. the
unit-test/CI environment)". Wiring that seam to a canonicalizer under
`src/archi/providers/` would silently undo exactly that guarantee. `src/bin/benchmark_sut.py`
is collateral — its only module-scope import today is `from typing import ...`.

Nothing in the suite would catch the regression: LangChain is installed in the gate
environment and `tests/unit/conftest.py:16-18` pins the real package, so the import cost is
invisible to every test. It would surface only in a LangChain-free environment, as
`processing.py` becoming unimportable.

`src/utils/` is LangChain-free (measured above), and `processing.py:36` and
`local_provider.py:14` both already import from it. Put the module there.

### Consequence for one acceptance criterion

Issue #463 asks that `grep -c 'openai_compat"' src/archi/providers/local_provider.py`
show the literal in exactly one predicate. With the constant defined in
`src/utils/local_mode.py` and imported, that count becomes **0**, not 1. The criterion's
stated purpose is that "the whitelist and `_is_openai_compat` agree rather than
duplicating the string test", and zero duplicates satisfies it strictly better than one.

Assert the repository-wide invariant instead: across `src/utils/local_mode.py` and
`src/archi/providers/local_provider.py` together, the literal `openai_compat"` appears
exactly **once** — on the `MODE_OPENAI_COMPAT` assignment. `_is_openai_compat` compares
against that constant. Call the deviation out in the PR body so the reviewer sees it was
deliberate.

## Decision 2: only `None` counts as unset — the empty string is refused

Issue #463 requires that a missing `mode` key stay the documented `ollama` default and
that `""` be refused. Those two rules are only separable if `None` and `""` are treated
differently, so:

- `canonical_local_mode(None)` returns `None`, meaning "the operator said nothing".
- `canonical_local_mode("")` raises, because `mode: ""` in YAML is an explicit statement.
- A non-string value raises with the same named error.

### The correction this forces at the seams

Issue #463 describes `app.py:182` and `base_react.py:1341` as copying the value
"verbatim". They do not. **All four** seams are truthiness-guarded on the current tree:

- `app.py:181` — `if provider_type == ProviderType.LOCAL and cfg.get("mode"):`
- `base_react.py:1340` — `if provider_type == ProviderType.LOCAL and cfg.get("mode"):`
- `__init__.py:274` — `if mode and "local_mode" not in extra_kwargs:`
- `processing.py:1013` — `if mode and "local_mode" not in extra:`

So `""` is swallowed by the guard at every seam today and never reaches `extra_kwargs`.
Wiring the canonicalizer **inside** those guards would leave `""` silently defaulting to
Ollama and would not meet the acceptance criteria.

The canonicalizer therefore runs on the raw value **before** the truthiness test.
`apply_local_mode(extra, raw_mode)` canonicalizes first — raising for an explicit bad
value — and only then decides whether to write the key, writing nothing when the
canonical result is `None`.

## Decision 3: each seam keeps its own precedence

The seams also disagree about what wins when a config sets both `mode` and
`extra_kwargs.local_mode`: `app.py` and `base_react.py` overwrite the `extra_kwargs`
value; `__init__.py` and `processing.py` let it win. That is a second divergence, and
unifying it would change behavior for configs that set both — something #463 never asked
for and no spec requirement covers.

`apply_local_mode(extra, raw_mode, *, overwrite=True)` preserves each seam exactly:
`app.py` and `base_react.py` take the default; `__init__.py` and `processing.py` pass
`overwrite=False`. The divergence stops being hidden across four files and becomes one
visible parameter. Record it in the PR body as a follow-up candidate.

## Decision 4: the provider canonicalizes too, and writes the value back

The seams are not the only entry point. The reproduction probe in #463 builds
`LocalProvider(ProviderConfig(..., extra_kwargs={"local_mode": mode}))` directly, and its
acceptance criterion requires that every rejected spelling raise **before a client is
built**. So `LocalProvider.__init__` canonicalizes as well, before it resolves
`config.base_url` from the mode.

`__init__` writes the canonical value back into `config.extra_kwargs["local_mode"]`
**whenever the key is present**, substituting `MODE_OLLAMA` when the canonical result is
`None`. Canonicalizing once at construction is cheaper and less surprising than re-deriving
it on every `local_mode` property read, and it means everything downstream — including the
`**self.config.extra_kwargs` splat in `_get_ollama_model` — sees the canonical spelling.
The `local_mode` property keeps its plain read with the `ollama` default.
`canonical_local_mode` is idempotent, so a value already canonicalized at a seam passes
through the constructor unchanged. When the key is **absent**, write nothing: the property
default already covers it, and writing would change the shape of a config that never
mentioned the mode.

### Why the null key must be written, not skipped

Substituting `MODE_OLLAMA` for a present-but-null key is not cosmetic. The `local_mode`
property at `local_provider.py:99` is
`self.config.extra_kwargs.get("local_mode", "ollama")` — the default fires only on a
**missing** key. A key present holding `None` returns `None`, and two other readers compare
that against the literal:

- `list_models` at `local_provider.py:177` — `if self.local_mode == "ollama":`
- `validate_connection` at `local_provider.py:266` — `if self.local_mode == "ollama":`

Both take their non-Ollama branch for a null mode today. `validate_connection` then probes
an OpenAI-style path against the Ollama port and always returns False, and `list_models`
skips Ollama's `/api/tags` discovery, so the model picker shows only statically configured
models. That is a pre-existing bug, but this change is what introduces the requirement "An
unset local mode keeps the documented Ollama default" — so shipping the requirement while
leaving those two readers broken would put a false statement in the spec. Writing the
canonical default at construction fixes all three readers at once, without touching the
property.

## Decision 4b: the base_react seam must move out of the bare `except`

`src/archi/pipelines/agents/base_react.py:1338-1343` is:

```python
extra = dict(cfg.get("extra_kwargs", {}) or {})
try:
    provider_type = ProviderType(provider_key)
    if provider_type == ProviderType.LOCAL and cfg.get("mode"):
        extra["local_mode"] = cfg.get("mode")
except Exception:
    pass
```

Swapping line 1341 for an `apply_local_mode` call **in place** is the obvious edit and it is
wrong: `except Exception: pass` swallows the new `ValueError`, and because
`apply_local_mode` raises before it writes, `extra` ends up with no `local_mode` key at all.
The provider then falls to its `ollama` default and the constructor's refusal never fires.
The agent path — the main chat path, via `_init_llms` — would keep silently building a
`ChatOllama` client for `mode: vllm`, which is the exact defect #463 exists to kill, while
the spec and the PR body both claim it now fails loudly.

Hoisting the call out of the `try` wholesale is also wrong: the `try` is there to absorb
`ProviderType(provider_key)` raising for a provider key that is not a member of the enum
(`ProviderType` is a `str, Enum`, so an unknown name raises `ValueError`). An unguarded call
would start canonicalizing and rejecting `mode` for non-local providers too — a scope
change nothing asked for.

Narrow the `try` to the one expression it exists to guard, and put the seam after it:

```python
extra = dict(cfg.get("extra_kwargs", {}) or {})
try:
    provider_type = ProviderType(provider_key)
except ValueError:
    provider_type = None
if provider_type == ProviderType.LOCAL:
    apply_local_mode(extra, cfg.get("mode"))
```

The unknown-provider-key path behaves exactly as before — no mode handling, no raise — and
an unrecognized `mode` on a genuine `local` provider now propagates.

This does mean the constructor now raises on a live chat request path. Round 1 of the
PR #462 review declined exactly that, on the grounds that it converts a working-if-oddly
-named deployment into a hard failure. Issue #463 overrides that objection deliberately
and requires the PR body to name the behavior change. Such a deployment is not working:
it is serving an Ollama client to an operator who asked for something else.

## Decision 5: `_is_openai_compat` stays the single predicate, with a corrected docstring

Its current docstring states that `get_chat_model` "falls back to `ChatOllama` for every
other value — including `None` and a typo". After this change a typo raises and only
`None` falls back. Leaving that text in place would document behavior the code no longer
has. Rewrite it to say the predicate compares an already-canonical mode, and that
canonicalization happens at construction.

## Decision 6: `resolve_local_mode` delegates its explicit branch only

`src/bin/benchmark_sut.py:27` reads:

```python
if explicit:
    return str(explicit).lower()
```

Replace the body of that branch with `canonical_local_mode(explicit)`, which never
returns `None` for a truthy input, so the return type stays `str`. Keep the `if explicit:`
guard: an empty explicit value must continue to fall through to the URL auto-detect,
which is documented behavior of the benchmark runner. Auto-detect itself already yields
canonical values and does not change.

## Decision 7: two scenarios in the unarchived #450 change are superseded

`openspec/changes/fix-issue-450-ollama-host-scope/specs/local-provider-endpoint-resolution/spec.md`
asserts, under "The endpoint rule and the client dialect read the local mode the same
way":

- "Scenario: An unrecognized mode resolves the Ollama endpoint" — mode `vllm` resolves
  `DEFAULT_OLLAMA_BASE_URL`
- "Scenario: An unrecognized mode still yields to the environment" — mode `vllm` resolves
  the `OLLAMA_HOST` value

Both become false: `vllm` now raises. PR #462 is merged but the change is not archived, so
those scenarios are still live pending spec text in the repository. Leaving them would
have the tree assert two contradictory behaviors for the same input.

The scenarios are not the only false text. The requirement's **rationale paragraph** at
that file's lines 61-64 also has to change:

> `get_chat_model` builds `ChatOpenAI` for the exact string `openai_compat` and
> `ChatOllama` for every other value, including `None` and a misspelling. `local_mode`
> reaches the provider straight from operator config and no seam validates or
> canonicalizes it.

A misspelling now raises rather than falling back, and all four seams now validate and
canonicalize — so both sentences become false. The requirement's **SHALL line** at line 59
("only the exact mode `openai_compat`") stays true and needs no change: this change
canonicalizes *before* the predicate, and keeps a single exact comparison against a shared
constant. Amend the rationale, leave the SHALL alone.

Both changes are `## ADDED` deltas to the same capability, so when they archive together
the capability will carry two requirement headings about the same predicate. That is
tolerable; two contradictory *statements* are not.

The deltas here use `## ADDED Requirements` against `local-provider-endpoint-resolution`,
because the capability is not in `openspec/specs/` — confirmed with
`ls openspec/specs/ | grep -i local-provider`, which returns nothing. Amend the two
superseded scenarios in the #450 change directory in the same PR, and flag the amendment
in the PR body. Reconciling an unarchived change has precedent here: PR #307,
`spec(#254): reconcile the issue-246 guard artifacts with the shipped guard`.

## Decision 9: five predating tests assert the behavior being removed

`tests/unit/test_local_provider_env_override.py` holds 22 tests on the current tip. Five of
them build a provider with `local_mode="vllm"` through the file's shared `_build()` helper
— a bare `LocalProvider(config)` — and assert it resolves an Ollama endpoint:

| line | test |
|---|---|
| 95 | `test_unknown_mode_falls_back_to_the_ollama_default` |
| 101 | `test_unknown_mode_still_honors_ollama_host` |
| 106 | `test_unknown_mode_configured_base_url_yields_to_ollama_host` |
| 119 | `test_unknown_mode_endpoint_and_client_agree` |
| 215 | `test_a_per_call_mode_cannot_promote_an_unrecognized_mode` |

Prototyping Decision 4 against the current tip and running the file gives `5 failed, 17
passed` — exactly those five. The last one raises on its `_build()` setup line, *before* its
own `with pytest.raises(ValueError)` block, so it fails rather than passing by accident.

They are not wrong tests; they pin the behavior #450 shipped and #463 removes. They must be
restated, not deleted — each one's point (the endpoint rule and the client dialect agree;
`OLLAMA_HOST` still applies; a per-call mode cannot promote) survives intact when the
unrecognized mode `vllm` is replaced by a **null** mode, which still resolves the Ollama
endpoint and still yields to `OLLAMA_HOST`. That is the same restatement Decision 7 applies
to the two analogous #450 spec scenarios, so the spec and the tests stay in step.

Naming them here matters because the alternative recovery is the damaging one. An
implementer who meets five unexplained failures and a "the suite must end green" rule is
pushed toward dropping the constructor check and validating only at the seams. That keeps
all 22 green, keeps the new seam tests green, keeps patch coverage at 100%, and passes the
gate — while the issue's own reproduction probe, which constructs `LocalProvider` directly,
still prints `mode='vllm' client=ChatOllama`. The change would ship green and do nothing.

## Decision 8: the `app.py` seam is testable — test it

The project's CLAUDE.md warns that `src/interfaces/chat_app/app.py` is not imported by the
unit tests, so new lines there fail diff-cover. **For this seam that warning does not
apply**, and planning around it would have left the change's only live-request seam
unverified.

`tests/unit/test_provider_config_override.py:14` does `from src.interfaces.chat_app.app
import ChatWrapper, _build_provider_config_from_payload, _is_provider_enabled_in_config`,
and three of its tests call `_build_provider_config_from_payload` directly. One of them, at
line 48, already asserts `pc.extra_kwargs.get("local_mode") == "openai_compat"`. A coverage
run of that file alone shows `app.py:181-182` — the exact seam being edited — executed, not
missed.

So the seam gets a real test like the other three, and there is no line budget to respect:
the `apply_local_mode` import and the rewritten guard are all covered. The general CLAUDE.md
rule still holds for the rest of `app.py`; it is this specific function that the unit tests
reach.

This also removes the incentive that would have caused the worst outcome here. A two-line
cap pushes an implementer toward the minimal in-place edit — keeping the
`and cfg.get("mode")` truthiness guard and just swapping the assignment — which is exactly
the shape Decision 2 exists to prevent, and nothing would have caught it.
