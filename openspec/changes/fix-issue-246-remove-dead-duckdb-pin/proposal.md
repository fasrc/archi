## Why

`requirements/requirements-base.txt:8` pins `duckdb==0.8.1`, and the two generated
base-image requirements files carry the same line
(`src/cli/templates/dockerfiles/base-python-image/requirements.txt:11`,
`base-pytorch-image/requirements.txt:12`). Nothing uses it. Re-derived on `origin/dev` at
`443351b6`:

- `git grep -n duckdb -- 'src/**/*.py' 'tests/**/*.py'` returns **nothing** — there is no
  `import duckdb` in the tree. Repo-wide, the string appears in exactly four files: the three
  requirements files above and `Containerfile`.
- It is **not** in `pyproject.toml`'s `dependencies` (`grep -n duckdb pyproject.toml` is
  empty), and that is the authoritative set for the `pip install .` every service image runs.
- Nothing resolves it transitively either. The loop image filters duckdb out of its install
  and the package is absent from that image entirely, yet the image builds and
  `pytest tests/unit/` there is green. If any dependency declared duckdb, pip would have
  installed it anyway.

The pin is not merely inert, it is costly. duckdb 0.8.1 ships no cp312 wheel and its C++
source does not build in a slim image, so `Containerfile:72` carries a bespoke
`grep -ivE '^[[:space:]]*duckdb([=<>!~ ]|$)'` filter whose entire purpose is to work around
this one line, with a four-line comment block at `:61-64` explaining it. It is also the only
pin in `requirements-base` blocking a future Python 3.12 move — the declared floor is
`>=3.11` (set by #201 / PR #237) and everything else in the file installs cleanly on 3.12.

Deleting it is not durable on its own. The pin was introduced upstream (`3218300c`,
2025-08-25) and `archi-physics/archi` still carries it in its own `requirements-base.txt`, so
the next upstream merge into this fork reintroduces it **silently** — no test fails, no image
breaks, and the dead pin is simply back. The removal therefore needs a guard, or this issue
gets refiled in six months.

## What Changes

- Delete the single `duckdb==0.8.1` line from each of the three requirements files —
  **exactly three deleted lines, one per file**, by surgical deletion rather than by
  regenerating the two derived files (design D1).
- Add `tests/unit/test_requirements_hygiene.py`: one guard asserting that none of the three
  files pins `duckdb`, collecting every offender so the failure names all of them at once
  rather than only the first. Its docstring states why it exists — upstream still carries the
  pin, so an upstream merge would otherwise reintroduce it unnoticed.
- The guard is written **red first**: it fails on `origin/dev` naming all three hits, then
  passes once the three lines are gone.

Explicitly **not** done:

- **No regeneration of the two derived files.** They have drifted from their generator by 6
  hunks / 33 lines each; regenerating would sweep that unrelated drift into this PR. Issue
  #247 owns that reconciliation (design D1).
- **No edit to `Containerfile`.** Its duckdb filter becomes redundant, but it is a
  control-plane path this automation must not touch; it is called out in the PR body as a
  human follow-up (design D4).
- No other pin is touched, and no Python-version change is made here.

## Capabilities

### New Capabilities
- `dependency-pin-hygiene`: the shared base requirements carry no pin the codebase does not
  use, and a guard test — collected in every environment the gating suite runs in — fails
  when a removed pin reappears, so an upstream merge cannot silently restore it.

### Modified Capabilities
<!-- None. -->

## Impact

- **Dependencies**: `requirements/requirements-base.txt`,
  `src/cli/templates/dockerfiles/base-python-image/requirements.txt`,
  `src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt` — one deleted line
  each. No pin is added, changed, or reordered.
- **Runtime**: none. The package is not imported anywhere and is not in `pyproject.toml`, so
  no service loses a capability. The loop image already runs without it, green.
- **CI / images**: merging **republishes the base images**, because
  `requirements/requirements-base.txt` matches the change-detection `PATTERN` at
  `.github/workflows/publish-base-images.yml:43`. That is expected and desirable — the
  republished images simply no longer carry a package nothing imports — but it must be stated
  in the PR body so a reviewer is not surprised by a base-image build on a three-line diff.
  The two derived `base-*/requirements.txt` files do **not** match that pattern (it matches
  `base-*/Dockerfile`), so the republish is triggered by the `requirements-base` edit alone.
- **Tests**: one new file, `tests/unit/test_requirements_hygiene.py`. No existing test
  changes.
- **Coverage**: diff-cover reports no measurable `src/` lines — the changed `src/` paths are
  `.txt` files carrying no executable lines, and the rest of the diff is the test file. This
  is a genuine "no lines with coverage information" result, not a skipped gate.
- **Upstream**: this does not fix upstream, and is not meant to. `archi-physics/archi` keeps
  its pin; the guard converts the next merge that reintroduces it from a silent regression
  into a red test that names the file and line.
- **Interaction with #247** (reconciles the derived-file drift): independent, either order.
  Whichever lands second rebases and re-runs its own checks; this change's `sed` is
  line-number independent, and #247 regenerates from whatever `requirements-base` holds at
  branch time, which by then has no duckdb line to reintroduce.
