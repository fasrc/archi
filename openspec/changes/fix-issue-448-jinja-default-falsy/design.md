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
`0` an operator can mean, and none of the four is expressible today.

`sitemap.max_pages` is the odd one and an earlier draft described it wrongly as "no page
budget". It is a validation ceiling checked after expansion, not a budget: `sitemap_source`
raises when `count > max_pages` and raises again when `count < min_pages`. So `0` means
"fail if this sitemap emits any page", and paired with `min_pages: 0` it asserts the
sitemap is empty. Preserving the `0` still beats rewriting it to `20000`, which silently
accepts twenty thousand pages from an operator who asked for none — but the meaning had to
be stated correctly, in the design and in the operator docs. The `min_pages` line
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

**"Only those four" scopes this change, not the file.** Three numeric keys already
preserved a configured `0` before it, through the same ternary:
`data_manager.scrape_workers` (default `8`), `data_manager.scrape_per_host_workers`
(default `4`) — both carrying a comment that says exactly why — and
`sources.links.sitemap.min_pages` (default `1`). Review round 5 caught the operator docs
claiming the four converted here were the only numeric keys where a `0` survives, which is
false; corrected there. Nothing changes here, but a reader comparing the two documents
needs the distinction.

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

## D8. Where "rendered" stops and "honored" begins

Review round 3 raised five findings asking this change to make the newly rendered flags
take effect: wire `services.data_manager.enabled` into deployment planning, honor
`links.enabled` in `ScraperManager`, normalize `visible` against stored `source_type`
values, and gate `git-`/`sso-`/`elog-` entries in `input_lists` behind their source flags.

**They describe real gaps, and none of them is this change.** This change moves a Jinja
filter. Every gap above predates it: before the fix, an explicit `false` never reached the
config at all, so no consumer could have honored it either. Fixing the rendering does not
regress anything and does not, by itself, deliver the flag's promise — those are two
separate pieces of work, and merging four subsystem changes into a template fix would make
the diff unreviewable and the bisect useless.

What this change owes the operator is an **honest account of which flags now do what**,
and that account was wrong in five places. Corrected in `docs/docs/configuration.md`:

| Claim | Measured |
| --- | --- |
| `local_files.enabled` — "no consumer reads it" | wrong. `stage_local_files_to_volume()` returns without staging when it is `false`, logging `local_files disabled; skipping staging.` Removed from the list; its real limitation (an already-populated volume keeps its files) documented instead |
| `html_scraper.reset_data` is honored | wrong. It appears only in the template, tests and docs — `ScraperManager` reads `verify_urls` and `enable_warnings` out of the `html_scraper` block and nothing else. Added to the list |
| `visible: false` "removes that content from citations" | wrong for most sources. `ChatWrapper._get_doc_visibility` looks up `metadata["source_type"]` in `data_manager.sources`, but links, Indico and ELOG persist `web` and Jira and Redmine persist `ticket` — none of which is a config key. The lookup misses, logs `Source type … not found in config`, and defaults to visible. Works only for `git`, `sso` and `local_files`, whose stored type equals their key |
| `enabled: false` is acted on by the `git` and `sso` collectors | wrong when `input_lists` carries prefixed entries. `collect_all_from_config()` sets `git_enabled = True` for any `git-` URL and `sso_enabled = True` for any `sso-` URL without consulting the flag, and ELOG URLs are collected as `extra_urls` regardless. Documented as a CAUTION with the workaround |
| `redmine.visible` is one of the 21 | wrong. Its expression is still `default(false, true)`; the default is already `false`, so an explicit `false` always rendered as `false`. Listing it made the list 22 items long. Removed, along with the same note for `elog.visible` |

The two functional gaps with user-visible consequences are filed as their own issues rather
than carried as comments here:

- **#459** — the `visible`/`source_type` mismatch, which leaves content in chat citations
  that an operator asked to hide. End-user-visible, and silent from the operator's side.
- **#460** — the `input_lists` override, which can fetch a source configured as disabled,
  possibly after CLI validation skipped that source's required secrets.

The other three findings need no issue. `services.data_manager.enabled` and
`links.enabled` were already documented as not-yet-honored before this round, and
`reset_data` joins them; all three are inert flags, not wrong behaviour, and the
documentation is the correct disposition until someone decides they gate a release.

## D9. What the AST guard can and cannot read

Review round 3 also found a hole in the guard from D4: both predicates tested
`isinstance(first_arg, nodes.Const)`, and Jinja parses `-1` as `nodes.Neg` wrapping a
`Const`. So `default(-1, true)` — a truthiness substitution, the exact form D4 exists to
keep out — passed both advertised guards silently. Fixed by folding unary signs in
`_literal_default` before either predicate runs.

The fold declines booleans deliberately. `bool` is an `int` subclass, so folding
`default(-true, true)` would produce `-1` and report a truthy non-boolean — moving a
boolean-literal default out of the guard built to reject it. Pinned by
`test_guard_folds_a_signed_literal_without_losing_the_boolean_check`.

**The wider fix was tried and rejected on measurement.** The finding's alternative was to
fail on every `default(<expr>, true)` the walker cannot read. Implemented, that reported 12
lines in the current template: `default({}, true)`, `default([], true)` and
`default('localhost' if host_mode else 'data-manager', true)`. A dict, list or conditional
default cannot stand in for a boolean, so none is in the bug class — the same argument the
test module's docstring already makes for list defaults. Twelve false failures on lines
that were always correct is not a stronger guard. The boundary is now enforced by
`test_guard_leaves_container_and_computed_defaults_out_of_scope` instead of asserted in a
comment, so a future attempt at that widening fails a test that explains why.

## D10. The null guarantee is not a whole-file guarantee

Review round 4 found the opening paragraph's `null` promise as over-broad as its `0`
promise had been. Measured against the template:

| Input | Result |
| --- | --- |
| `global.DATA_PATH: null` | `/root/data/` — the default, as promised |
| `data_manager.sources.jira.max_tickets: null` | `10000000000.0` — the default |
| `services.chat_app.num_responses_until_feedback: null` | `3` — the default |
| **`global.ACCEPTED_FILES: null`** | **`TypeError: 'NoneType' object is not iterable`** |
| **`services.benchmarking.modes: null`** | **the same** |

Those two take a bare `default([...])` with no `boolean=true`, which replaces only an
*undefined* value, and both are iterated directly by a `{%- for %}`. So an explicit `null`
reaches the loop and raises. This is worse than rendering the wrong value: `archi create`
cannot render the config at all, so the deploy is blocked.

Swept the rest rather than fixing the two instances: every other bare-`default()` call site
with a container value — `ragas_settings.metrics`, `chat_app.providers`,
`anonymizer.names` — renders fine under an explicit `null`, because nothing iterates the
result at template level. Two keys, not a class.

Documented rather than converted. Adding `boolean=true` to those two would change what an
explicit empty list means at both sites (an empty `ACCEPTED_FILES` would silently become the
21-extension default, which is the truthiness-substitution bug this change exists to
remove), so it is a separate decision with its own blast radius. Pinned by
`test_null_on_an_iterated_container_key_fails_the_render`, with a contrast test for a
`default(…, true)` site, so the documented exception cannot rot: if a later change converts
these, the test fails and the doc paragraph comes out with it.

## D11. The change also makes SSO a source, and that needs saying

Review round 5 found the largest behaviour change in this PR, and it has nothing to do with
the `default` filter. Converting the `indico.use_sso` line replaced a `{% endif -%}` with
`{% endif %}` on the line above `sso:`, and the trailing `-` had been stripping the newline
that put `sso:` at its own indentation.

**Measured, rendering the template from `origin/dev` and from this branch.** At
`origin/dev`:

```yaml
    git:
      enabled: True
      visible: True
      schedule: ''
      sso:            # a null key inside git, not a source
      enabled: True   # SSO's rows, as DUPLICATE keys inside git:
      visible: True
      schedule: ''
```

`data_manager.sources` had no `sso` key at all — its keys were `elog, git, indico, jira,
links, local_files, redmine` — and YAML's last-wins rule meant the SSO block's rows replaced
Git's. Operator-visible consequences, each rendered both ways:

| Configuration | `origin/dev` | this branch |
| --- | --- | --- |
| `sources.git.enabled: false` | `true` — **discarded** | `false` |
| `sources.git.schedule: "0 3 * * *"` | `''` — **discarded** | `0 3 * * *` |
| `sources.sso.enabled: false` | `sources.sso` absent | `false` |
| nothing configured | no `sources.sso` key | `sso` with `enabled: true` |

So `git.enabled: false` has been silently ignored on `dev`, and this change starts honouring
it. That is a fix, not a regression — but it is a rollout event, because a deployment that
has been carrying an ignored `git.enabled: false` will stop ingesting Git on its next
`archi create`, and one with an SSO schedule and no explicit `sso.enabled` will start.

Documented as its own subsection with a CAUTION in `docs/docs/configuration.md`, rather than
folded into the falsy-value story, because an operator auditing the falsy-flag list would
never look for it there. Two tests pin it: the structural property (`sso` is a sibling, and
neither source lost a row) and the operator-visible one (`git.enabled: false` and
`git.schedule` survive). Neither would be caught by anything else in the file — it is an
indentation property of a Jinja template, and one re-added `-` puts it back.

Not reverted to keep the diff narrow. Leaving the marker would mean shipping a change that
converts `use_sso` while leaving `sso` structurally lost, and the next person to touch that
line would hit the same thing with less context.
