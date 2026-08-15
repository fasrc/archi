## Context

Three files pin `duckdb==0.8.1`, and they are not three peers. One is a source of truth and
two are derived artifacts:

```
requirements/cpu-requirementsHEADER.txt ─┐
                                         ├─ cat ─> base-python-image/requirements.txt
requirements/requirements-base.txt ──────┤
                                         └─ cat ─> base-pytorch-image/requirements.txt
requirements/gpu-requirementsHEADER.txt ─┘
```

`scripts/dev/build_docker_images.sh:80-86` performs that concatenation, and
`.github/workflows/publish-base-images.yml:91` runs the script before every base-image
build. So the published images always reflect the regenerated content, and the tracked bytes
of the two derived files are consumed by nobody.

That last fact is what makes this change safe **and** what makes it hazardous. Safe, because
editing the derived files cannot change a published image. Hazardous, because the tracked
copies have already drifted from their generator by 6 hunks / 33 lines each, so the obvious
way to remove a line from a generated file — regenerate it — would sweep 33 lines of
unrelated dependency-looking changes into a three-line PR. Issue #247 exists to reconcile
that drift under its own review.

The pin's only other appearance is `Containerfile:72`, a
`grep -ivE '^[[:space:]]*duckdb([=<>!~ ]|$)'` filter that strips duckdb from the loop image's
install because 0.8.1 has no cp312 wheel. Its four-line comment block at `:61-64` documents
exactly this pin.

## Goals / Non-Goals

**Goals**: remove a dead pin from the three files that carry it; make its reintroduction by a
future upstream merge fail loudly; keep the diff provably surgical.

**Non-Goals**: reconciling the derived files' pre-existing drift (#247); editing
`Containerfile` to drop its now-redundant filter (control-plane, human follow-up); fixing the
pin upstream in `archi-physics/archi`; moving the project to Python 3.12; touching any other
pin.

## Decisions

### D1 — Surgical line deletion, never regeneration

The three edits are done with a line-anchored `sed` that deletes `duckdb==0.8.1` and nothing
else:

```bash
sed -i '/^duckdb==0\.8\.1$/d' \
  requirements/requirements-base.txt \
  src/cli/templates/dockerfiles/base-python-image/requirements.txt \
  src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt
```

`scripts/dev/build_docker_images.sh` is **not** run. The alternative — regenerate the two
derived files from the generator, which is how one would normally edit a derived artifact —
is rejected because the tracked copies are already 33 lines out of step with their generator.
Regenerating would add `markdownify`, `pymdown-extensions`, `pandas`, and
`markitdown[pdf,pptx]`, pin `mkdocs-material`, bump `python-dotenv` 1.0.0 → 1.2.2, and
rewrite comment blocks — all changes that *look* like a dependency bump in review and none of
which belong to this issue.

The guard against this is mechanical rather than a matter of care: the diff must contain
**exactly three deleted lines** across `requirements/` and
`src/cli/templates/dockerfiles/`. Any larger number means regeneration happened; revert and
redo. That check is an acceptance criterion, not a suggestion.

### D2 — The guard lives in a new module, not in `test_repo_hygiene.py`

`tests/unit/test_repo_hygiene.py` is the natural-looking home and is the wrong one. It
carries a module-level `pytestmark = pytest.mark.skipif(...)` keyed on
`git rev-parse --git-dir` succeeding, because its own invariants are about git tracking. A
guard placed there would **silently skip** in any environment that is not a git checkout —
an sdist, a container that copied the tree in, a CI job with a shallow or absent `.git` —
reporting green while asserting nothing. This is the same reasoning as PR #237's `design.md`
Decision 3.

`tests/unit/test_requirements_hygiene.py` therefore has no module-level skip and no git
dependency at all. It resolves the repo root positionally, as `test_repo_hygiene.py` already
does for its own paths:

```python
REPO_ROOT = Path(__file__).resolve().parents[2]
```

`tests/unit/x.py` → `parents[2]` is the checkout root. The files it reads are tracked and
always present next to the test, so the guard is executable wherever the suite is.

### D3 — Match the distribution, not the version string

The guard matches `^\s*duckdb([=<>!~ ]|$)` per line, which is deliberately the same shape as
the filter at `Containerfile:72`. Two properties follow, and both matter:

- **A reintroduction at any version is caught.** Upstream could merge `duckdb==0.10.0` or
  `duckdb>=1.0`; a guard matching the literal `duckdb==0.8.1` would wave those through. The
  requirement is "this project does not depend on duckdb", not "not on that one version".
- **A different distribution is not a false positive.** `duckdb-engine==0.13.0` does not
  match: after `duckdb` comes `-`, which is neither in the character class nor end-of-line.
  Should the project ever legitimately want a `duckdb-`prefixed package, the guard does not
  stand in the way — which is what keeps it from being disabled wholesale later.

Having the guard and the `Containerfile` workaround agree, character for character, on what
counts as a duckdb pin is intentional: the day someone deletes the filter, the guard is
already enforcing the condition the filter assumed.

### D4 — `Containerfile:72` stays, and that is recorded rather than fixed

With the pin gone, the filter matches nothing and the `grep -ivE` is a no-op that passes the
file through unchanged — harmless, but now misleading, since its comment block describes a
pin that no longer exists. `Containerfile` is on this automation's must-not-touch list, so
the change leaves it exactly as it is and the PR body names the cleanup as a human
follow-up. Removing a workaround in the same PR that removes its cause would also couple a
loop-image rebuild to a requirements change, which is worth keeping separate regardless of
the rail.

### D5 — Failure names every offender, not the first

The guard collects offending `path:line` strings across all three files and asserts on the
collected list, rather than asserting per-file and aborting on the first hit. The pin arrives
in all three files at once — that is how upstream carries it — so a guard that reports one
file at a time turns one red run into three sequential red runs for whoever is resolving the
merge. Asserting on the list also makes the red step self-evidencing: it must report exactly
three hits on `origin/dev`, which proves the guard reads all three files rather than passing
vacuously on a path it failed to find.

## Risks / Trade-offs

- **Base images republish on merge.** `requirements/requirements-base.txt` matches
  `.github/workflows/publish-base-images.yml:43`'s `PATTERN`, so merging kicks off a
  base-image build. Intended; the republished images just lack a package nothing imported.
  The risk is reviewer surprise at a three-line diff triggering an image build, mitigated by
  stating it in the PR body.
- **Upstream still carries the pin.** Not fixed here and not fixable here. The guard is the
  mitigation: the reintroduction becomes a named test failure at merge time instead of a
  silent restoration.
- **`Containerfile`'s filter is now dead code.** Accepted, D4. It is inert, not wrong.
- **Diff coverage has nothing to measure.** The `src/` paths in the diff are `.txt`, so
  diff-cover reports no lines with coverage information. That satisfies the gate legitimately
  rather than bypassing it, and the memory of past "tests-only diff" runs confirms this is
  the expected shape, not a misconfiguration.
- **#247 could conflict textually.** Both changes touch the same two derived files. They are
  semantically independent; whichever lands second rebases. #247 regenerates from
  `requirements-base` as it stands at its branch time, so once this lands there is no duckdb
  line for it to reintroduce.

## Migration Plan

None. No schema, no data, no deploy ordering, no runtime behaviour change. The base images
republish on merge through the existing CI path; no deployment needs to be touched and no
running service is affected.

## Open Questions

None. Issue #246 supplies the file anchors, the deletion method, the guard's location and
regex, and the acceptance criteria; every one of those anchors was re-verified against
`origin/dev` @ `443351b6` while writing this proposal. Nothing is invented here.
