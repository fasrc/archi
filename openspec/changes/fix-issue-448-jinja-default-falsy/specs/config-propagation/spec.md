## ADDED Requirements

### Requirement: A configured falsy value survives config template rendering
Rendering `base-config.yaml` SHALL substitute a default only when the operator did not configure the key, so a key configured with a falsy value (`false` or `0`) reaches the rendered runtime config with that value and that type.

A key is "not configured" when it is absent from the input config (including any absent
ancestor section, which the render environment resolves to a chainable undefined) or when
it is present with an explicit `null`. A key configured with `false` or `0` is configured,
and the operator's value SHALL be rendered.

The rendered value SHALL keep its YAML type: a boolean flag renders as a YAML boolean and
never as the string `'None'` or the string `'False'`.

#### Scenario: HTML-to-markdown processing is turned off
- **WHEN** the input config sets `data_manager.processing.html_to_markdown.enabled: false`
- **THEN** the rendered config parses to `False` as a boolean at that key

#### Scenario: A source is turned off
- **WHEN** the input config sets `data_manager.sources.git.enabled: false`
- **THEN** the rendered config parses to `False` as a boolean at that key

#### Scenario: A service is turned off
- **WHEN** the input config sets `services.data_manager.enabled: false`
- **THEN** the rendered config parses to `False` as a boolean at that key

#### Scenario: Every boolean flag in the template honors an explicit false
- **WHEN** the input config sets any one of the template's boolean flags to `false`
- **THEN** the rendered config parses to `False` as a boolean at that key

#### Scenario: An unset key still renders its documented default
- **WHEN** the input config omits a boolean flag, or omits the whole section containing it
- **THEN** the rendered config parses to that flag's documented default as a boolean

#### Scenario: An explicitly null key renders the default, not the string None
- **WHEN** the input config declares a boolean flag with an explicit `null` value
- **THEN** the rendered config parses to that flag's documented default as a boolean
- **AND** the rendered value is not the string `'None'`

#### Scenario: A configured zero survives where zero is a meaningful bound
- **WHEN** the input config sets `data_manager.sources.links.base_source_depth: 0` or
  `data_manager.sources.links.sitemap.max_pages: 0`
- **THEN** the rendered config parses to the integer `0` at that key
- **AND** with the same key unset the rendered config parses to `1` and `20000` respectively

#### Scenario: A configured zero survives on a cap whose default is null
- **WHEN** the input config sets `data_manager.sources.links.max_pages: 0` or
  `data_manager.sources.elog.max_entries: 0`
- **THEN** the rendered config parses to the integer `0` at that key
- **AND** with the same key unset the rendered config parses to null at that key
- **AND** the rendered value is never the string `'None'`

### Requirement: The truthiness-substituting default form cannot return to the template
A test SHALL parse `base-config.yaml` with the Jinja parser and fail the build if any `default` filter call carries a boolean-literal default in any shape other than `default(false, true)`, in any arity and any capitalisation.

The same test SHALL freeze the remaining non-boolean `default(<value>, true)` call sites —
falsy scalars as well as truthy ones — against a recorded baseline, so the deprecated form
can be removed over time but never added. Freezing only the truthy ones leaves
`default(0, true)` with nowhere to be caught: it substitutes for a configured `false` and
renders the integer `0`, and it is not a boolean literal, so the rule above does not see it
either.

The test SHALL read the `boolean` argument in both spellings. `default(7, true)` and
`default(7, boolean=true)` are the same call, and Jinja parks the keyword spelling in a
different field of the parsed node, so reading only the positional form would admit the
deprecated call under a new name and reject the one permitted form written that way. The baseline SHALL identify each call site by the configuration path it reads
and the default it carries, not by a count and not by a line number. A count cannot see a
swap — converting one frozen site while adding the deprecated form at another key leaves
the total unchanged — and a line number renumbers on every edit above it. The failure
message SHALL name the paths that arrived and the paths that left, and state the
replacement form.

The template SHALL state the rule once, near the top of the file: a boolean flag, and any
number whose `0` is meaningful, uses `{%- set %}` plus an `is defined and is not none`
ternary, and `default(<value>, true)` is deprecated in this file.

#### Scenario: A reintroduced boolean truthiness default fails the build
- **WHEN** any call site in the template is written as `default(true, true)`, in any
  spacing or capitalisation, or as `default(true)`
- **THEN** the guard test fails and names that line

#### Scenario: A new truthy non-boolean site fails the build
- **WHEN** a call site is added as `default(<truthy string or number>, true)`
- **THEN** the guard test fails, because the count of such sites exceeds the frozen baseline

#### Scenario: A comment mentioning the pattern does not fail the build
- **WHEN** the template contains the text `default(true, true)` inside a comment
- **THEN** the guard test passes, because it reads the parsed template and not the raw text

#### Scenario: The falsy-default sites are left alone
- **WHEN** the template contains `default('', true)`, `default([], true)`,
  `default({}, true)`, or `default(false, true)`
- **THEN** the guard test passes for those sites, because a falsy default substitutes the
  same value the falsy input already carries

#### Scenario: A boolean default without truthiness substitution fails the build
- **WHEN** a boolean flag is written as `default(true)`, `default(false)` or
  `default(true, false)`
- **THEN** the guard test fails and names that line, because an explicit `null` at that key
  would render as the truthy string `'None'`
