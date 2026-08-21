# Design

## Context

This is a formatting change with a coverage prerequisite. The interesting decisions are all
about *proof* — how to demonstrate that a whole-file reflow changed nothing — and about the
order of commits, because the naive order cannot be committed at all.

Measurements below were taken on branch `fix/issue-291-black-clean-secret-manager` at
`origin/dev` = `07e007df`, with black 24.10.0 / isort 6.0.1 from
`/home/austin/miniforge3/envs/archi/bin`.

## Decision 1 — Prove behaviour preservation by AST equality, not `git diff -w`

Issue #291's acceptance criterion 2 says `git diff -w -- <file>` should show "no substantive
change". **That check is wrong and must not be used as the gate on this change.** `-w` ignores
whitespace differences *inside* a line; it does not collapse a line that black split into
three. Run against the actual reformat, `git diff -w` still reports:

- the `logger.warning(...)` in `__init__` split across three lines,
- the `get_secrets(...)` signature split across three lines,
- `required = self.get_required_secrets_for_services(services) | ...` split across three lines.

A reviewer told "`-w` should be empty" would read that output as a behaviour change, or worse,
would relax the criterion until it passed.

The exact test is that the parsed syntax tree is unchanged:

```python
import ast
before = ast.dump(ast.parse(old_source))
after  = ast.dump(ast.parse(new_source))
assert before == after
```

`ast.dump` omits line numbers and column offsets by default, so re-wrapping is invisible to it
while any real edit — a renamed symbol, a changed default, a reordered expression — shows up
immediately. Note that isort's import reordering *would* change the tree; on this file isort is
a no-op, and the task list checks that rather than assuming it.

**Alternative rejected:** comparing tokenized output (`tokenize`) with whitespace tokens
filtered. It works, but it is more code than `ast.dump` and it still sees the difference
between `config_manager = None` and `config_manager=None`, which black does change here and
which is not a behaviour change.

## Decision 2 — Add characterization tests, and add them *first*

The reformat cannot be committed on its own. Measured:

| quantity | value |
| --- | --- |
| black churn on the file (`--check --diff`, `+`/`-` lines) | 81 |
| lines in the resulting patch | 51 |
| of those, measurable statements | 16 |
| covered by the suite as it stands | 8 |
| **patch coverage** | **50%** |
| gate floor | 80% |

The eight uncovered lines and their homes:

| line | member |
| --- | --- |
| 87, 90, 95, 100 | `_get_model_based_secrets` — the model-name scan loop and its open-source warning |
| 180, 184 | `write_secrets_to_files` — the per-secret file write, and the `KeyError` → `ValueError` re-raise |
| 195 | `write_env_file` — the compose `.env` write |
| 209 | `get_env_file_path` |

So the commit order is: **tests first, reformat second.** Two commits, each green on its own:

1. `test(secrets): cover the members a black reflow will touch` — a test-only diff. Coverage
   is measured over `src/` only, so a test-only patch has no coverable lines and clears
   `diff-cover` trivially. The suite is green before and after.
2. `style(secrets): reformat to black` — the src reflow. Its patch, measured against
   `origin/dev`, now contains both the new test file and the reflowed module; the reflowed
   lines are covered by commit 1, so patch coverage is ~100%.

**On the red-first rule.** This project's standard is a failing test before implementation.
A pure reformat has no new behaviour to drive from red, and manufacturing a red test here
would produce a task that ends with the suite red — which can never be committed, because the
gate runs pre-commit. The red-state proof for this change is therefore the **gate failure
itself**: task 1 records `diff-cover` refusing the reformat at 50%, and task 3 records the
same measurement passing. The characterization tests are asserted to pass against the
*unformatted* module — that is the point of them. A characterization test that fails before
the reformat would mean it had encoded the reformat's behaviour rather than today's, and is a
bug in the test.

**Alternative rejected:** `# pragma: no cover` on the reflowed lines, or lowering the floor
for this patch. Both make the gate lie, and neither leaves the module any more editable than
it is now — the next behavioural edit would re-open the same hole.

## Decision 3 — Depart from "exactly one file", and say so

Issue #291 acceptance criterion 5 requires the PR to touch exactly one file. Given Decision 2
that is unsatisfiable, so this change targets two files: the reformatted module and one new
test module. The substantive constraint the issue is protecting — *no behaviour change mixed
into the formatting commit* — is fully honoured; tests add no behaviour. The departure is
recorded here, in the spec, and must appear in the PR body so a reviewer is not left to
discover it.

**Alternative rejected:** two PRs, one for tests and one for the reformat. It satisfies the
letter of the criterion, doubles the review load, and leaves a window where the test PR is
merged and the reformat is not — during which the file is still a trap. The two commits inside
one PR give a reviewer the same separability.

## Decision 4 — Keep the diff to the one module even though the gate formats the whole repo

The gate applies black across the repo, so a run can reformat files this change never
intended to touch. Stage the two intended paths explicitly, and check `git status` is clean
after committing — if the gate reformatted something else, that shows up as leftover modified
files rather than as a silent extra hunk in this PR.

This also interacts with a known trap: editing `requires-python` in `pyproject.toml` retargets
black and reformats the whole repo. This change must not touch `pyproject.toml`.

## Decision 5 — Repoint the root `tasks.md` symlink; do not touch the test that trips on it

The repo root has a gitignored `tasks.md` symlink (`.gitignore:64`) used as a human's live
OpenSpec pointer. On this branch it dangles — it points into a change directory that only
exists on another branch — and
`tests/unit/test_python_version_declaration.py::test_every_page_stating_a_minimum_is_guarded`
globs root markdown, follows it, and fails with `FileNotFoundError`. That failure is
environmental, is present before this change touches anything, and must not be "fixed" in the
test. Repoint the symlink at this change's own `tasks.md` — it is gitignored, so repointing
does not dirty the tree, and it is also what points the executor at this task list.

## Decision 6 — Reformat the file, but do not touch the ignore rule that let it drift

Why this one file drifted while the other 343 in the enforcement scope stayed clean:
`.gitignore:19` carries a broad `*secrets*` pattern. A gitignore pattern with no slash matches
a **basename at any depth**, and `secrets_manager.py` contains `secrets`, so the rule matches
this tracked source file. Verified:

```
$ git check-ignore -v --no-index src/cli/managers/secrets_manager.py
.gitignore:19:*secrets*  src/cli/managers/secrets_manager.py
```

(Plain `git check-ignore` reports nothing, because git does not apply ignore rules to files it
already tracks; `--no-index` shows the rule that matches.)

black and isort respect `.gitignore` when they **walk a directory**, but not when a path is
**named explicitly**. That splits the gate in two:

| gate mode | how it selects files | sees this file? |
| --- | --- | --- |
| CI, whole-scope assert (`_check_format_scope`) | directory args `src tests scripts` | **no** — the walk skips it |
| local pre-commit writer (`_format_changed` via `_changed_py`) | explicit paths from `git diff --name-only` | **yes** |

Measured both ways on `origin/dev` at `07e007df`:

```
$ black --check src tests scripts          ->  343 files would be left unchanged   (green)
$ black --check src/cli/managers/secrets_manager.py
                                           ->  1 file would be reformatted         (dirty)
```

This is the whole mechanism behind issue #291, and it is worth stating precisely, because it
explains a fact that otherwise looks impossible: CI is green on `dev` **and** the file is
misformatted. The enforcing check cannot see the file; the writer that reflows it on edit can.

**Consequence for this change:** reformatting the file removes today's 81 lines of churn, but
nothing prevents a recurrence. The CI assert still cannot see the file, so a later hand-edit
that lands without the local hook re-introduces the drift silently.

**Not fixed here, deliberately.** The durable fix is to stop `*secrets*` from matching tracked
source — anchor the rule to the directories and env files it is meant to protect, or add an
explicit negation for this one module. That rule is a defence-in-depth guard against committing
real secret material (`.gitignore:18-19` and the default-deny block below it), and narrowing it
wrongly makes a secret-bearing file trackable. Weakening a secret-leak guard is not a decision
to take unattended, and it is not what issue #291 asked for. Leave the rule alone, and raise it
for a human as a follow-up filed against the ignore rule, not against this module.

## Decision 7 — No new path may contain the substring `secrets`

The same `.gitignore:19` rule from Decision 6 has a second, sharper consequence, hit while
authoring this change: `*secrets*` matches a **directory** basename as well as a file's, and git
refuses to add an ignored path without `-f`. Three of this change's own intended paths were
silently unaddable:

| intended path | verdict |
| --- | --- |
| `openspec/changes/fix-issue-291-black-secrets-manager/` | ignored — change artifacts could not be committed |
| `openspec/changes/.../specs/secrets-provisioning/` | ignored — and would be ignored again at `openspec/specs/` on archive |
| `tests/unit/test_secrets_manager_provisioning.py` | ignored — the new test file could not be committed |

`git add` reports this as a hint and exits without staging, so the failure mode is a commit
that appears to succeed while containing nothing. It is only visible if `git status` is checked
afterwards.

Hence the names this change actually uses, all with `secret` singular:

- branch `fix/issue-291-black-clean-secret-manager`
- change `openspec/changes/fix-issue-291-black-clean-secret-manager/`
- capability `secret-provisioning`
- test module `tests/unit/test_secret_manager_provisioning.py`

`src/cli/managers/secrets_manager.py` keeps its name — it is already tracked, and git does not
apply ignore rules to tracked files. Renaming it is out of scope, and would be a behaviour-
adjacent change to every import site.

**Rule for the executor:** before creating any new file or directory for this change, check
`git check-ignore -v --no-index <path>`. If it reports a match, rename the path. Do **not**
reach for `git add -f` — that tracks a file the ignore rules say should not be tracked, and the
next person to read the rule cannot tell it was deliberate.

## Round-1 decisions (adversarial review of PR #308)

The local Codex adversarial pass returned `needs-attention` with two medium findings and
**no semantic delta in the reformat itself**. Both findings held on verification.

### D8 — The spec delta records an invariant CI does not enforce

`design.md` Decision 6 and `proposal.md`'s risk list both state the recurrence hole plainly, so
the branch never hid it. The **spec delta** did not, and the spec delta is the artifact that
archives into `openspec/specs/` and outlives this change. A future reader would take
"`src/cli/managers/secrets_manager.py` SHALL satisfy `black --check`" as a guarded contract when
nothing in CI guards it.

Fixed in the spec text rather than in the ignore rule: the requirement now states that CI's
directory-walk assert cannot see the file, that the invariant rests on the local pre-commit
writer alone, and that closing the gap is issue #313. (Round 2 revisited how that gap is
expressed — see D10; it is descriptive prose, not a scenario.)

Rejected the reviewer's first option — "narrow the requirement to the current reformat only".
The requirement is not wrong; it is unguarded. Narrowing it to a one-time event would lose the
statement that the module must *stay* black-clean, which is the whole point of #291. Rejected the
second option — fix the ignore rule here — for Decision 6's reason: it weakens a secret-leak
guard, and issue #291 asked for a formatting-only change. Issue #313 carries both options and a
third the reviewer did not raise: an explicit-path formatter test, which closes the hole without
touching `.gitignore` at all.

### D9 — Three characterization tests were coverage-shaped, not behaviour-shaped

The reviewer's charge was that the tests lift patch coverage without pinning the contract. Three
concrete gaps, all confirmed against the code:

Line anchors below are in `src/cli/managers/secrets_manager.py` **as of commit `8956f5be`**, the
reformat. Read them with `git show 8956f5be:src/cli/managers/secrets_manager.py` rather than
against a later tree.

| gap | why the old test could not catch it |
| --- | --- |
| `continue` vs `break` on a non-mapping section (guard `:88`, `continue` `:89`) | the config had one section, so both control-flow choices give `set()` |
| the `evaluator_provider` arm of `if "huit_bedrock" in (sut_provider, evaluator_provider)` (assignment `:119-121`, branch `:122`) | only the `sut_provider` arm was exercised, and black re-wrapped that assignment |
| "no file is left holding an empty value" (spec scenario) | the test asserted only that `ValueError` was raised |

Two tests added and one existing test strengthened, taking the file from 8 to 10 tests. Each of
the three was proved to have teeth by mutating the source and confirming that test — and only
that test — turns red:

| mutation | test that fails |
| --- | --- |
| `continue` -> `break` | `test_a_non_mapping_section_does_not_stop_the_remaining_sections` |
| `in (sut_provider, evaluator_provider)` -> `in (sut_provider,)` | `test_huit_bedrock_as_the_ragas_evaluator_requires_huit_api_key` |
| `touch()` the secret file before resolving it | `test_secret_absent_from_env_raises_value_error_naming_it` |

One part of the reviewer's reasoning does not hold and is recorded rather than adopted: it said
the spec "calls out" `ragas_settings.evaluator_provider`. The spec delta does not specify the
HUIT path at all. The test was added anyway, on the stronger ground that black re-wrapped that
expression, which puts it squarely inside what a characterization test for this reflow must
cover.

## Round-2 decisions (adversarial review of PR #308)

Round 2 returned `needs-attention` with two medium findings, both on the spec text and both
confirmed. It again found no semantic change in the reformat.

### D10 — Two spec defects that round 1 introduced or left standing

**The requirement described a gate that does not exist.** The opening paragraph said "The gate
runs black as a formatter rather than a checker: it rewrites files in place". That is true of
one of the gate's two modes and false of the other. `_check_format_scope`
(`scripts/gate.sh:65-71`) runs `black --check` and rewrites nothing; it is what runs when `$CI`
is set, and the `lint` job mirrors it (`.github/workflows/pr-preview.yml:29-33`).
`_format_changed` (`scripts/gate.sh:74-80`) is the writer, and it runs only from the local
pre-commit hook. Since the spec archives into `openspec/specs/`, an absolute claim about the
control plane would mislead every later reader. Replaced with a two-row table naming both modes
and their line anchors, and the rationale re-based on the writer specifically.

**Round 1's fix introduced the defect it was fixing.** To record the CI blind spot, round 1
added `#### Scenario: CI cannot detect a later drift in this module`. A scenario is a normative
check, and nothing in this repository executes that one — no test touches `scripts/gate.sh` path
selection or the `lint` job's scope. So round 1 answered an unbacked claim with another unbacked
claim, one step further into normative language. The scenario is demoted to descriptive prose
inside the requirement, and the requirement now says outright why it is prose and not a
scenario. Issue #313 carries the executable pin.

Recorded for #313 rather than acted on here: the `gate` CI job installs black 24.10.0
(`.github/workflows/ci.yml:47-51`) and runs `pytest tests/unit/`, so a unit test that shells out
to `black --check` on an explicit path would work there. The separate `unit-tests` job installs
only `requirements/requirements-base.txt` and `pytest`, so the same test errors in that job
without a guard. That constraint shapes the fix, and it is cheaper to write down now than to
rediscover.

### D11 — The pre-commit writer can commit content it has already rewritten

Hit while landing the round-1 fixes, and worth recording because the failure mode is invisible
locally. `_format_changed` rewrites the **working tree**. Content staged before the hook runs is
already snapshotted, so black's rewrite lands in the working tree and not in the commit. The
result is a commit carrying unformatted content while the working tree holds the formatted
version — and every local `black --check` then reads the working tree and passes.

CI caught it because the `lint` job reads the commit, not the working tree. Reproducing it also
needs the right tree: `pull_request` checks out the **merge ref**, not the branch head.

```
git fetch origin refs/pull/<N>/merge
git worktree add <dir> FETCH_HEAD
black --check src tests scripts     # 1 file would be reformatted
```

**The check that catches it is `git status --porcelain` immediately after the commit** — the
task list already demands it for `src/`, and it applies to every path in the commit, not just
the reformatted module. Fixed in a follow-up commit rather than an amend, because the prior
commit was already pushed and cited by SHA in the PR's round-1 log.

## Round-3 decisions (Greptile, and adversarial review round 3, on PR #308)

### D12 — Two of the four characterized members are not reachable in production

Greptile's P2 on `tests/unit/test_secret_manager_provisioning.py`, confirmed by tracing the
accessor to its only implementation.

`_get_model_based_secrets` opens by deriving `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` from model
names in `config_manager.get_models_configs()`. That accessor has exactly one implementation and
it returns a constant empty list (`src/cli/managers/config_manager.py:471-473`, docstring "Legacy
models configuration accessor (archi section removed)"). `ConfigurationManager` has no subclass,
and all four construction sites pass one (`src/cli/cli_main.py:183, 572, 584, 812`). The loop
body therefore never executes in production.

A second, milder case found in the same sweep: `get_env_file_path` has no caller anywhere in the
repository outside its own test.

**Not a defect in this change** — the reformat is unaffected, and the tests still earn their
keep: they pin what the code does today, so reviving the accessor or deleting the loop becomes a
visible decision. What *was* a defect is the spec delta describing the derivation as live
production behaviour. Both requirements now state their reachability, and issue #314 carries the
delete-or-restore decision.

Scope of the live parts, so the record is complete: the `huit_bedrock` scan over
`get_configs()` is live, and `write_secrets_to_files` is called from `src/cli/cli_main.py:288`,
`:577`, and `:863`.

### D13 — Every anchor is verified against a named commit, not against "the file"

Round 3 caught two anchors in D9 that were off by a line or a range: the `continue` is `:89`, not
`:88` (`:88` is its guard), and the `huit_bedrock` branch is `:122`, while `:119-121` is only the
`evaluator_provider` assignment. The spec carried a third: `validate_secrets` at `:123-141` was
measured on `07e007df`, and this change's own reflow moves it to `:144-162` — an anchor that a
reader after the merge would follow into the wrong method.

That is a class, not three slips: a file:line written while a change is in flight is anchored to
a tree that the change itself moves. Every citation in this change directory was re-derived and
each now names the commit it is read against. D9 states `8956f5be` explicitly.

### D14 — Where a scenario is the wrong shape, and where it is not

Round 3 argued that if the CI-blind-spot claim had to be demoted for having no executable check,
then `Scenario: The module needs no reformatting` and `Scenario: A one-line edit yields a
one-line patch` must go too, since nothing runs those either.

Not adopted, and the round-2 wording that invited the reading is corrected instead. Round 2 said
"a normative check nothing runs is the same defect", which is too strong: it would empty most of
the spec set, since specs state contracts and tests verify them. The real dividing line is what a
statement is *about*.

- "The module is black-clean" is a requirement **on the thing this capability governs**. It stays
  a scenario. Nothing automated checks it yet, and the requirement says so in bold two paragraphs
  above, which is where that belongs.
- "CI's directory walk skips this file" is **an observation about the tooling**. It is nobody's
  contract to uphold, and it becomes false the day #313 lands. Observations that expire belong in
  prose.

The spec now states that distinction where the demotion is explained, so the next reader does not
have to re-derive it.

## Review loop, terminal state

Four rounds against the local Codex CLI, plus one Greptile pass. The GitHub Codex connector was
asked twice and answered both times with "You have reached your Codex usage limits for code
reviews", so it contributed nothing — a silent connector on this repo means no review happened,
not a clean review.

| round | reviewer | verdict | findings | adopted |
| --- | --- | --- | --- | --- |
| 1 | Codex CLI | needs-attention | 2 medium | 2 |
| 2 | Codex CLI | needs-attention | 2 medium | 2 |
| 3 | Greptile + Codex CLI | 4/5 + needs-attention | 1 P2 + 2 medium | 2 of 3 — D14 records the rejection |
| 4 | Codex CLI | **approve** | none | — |

Seven findings, six adopted. **Not one was in the reformat**: every round confirmed the module's
syntax tree is identical to `origin/dev`. Every finding was in the surrounding artifacts, and all
but one were the same defect — a claim the code, the workflows, or production reachability does
not support. That pattern is the reusable lesson here. A formatting-only change is trivially
verifiable by AST equality, and the risk migrates entirely into what the change *says about
itself*, which nothing executes.

Two follow-ups came out of the loop and neither blocks this change: **#313**, the CI formatting
blind spot, and **#314**, the dead model-name loop.
