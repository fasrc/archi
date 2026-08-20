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
