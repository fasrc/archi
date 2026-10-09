## Context

Anchors verified on `origin/dev` `db701852` (the issue cites `0ddc96e1`; the
lines moved, the code did not):

- `src/utils/benchmark_schema.py:720` `_positive_number(value, default, name)`.
  `:737` `or not math.isfinite(value)` raises `OverflowError` for `10**309`.
  `:748` returns `value` unchanged.
- `src/utils/benchmark_schema.py:751` `ragas_run_config_kwargs`, `:783`
  `ragas_effective_settings`, `:800` `with_effective_ragas_settings`.
- `src/bin/service_benchmark.py:484` `ResultHandler.map_prompts(config)` mutates
  the dict in place. `:502` `handle_results` loads the file at `:516-517`, calls
  `map_prompts(config)` at `:519`, records `"configuration": config` at `:611`,
  and passes `selected=config` to `config_version(...)` at `:692-699`.
- `src/utils/benchmark_provenance.py:729` `config_version`: `selected` feeds
  `selected_file_digest` and the divergence list; `effective_selected` replaces
  `selected` in the digest basis only.
- `docs/docs/benchmarking.md:120` (`timeout` row) and `:375` ("The configuration
  is also recorded verbatim as `configuration`").

## Goals / Non-Goals

**Goals:** the four items of issue #521, each with a test that fails first.

**Non-Goals:** `_positive_int` and `max_workers` (`_positive_int` accepts only
`int` and never calls `math.isfinite`, so it cannot overflow); any change to
`config_version` or `effective_config`; templates and deploy files.

## Decisions

### D1 — Oversized number: fall back, do not raise

Wrap the finiteness check so that `OverflowError` from `math.isfinite` counts as
invalid. Implementation: compute `math.isfinite(value)` inside a `try`, and treat
`OverflowError` as "not finite". The fallback path and its warning stay the same.
The warning format uses `%r` for the value, which is safe for a huge int.

### D2 — Canonicalize an integral float to int

After validation, `return int(value)` when `isinstance(value, float)` and
`value.is_integer()`. `ragas_run_config_kwargs` and `ragas_effective_settings`
both read through `_positive_number`, so `600` and `600.0` give equal kwargs,
equal effective settings, and an equal effective digest. `120.5` stays `120.5`.
`asyncio.wait_for` accepts an int, so the run behavior does not change.

### D3 — `selected=` gets the untouched file

In `handle_results`, take `selected_file = copy.deepcopy(config)` right after the
YAML load and before `map_prompts(config)`. Pass `selected=selected_file` to
`config_version`. Add `import copy` to the module imports.

What does not change:

- `effective_selected=with_effective_ragas_settings(config, ...)` keeps the
  prompt-expanded `config`. The effective digest describes what ran, and prompt
  contents are part of what ran.
- `"configuration": config` keeps the prompt-expanded text. The issue body says
  `configuration` "must hold the untouched copy" per its documented meaning, but
  it also says `map_prompts` must still feed "the run record's expanded prompt
  text", and `configuration` is the only field on the record that carries that
  text. The existing test
  `test_prompts_are_mapped_in_the_file_config_as_before`
  (`tests/unit/test_benchmark_report_records_running_config.py:449`) pins this
  behavior, and the leaderboard and A/B readers (`:966`, `:1105`) read
  `configuration`. To remove the prompt text from the record would delete
  information with no replacement. This change keeps `configuration` as it is
  and corrects the docs sentence that calls it "verbatim" (D4). The PR body
  must name this choice so the reviewer can reverse it.

For a config without `prompts`, `map_prompts` returns at once and the two dicts
are equal, so `selected_file_digest` stays byte-identical.

### D4 — Docs

`docs/docs/benchmarking.md:120`: `timeout` must be a positive number; an invalid
value (`0`, `-1`, `"many"`, NaN, infinity, an oversized integer) falls back to
`180` with a warning; an integral float such as `600.0` is recorded as `600`.
`:375`: `configuration` holds the selected file with each prompt path replaced by
the file contents; `config_version.selected_file_digest` fingerprints the file as
written, before that replacement.

## Risks / Trade-offs

- An artifact made before this change and one made after it, from the same
  hand-written file with `prompts`, have different `selected_file_digest`s. The
  template never renders `prompts`, so rendered configs are not affected.
- An integral float `timeout` changes the effective digest of an older artifact
  that recorded `600.0`. That is the intended fix.
