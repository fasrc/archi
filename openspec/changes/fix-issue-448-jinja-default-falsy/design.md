# Design — falsy config values must survive `base-config.yaml` rendering

All measurements below were taken on `origin/dev` at `c6112167` with `jinja2 3.1.3` and
`PyYAML`, using the render environment the CLI itself builds
(`src/cli/utils/helpers.py:203-210`: `PackageLoader("src.cli")`, `select_autoescape()`,
`undefined=ChainableUndefined`). Re-derive every line number before quoting it in the PR
body; do not paste these numbers forward.

## D1. The substitution form: `{%- set %}` + ternary, not bare `default(true)`

Issue #448 prescribes bare `| default(true)` for booleans and prints a table claiming it
renders `True` for input `None`. Rendered and parsed, that claim does not hold:

```
input                ternary                bare default(true)
absent (chained)     True   (bool)          True   (bool)
false                False  (bool)          False  (bool)
true                 True   (bool)          True   (bool)
null                 True   (bool)          'None' (str)     <-- silent type change
0                    0      (int)           0      (int)
```

Jinja's one-argument `default` substitutes only for `Undefined`. `None` is defined, so it
survives the filter and renders as the four characters `None`, which `yaml.safe_load`
reads as a string. Three consequences make this unacceptable for a boolean flag:

1. `'None'` is truthy, so a flag an operator blanked out reads as **on** — the same class
   of silent override this change exists to remove.
2. `sources.elog.verify_ssl` (`:416`) is handed to `requests`, which reads a **string** as
   a path to a CA bundle. `verify_ssl:` left blank would move from "verify" to "look for a
   CA bundle called None".
3. The selenium/indico `headless` flags (`:343`, `:375`) reach webdriver options the same
   way.

The chosen form is the one this file already uses for numbers at `:253-258` and for
`sitemap.min_pages` at `:331`:

```jinja
{%- set v = data_manager.sources.git.enabled %}
enabled: {{ v if v is defined and v is not none else true }}
```

`v is defined` is False for a `ChainableUndefined` produced by any missing link in the
path, so an absent section still yields the default. This is not a new pattern: it is the
file's second established idiom, and the one whose accompanying comment (`:255-257`)
already explains the exact defect being fixed.

**Rejected alternatives.** A Jinja macro or a custom filter would read better at 30 call
sites, but a custom filter has to be registered in **every** environment that renders this
template (`src/cli/utils/helpers.py:208` and `src/cli/managers/templates_manager.py`), and
a third render path added later would fail at parse time with no test to catch it. The
issue's "do not invent a pattern" constraint points the same way. `{{ v if v is boolean
else true }}` is compact and null-safe but silently discards a configured `0`, which the
ternary honors.

## D2. 28 boolean sites convert; 22 stay

The AST census over every boolean-literal `default` call — 50 of them at `c6112167` — sorts
into five shapes:

| shape | count | lines | verdict |
|---|---|---|---|
| `default(true, true)` | 21 | the bug list in proposal.md | hides an explicit `false` — **convert** |
| `default(true)` | 4 | `:43 :280 :360 :378` | explicit `null` renders `'None'` (truthy string) — **convert** |
| `default(false)` | 1 | `:163` | explicit `null` renders `'None'`, which flips a false default **on** — **convert** |
| `default(true, false)` | 2 | `:122 :188` (`flask_debug_mode`) | same `'None'` hazard — **convert** |
| `default(false, true)` | 22 | — | every falsy input maps to `False`, which is what the operator asked for — **keep** |

So 28 convert and 22 stay, and the guard rule is one sentence: *on a boolean flag, the only
permitted use of the `default` filter is `default(false, true)`; every other boolean form
either hides an explicit `false` or turns an explicit `null` into the truthy string
`'None'`.* Converting the 22 harmless sites as well would buy a marginally shorter rule for
22 no-behaviour-change edits, which is the churn the issue tells us not to spend.

The 7 null-safety conversions are a separate commit from the 21 behaviour fixes, so a
reviewer can take one without the other.

Note that `:278-279` documents bare `default(true)` as the correct pattern. That comment is
replaced by the file-level note (D5); leaving it would leave the file contradicting its own
guard.

## D3. The four numeric sites, and only those four

`sources.links.base_source_depth` (default `1`), `sources.links.sitemap.max_pages`
(default `20000`), `sources.links.max_pages` (default `null`) and
`sources.elog.max_entries` (default `null`) take the same ternary. Each is a bound whose
`0` an operator can mean, and none of the four is expressible today. The `min_pages` line
immediately above `sitemap.max_pages` is already a ternary, so those two adjacent lines
currently implement opposite policies for the same block.

**What `0` means at each site, measured against the consumer, not assumed.**
`crawl_iter` starts at `depth = 0` and loops while `depth < max_depth`, so depth counts
levels of pages: `1` is the seed page alone and `0` is no page at all. A configured
`base_source_depth: 0` therefore switches that seed off; it does not shrink the crawl to
the base page. An earlier draft of this document said it meant "index the base page only",
which is what `1` already does — the sentence was wrong, not the conversion.

Turning the seed off is a coherent thing to ask for, and it is the reading the number
already carries. The alternative — keeping `default(1, true)` so a configured `0` is
silently rewritten to `1` — is the exact failure this change exists to remove, and it
would leave the operator with no way to tell that their value was discarded.

The two nullable caps are the sharper case, because there `0` and `null` are *both*
meaningful and they mean opposite things. `LinkScraper` stops as soon as
`pages_visited >= max_pages` and treats `None` as no cap; `ElogScraper` reads
`max_entries` the same way. `default(null, true)` renders a configured `0` as null, so an
operator who asks for no pages gets an unbounded crawl — the largest possible distance
from the request.

The other 31 truthy numeric defaults (ports, timeouts, worker counts, `num_documents_to_
retrieve`) have no meaningful `0`, and the issue says to leave them alone. They are frozen
by D4 rather than changed.

## D4. The guard, and the contradiction in acceptance criterion 4

Acceptance criterion 4 asks for a test that "fails if any `default(` in the template passes
a truthy first argument plus the boolean second argument". Read literally, that test fails
on 79 sites the issue's own scope section says to leave alone (31 numbers, 48 strings). The
criterion and the scope cannot both be satisfied by fixing everything, so the guard splits
into two assertions with different strictness:

1. **Hard zero — booleans.** Walk `jinja2.Environment().parse(template_source)` for every
   `nodes.Filter` named `default`; fail if any of them has a `nodes.Const` boolean first
   argument in any shape other than `default(false, true)` (D2). This catches
   `default(true, true)`, `default(true, True)`, spacing variants, the one-argument form
   and the `boolean=false` form, none of which a `grep` for the literal string finds.
   The failure message names the line numbers.
2. **Ratchet — everything else.** Collect the sites whose default is a truthy non-boolean
   literal and whose second argument is `true`, and assert the count equals a frozen
   baseline of **79**. A new site of the deprecated form fails the test with a message
   telling the author to use the ternary; a deliberate removal fails it too, with the
   instruction to lower the baseline in the same commit.

The AST is the oracle rather than a regular expression because the template's own text is
inconsistent (`default(false, True)` at `:334` has a capitalised second argument) and
because a comment mentioning the pattern must not fail the guard — grep counted the note at
`:279` as a 22nd occurrence.

## D5. Where the rule is written down

The note at `:278-279` is local to `hierarchical_rerank.enabled` and prescribes the form D1
rejects. It is replaced by a block near the top of the template that states the rule once:
booleans and any number whose `0` is meaningful use `{%- set %}` + `is defined and is not
none`; `default(x, true)` is deprecated in this file and the guard test enforces it. The
existing numeric note at `:255-257` stays where it is — it explains a local decision and
still reads correctly.

## D6. Test strategy

- **Reuse the harness, do not re-implement it.** `tests/unit/test_base_config_sitemap_
  render.py:15-24` already builds the environment and `yaml.safe_load`s the result. The new
  file follows that shape and renders the whole template each time.
- **Parametrise by dotted path.** One list of `(render_kwargs_path, rendered_path,
  default_value)` triples drives every case, so the 28 boolean flags cost one test body.
  A small helper turns `"data_manager.sources.git.enabled"` into the nested dict the
  template expects.
- **Assert the type, not only the value.** `assert rendered is False`, not `assert not
  rendered` — the `'None'` failure in D1 is invisible to a truthiness assertion, and so is
  a `0`/`False` mix-up.
- **Every flag gets three cases**: configured `false` renders `False`; the key absent
  renders the documented default; the key explicitly `null` renders the documented default
  as a **bool**.
- **Watch out for existing tests that encode the old behaviour.** Any test that renders one
  of these keys as `None` or `0` and expects the default will now fail. That is a real
  behaviour change, not a flake: fix the test to the new contract and say so in the PR
  body. `bash scripts/gate.sh` runs the whole unit suite, so nothing hides.

## D7. Risk

- **Blast radius is deployment-time, not runtime.** No Python source changes; nothing
  ships in a container image. A rendered config only changes when the operator had
  configured a falsy value that was being ignored — which is the point.
- **A deployment that relied on the bug** (a config carrying `enabled: false` while
  expecting the feature on) will change behaviour on its next `archi create`. Not verified
  here, and not verifiable from this checkout: the live deploy configs are git-excluded
  host-specific files under a control-plane path this run must not read. Stated as a
  reviewer question in the PR body rather than as a fact. The campaign arm that exposed the
  bug wants exactly the new behaviour, and any operator hitting this was already not
  getting the value they asked for.
- **`diff-cover` sees no template lines**, so patch coverage is decided entirely by the new
  test file. Keep the tests in `tests/unit/`, where the gate collects them.
