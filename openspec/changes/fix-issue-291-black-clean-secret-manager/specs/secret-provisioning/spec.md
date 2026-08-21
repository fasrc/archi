## ADDED Requirements

### Requirement: The secrets manager module is black-clean

`src/cli/managers/secrets_manager.py` SHALL satisfy `black --check` and `isort --check` at the
versions the gate pins (black 24.10.0, isort 6.0.1 with `profile = "black"`), so that a later
edit of one line in the module produces a patch of one line.

`scripts/gate.sh` runs black in **two different modes**, and the distinction is what makes an
unformatted module expensive:

| mode | selects | what black does | where |
| --- | --- | --- | --- |
| `_check_format_scope` (`scripts/gate.sh:65-71`) | directory args `src tests scripts` | `black --check` — reports, rewrites nothing | CI, when `$CI` is set; mirrored by the `lint` job at `.github/workflows/pr-preview.yml:29-33` |
| `_format_changed` (`scripts/gate.sh:74-80`) | explicit paths from `git diff` | `black` — **rewrites in place** | the local pre-commit hook |

The local writer is the one that taxes an edit. It rewrites the touched file before the gate
scores patch coverage with `diff-cover --fail-under=80`, so a module that is not already
black-clean drags the whole reflow into the patch, uncovered reflowed lines pull the score below
the floor, and the gate fails for reasons unrelated to the edit.

Measured on `origin/dev` at `07e007df`, this module carried 81 lines of pending black churn in
185 lines of source, which is what made the operator-facing `validate_secrets` message uneconomic
to improve in issue #287. That method sits at `:123-141` on `07e007df`; this change's reflow
moves it to `:144-162`, so read the anchor against the commit it names.

**This requirement is currently unenforced by CI, and that is a known gap, not an oversight.**
`.gitignore:19`'s bare `*secrets*` pattern matches this file's basename, and black honours
`.gitignore` when it walks a directory. The CI whole-scope assert passes directory arguments
(`scripts/gate.sh:70`), so the walk skips this file; the local pre-commit writer passes explicit
changed paths (`scripts/gate.sh:78`), so it does not. The invariant above therefore rests on the
local hook alone: a hand-edit that lands without it re-introduces the drift silently, and CI
stays green. Narrowing the ignore rule would weaken a secret-leak guard, so closing the gap is
tracked separately as issue #313 rather than done here (design.md, Decision 6). Read this
requirement as "the module is black-clean and must be kept so", not as "an automated check
guarantees it".

That CI-blind-spot claim is stated here as description and deliberately **not** written as a
scenario. The dividing line is what a statement is *about*, not whether a test runs it. A
scenario states a requirement on the thing this capability governs — "the module is
black-clean" is one, and it stays a scenario even while nothing automated checks it, because the
paragraph above says so plainly. "CI's directory walk skips this file" is not a requirement on
the module at all; it is an observation about the tooling, it is nobody's contract to uphold, and
it becomes false the day #313 lands. Observations that expire belong in prose. Issue #313 carries
the executable pin.

Note for whoever takes #313: the `gate` CI job installs black 24.10.0
(`.github/workflows/ci.yml:47-51`) and runs `pytest tests/unit/`, so a test that shells out to
black works there — but the separate `unit-tests` job installs only
`requirements/requirements-base.txt` and `pytest`, so the same test needs a guard or it errors in
that job.

#### Scenario: The module needs no reformatting

- **WHEN** `black --check src/cli/managers/secrets_manager.py` is run
- **THEN** it exits 0 and reports nothing to reformat
- **AND** `isort --check` on the same path exits 0

#### Scenario: A one-line edit yields a one-line patch

- **WHEN** a maintainer changes a single statement in the module and runs the local pre-commit gate
- **THEN** the writer step (`_format_changed`) leaves the rest of the module untouched
- **AND** the patch `diff-cover` scores contains that edit rather than a whole-file reflow

### Requirement: A formatting change to the module preserves its syntax tree

A change to `src/cli/managers/secrets_manager.py` that claims to be formatting-only SHALL be
verified by comparing the module's abstract syntax tree before and after, and the two SHALL be
identical.

The proof SHALL be `ast.dump(ast.parse(source))` equality. It SHALL NOT be `git diff -w`.
`-w` ignores whitespace *within* a line but not a change of line *boundaries*, so it still
reports every line black wrapped — on this module, the `logger.warning(...)` call in
`__init__`, the `get_secrets(...)` signature, and the `required = ... | ...` expression. A
criterion that reports differences on a correct reformat is not usable as a gate: it either
blocks correct work or gets relaxed until it accepts anything. `ast.dump` omits line and column
information, so re-wrapping is invisible to it while a renamed symbol, a changed default, or a
reordered expression is not.

#### Scenario: A pure reformat is accepted

- **WHEN** the module is reformatted by black and isort with no other edit
- **THEN** `ast.dump(ast.parse(...))` of the module before and after are equal
- **AND** the full unit suite passes with the same counts as before the reformat

#### Scenario: A behavioural edit smuggled into a reformat is caught

- **WHEN** a change to the module alters a symbol name, a parameter default, or the order of an
  expression alongside the reformatting
- **THEN** the syntax-tree comparison reports a difference
- **AND** the change is not presented as formatting-only

### Requirement: Required secrets are derived from the configured model names

`SecretsManager._get_model_based_secrets` SHALL scan every section of every models config
returned by `config_manager.get_models_configs()` and SHALL require `OPENAI_API_KEY` for a
model name containing `OpenAI` and `ANTHROPIC_API_KEY` for one containing `Anthropic`.

A models config section that is not a mapping SHALL be skipped rather than raising. An
open-source model name — one containing `HuggingFace`, `Llama`, or `VLLM` — SHALL NOT add a
required secret, and SHALL instead emit a warning that a HuggingFace token may be needed but is
not enforced. The three name tests are an ordered `if`/`elif` chain, so a name matching more
than one provider resolves to the first match.

This requirement records behaviour that already exists **in the code**, and it is specified here
because these lines had no test reaching them, which is what blocked the module from being
reformatted.

**The model-name loop is unreachable in production, and the requirement must be read that way.**
`get_models_configs()` has exactly one implementation and it returns a constant empty list
(`src/cli/managers/config_manager.py:471-473`, docstring "Legacy models configuration accessor
(archi section removed)"). `ConfigurationManager` has no subclass, and all four
`SecretsManager(...)` construction sites pass one (`src/cli/cli_main.py:183, 572, 584, 812`). So
no key is derived from a model name today and the open-source warning never prints. The config
shape the loop reads was removed with the `archi` section, so no user configuration reaches it
either.

What this requirement pins is therefore the behaviour of **dormant code**: it exists so that
reviving the accessor, or deleting the loop, is a visible decision rather than a silent one.
Issue #314 carries that decision. The second half of `_get_model_based_secrets` — the
`huit_bedrock` scan over `config_manager.get_configs()` — is live, and the requirement below
covers it.

#### Scenario: A commercial provider requires its key

- **WHEN** a models config names a model containing `OpenAI`, and another containing `Anthropic`
- **THEN** the derived secret set contains `OPENAI_API_KEY` and `ANTHROPIC_API_KEY`

#### Scenario: An open-source model requires no secret

- **WHEN** a models config names only models containing `HuggingFace`, `Llama`, or `VLLM`
- **THEN** no key is added to the derived secret set for them
- **AND** a warning is logged stating that a HuggingFace token is not explicitly enforced

#### Scenario: A non-mapping config section is skipped

- **WHEN** a models config contains a section whose value is not a mapping
- **THEN** that section is skipped and no exception is raised
- **AND** the remaining sections are still scanned

### Requirement: Secrets are provisioned to disk for compose in two forms

`SecretsManager.write_secrets_to_files` SHALL write each required secret to
`<target_dir>/secrets/<name-lowercased>.txt` containing only that secret's value, and SHALL then
also write a `.env` file in `target_dir` via `write_env_file`.

Both forms are written because podman-compose does not reliably support docker secrets, so the
`.env` file supplies the same values by environment-variable interpolation. A required secret
absent from the loaded `.env` SHALL cause `write_secrets_to_files` to raise `ValueError` naming
that secret, rather than writing an empty file. `write_env_file` SHALL emit one
`NAME=value` line per secret it can resolve and SHALL skip a secret it cannot, because the
missing-secret case is already refused by the caller. `get_env_file_path` SHALL return the
path of the `.env` file the manager loaded.

This requirement records behaviour that already exists, for the same reason as the previous one.
Unlike the model-name loop above, `write_secrets_to_files` is live: `src/cli/cli_main.py:288`,
`:577`, and `:863` all call it, and it calls `write_env_file` itself. One caveat for
completeness — `get_env_file_path` has no caller anywhere in the repository outside its own
test, so the clause covering it pins an accessor nothing currently reads (noted in #314).

#### Scenario: Each secret becomes a lowercased file

- **WHEN** `write_secrets_to_files` is called with a target directory and a set of secrets that
  are all present in the loaded `.env`
- **THEN** `<target_dir>/secrets/<name-lowercased>.txt` exists for each, holding just its value
- **AND** `<target_dir>/.env` exists containing a `NAME=value` line for each

#### Scenario: A missing secret is refused, not written empty

- **WHEN** `write_secrets_to_files` is asked for a secret that the loaded `.env` does not define
- **THEN** it raises `ValueError` naming that secret
- **AND** no file is left holding an empty value for it

#### Scenario: The env writer skips what it cannot resolve

- **WHEN** `write_env_file` is called directly with a mix of defined and undefined secret names
- **THEN** the written `.env` has a line for each defined name and no line for the others
- **AND** no exception is raised

#### Scenario: The loaded env file path is readable

- **WHEN** `get_env_file_path` is called
- **THEN** it returns the path the manager loaded its secrets from
