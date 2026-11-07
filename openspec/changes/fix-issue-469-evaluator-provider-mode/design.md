# Design — render the judge's own local mode

## Decision 1: Copy the SUT key's render verbatim, do not invent a third spelling

`base-config.yaml` already holds two render styles for the same kind of value. The three
sibling evaluator keys at lines 98-100 use `| default("", true)`:

```yaml
evaluator_provider: {{ ... .evaluator_provider | default("", true) }}
```

The SUT's `provider_mode` at lines 46-61 uses a presence guard plus `| tojson`.

The `default("", true)` style is wrong for a mode key, and PR #467 proved it twice by
measurement:

- `default("", true)` is the truthy-default filter. A configured `false` or `0` is falsy,
  so the filter replaces it with `""`, and `""` means "inherit" to the consumer. The
  operator's value vanishes before any validator sees it.
- A bare interpolation lets YAML 1.1 retype the value on the way back in. The string
  `"null"` renders as the bare word `null` and reloads as `None`; `"on"` reloads as `True`.

So this change follows the SUT key, not the evaluator siblings beside it. The result is a
deliberate inconsistency within the RAGAS block: `evaluator_provider_mode` is guarded and
JSON-serialized while `evaluator_provider`, `evaluator_model` and `evaluator_ollama_url`
next to it are not.

That inconsistency is accepted rather than fixed here. The other three are free-text names
and URLs: they have no validator to reach, no falsy value that means anything, and no
YAML-special spelling that is meaningful. Converting them is a wider change with its own
regression surface, and it is not needed to make the judge/SUT mode split reachable. A
comment in the template records why the neighbour differs, so the next reader does not
"tidy" it back into the sibling style and silently reintroduce the defect.

## Decision 2: Omit the key when absent or null, matching the SUT key

The consumer treats `None` and `""` identically — both inherit the SUT's mode:

```python
evaluator_mode if evaluator_mode not in (None, "") else benchmark_cfg.get("provider_mode")
```

So an omitted key and a rendered `null` would behave the same. Omitting is still the right
choice, for two reasons. It keeps the rendered config honest — a key that is not configured
does not appear as if it were — and it keeps this key's rule identical to the SUT key's,
which is the whole point of Decision 1. A reader who knows one now knows both.

The empty string is the exception and stays a *rendered* value: it is a configured value
that means "inherit", it is already load-bearing in the consumer, and a test pins it.

## Decision 3: Do not touch `src/bin/service_benchmark.py`

The issue's acceptance criteria include `git diff origin/dev...HEAD --
src/bin/service_benchmark.py` printing nothing. The consumer branch added by `d9798fb6` is
correct and already covered by
`tests/unit/test_ragas_evaluator_local_mode.py:390,419`. The defect is that no config
reaches it. Changing the consumer would treat the symptom at the wrong layer and would
break the tests that currently pin its inherit semantics.

## Decision 4: The spec delta is ADDED, not MODIFIED

`openspec/specs/` holds no `local-provider-endpoint-resolution` directory. The capability
exists only inside two unarchived change directories
(`fix-issue-450-ollama-host-scope` and `fix-issue-463-local-mode-canonicalization`). An
OpenSpec delta can only say `MODIFIED` about text that is in `openspec/specs/`, so this
change uses `## ADDED Requirements`. Verify before writing:

```bash
ls openspec/specs/ | grep local-provider-endpoint-resolution   # expect: no output
```

## Decision 5: The gate's coverage number is expected to be vacuous here

The diff changes a Jinja template, a test file, a spec and a documentation page. It changes
no line of Python under `src/`. `scripts/gate.sh` measures patch coverage with `--cov=src`,
which collects Python only, so `diff-cover` will report **"no lines with coverage
information"** and the `--fail-under=80` check passes trivially.

That message is the expected result for this change, not a gate failure and not something
to work around by adding a Python shim. Do not manufacture a `src/**.py` edit to produce a
coverage number.

## Testing strategy

Every test renders the real template through the existing `_render` helper at
`tests/unit/test_base_config_benchmark_render.py:20`, which mirrors the CLI's own Jinja
environment (`PackageLoader` + `ChainableUndefined`). Rendering the real template is what
makes these tests catch the defect that the in-memory `ragas_configs` tests missed.

Two of them assert **through the consumer** rather than on the rendered value alone —
they pass the rendered value to `resolve_local_mode` and require a `ValueError`. Asserting
only "the key is present" would pass even if the render retyped the operator's string,
which is exactly the second defect `d37afb3a` fixed for the SUT key.
