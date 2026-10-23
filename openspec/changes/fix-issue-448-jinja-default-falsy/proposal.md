# Honor an explicitly configured falsy value when `base-config.yaml` is rendered

## Why

Jinja's `default(value, boolean=true)` substitutes for **every falsy input**, not only for
an undefined one. `src/cli/templates/base-config.yaml` uses that two-argument form on 21
live boolean flags, so `enabled: false` in `config.yaml` renders as `enabled: True`.
**None of those 21 flags can be turned off in any archi deployment.**

Measured on this branch's base, `origin/dev` at `c6112167`, with the render harness the
unit tests already use (`jinja2 3.1.3`, `PackageLoader("src.cli")`, `ChainableUndefined`,
`yaml.safe_load` — the shape at `tests/unit/test_base_config_sitemap_render.py:15-24`):

```
input config                                              rendered value
data_manager.processing.html_to_markdown.enabled: false   ->  True
data_manager.sources.git.enabled: false                   ->  True
services.data_manager.enabled: false                      ->  True
```

**End-to-end proof (fasrc/archi#448).** Feature-matrix campaign #396 arm 06 sets
`processing.html_to_markdown.enabled: false`, deployed a stack, ingested for 4933 s and ran
a 109-question RAGAS pass. `archive_run.sh` then refused the artifact — `REFUSED: artifact
ran factor processing.html_to_markdown.enabled=True, arm 06 wants False`. 3 h 45 m of GPU
time lost. The integrity gate held, so no bad data was recorded.

**Runtime reach.** These values are load-bearing, not cosmetic: `scraper_manager.py:128`
reads `git_config.get("enabled", False)`, and a rendered deployment that never configured
Jira or Redmine still carries `jira: enabled: True` and `redmine: enabled: True`.

**Why nothing caught it.** `lock_campaign.sh` hashes arm YAML *content* and never renders
it; benchmark "Procedure E" compares a run against the *rendered* config, which already
carried the wrong value. No test asserts that a falsy config value survives rendering.

### Exact scope, measured with the Jinja AST — not with grep

`grep -c "default(true, true)"` returns 22 and the issue reports "21 live + 1 comment".
That is right but it is not the whole picture: grep cannot see spacing variants,
capitalisation (`default(false, True)` at `:334`), or the one-argument form. Parsing the
template with `jinja2.Environment().parse()` and walking every `Filter` node named
`default` gives the authoritative census at `c6112167`:

| call form | count | verdict |
|---|---|---|
| `default(<true>, true)` — boolean default, truthiness substitution | **21** | **the proven bug**: `false` is unexpressible |
| `default(<true\|false>, false)` — `:122`, `:188` (`flask_debug_mode`) | 2 | honors `false`; still renders explicit `null` as the string `'None'` |
| `default(<true\|false>)` — `:43`, `:163`, `:280`, `:360`, `:378` | 5 | honors `false`; same `'None'` hazard |
| `default(<truthy number>, true)` | 33 | `0` unexpressible; **2 of them matter** (`:299`, `:332`) |
| `default(<truthy string>, true)` | 48 | `''` unexpressible; out of scope |
| `default(<falsy>, true)` (`''`, `[]`, `{}`, `false`, `none`, `0`) | 58 | no-op — the default is itself falsy |
| non-const default expression | 14 | not a literal; unaffected |

The 21 bug sites are `:164 :214 :259 :290 :291 :301 :302 :310 :343 :345 :346 :352 :353
:357 :375 :390 :392 :402 :410 :416 :426`.

## What Changes

- **The 28 boolean sites that can misrepresent the operator move to the ternary** — the 21
  broken ones plus the 7 null-unsafe ones (`:43`, `:122`, `:163`, `:188`, `:280`, `:360`,
  `:378`). Each substitutes its default only when the key is **absent or null**, using the
  `{%- set %}` + `is defined and is not none` form the file already uses at `:253-258` and
  `:331`. The 21 behaviour fixes and the 7 null-safety conversions are separate commits.
- **The 22 `default(false, true)` boolean sites stay as they are.** A falsy default with
  truthiness substitution maps every falsy input to `False`, which is the value the
  operator asked for, so that form cannot lie. It is the one permitted use of `default` on
  a boolean flag, and the guard encodes exactly that.
- **Four numeric call sites where `0` is a plausible operator intent** move to the same
  ternary: `sources.links.base_source_depth` (default `1`),
  `sources.links.sitemap.max_pages` (default `20000`), `sources.links.max_pages` and
  `sources.elog.max_entries` (both default `null`). The `min_pages` line immediately above
  `sitemap.max_pages` is already written that way — the two lines currently disagree with
  each other. The two nullable caps matter most: `LinkScraper` and `ElogScraper` stop at
  once for `0` and run uncapped for `null`, so rendering a configured `0` as null turns
  "fetch nothing" into "fetch everything". `sitemap.max_pages` is a validation ceiling
  rather than a budget, so its `0` means "fail if any page is emitted"; see D3.
- **A guard test freezes the pattern out.** Parsing the template's AST, it fails if any
  `default` call carries a boolean-literal default in any form other than
  `default(false, true)` — that covers `default(true, true)`, `default(true, True)`,
  spacing variants, the one-argument form and the `boolean=false` form — and it fails when
  a **new** truthy non-boolean site appears, by comparing the survivors against a frozen
  baseline of 79.
- **The rule is stated once, at the top of the template**, replacing the local note at
  `:278-279`.

### Correction to the issue's prescribed fix — measured, not argued

Issue #448 says the fix for booleans is bare `| default(true)` and gives a table claiming
that form renders `True` for input `None`. **That is wrong.** Jinja's one-argument
`default` substitutes only for `Undefined`; `None` is defined, so it passes through and
renders the four characters `None`, which `yaml.safe_load` reads as the **string**
`'None'`. Measured on `c6112167` with `jinja2 3.1.3`:

| input | `default(true, true)` (today) | `default(true)` (issue's fix) | `set` + ternary (this change) |
|---|---|---|---|
| absent key (chained undefined) | `True` (bool) | `True` (bool) | `True` (bool) |
| `false` | `True` (bool) — **the bug** | `False` (bool) | `False` (bool) |
| `true` | `True` (bool) | `True` (bool) | `True` (bool) |
| **explicit `null`** | `True` (bool) | **`'None'` (str)** | `True` (bool) |
| `0` | `True` (bool) | `0` (int) | `0` (int) |

A blank `enabled:` in hand-written YAML is exactly how an operator writes null. Under the
issue's fix that key renders as the truthy string `'None'` — a silent type change on a
boolean flag, and an active hazard for `sources.elog.verify_ssl` (`:416`), where `requests`
reads a string as a CA-bundle path, and for the selenium/indico `headless` flags (`:343`,
`:375`). The ternary is the file's other established idiom, it is null-safe, and it
satisfies the issue's own acceptance criterion that an unset key still yields `True`. This
change therefore uses the ternary and adds no new bare `default(true)`. See design.md D1.

## Out of scope, deliberately

- **`default(<falsy>, true)` — 58 sites.** The default is itself falsy, so behaviour is
  identical with or without the second argument. Touching them is pure churn; the issue
  rules them out by name.
- **The 79 remaining truthy non-boolean sites** (31 number, 48 string, after `:299` and
  `:332` are fixed). For a string the unexpressible value is `''` and for a number it is
  `0`, and the issue's scope section says to leave them alone unless a test shows `0` is
  meaningful. They are **frozen, not fixed**: the guard records them as a baseline that can
  go down but never up. See design.md D4 — this is the one place where the issue's
  acceptance criterion 4, read literally, contradicts its own scope section, and D4 is how
  the change resolves that.
- **`lock_campaign.sh` rendering each arm before hashing it** — the issue's step 6, filed
  separately, not done here.
- **Any deployment.** Campaign #396 pins `src/` in
  `bench_out/feature_matrix/campaign.lock` and runs from a pinned checkout, so merging to
  `dev` is safe; deploying to `holygpu7c0717` while the campaign runs is not, and is not
  part of this change.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `config-propagation`: adds the requirement that a value the operator configured survives
  rendering into the deployed config — the first link in the chain this capability already
  covers (render → seed → restart → observable). Recorded as **ADDED** requirements, not
  MODIFIED: the capability has no file in `openspec/specs/` yet, because it exists only in
  the unarchived change `harden-config-propagation`, so there is nothing to modify.

## Impact

- `src/cli/templates/base-config.yaml` — 30 render expressions (28 boolean + 2 numeric) and
  one comment block. No Python source changes, so `diff-cover` finds no measurable lines
  for the template itself; the new tests must still be green and black/isort clean.
- `tests/unit/test_base_config_falsy_defaults_render.py` (new) — per-key render assertions
  for the boolean flags and the two numeric ones, plus the AST guard.
- No change to `src/cli/managers/templates_manager.py`, `src/cli/utils/helpers.py`, the
  config schema, dependencies, CI, or any deployment. No container rebuild.
