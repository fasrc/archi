## Context

Two tracked files under `src/cli/templates/dockerfiles/` are derived artifacts with a
one-line generator:

```bash
# scripts/dev/build_docker_images.sh:80-86
cat requirements/cpu-requirementsHEADER.txt requirements/requirements-base.txt \
    > src/cli/templates/dockerfiles/base-python-image/requirements.txt
cat requirements/gpu-requirementsHEADER.txt requirements/requirements-base.txt \
    > src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt
```

Measured on `origin/dev` @ `443351b6`, neither tracked file equals that output:

```
$ cat requirements/cpu-requirementsHEADER.txt requirements/requirements-base.txt \
    | diff - src/cli/templates/dockerfiles/base-python-image/requirements.txt | grep -c '^[0-9]'
6
$ cat requirements/gpu-requirementsHEADER.txt requirements/requirements-base.txt \
    | diff - src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt | grep -c '^[0-9]'
6
$ wc -l   # generated vs tracked
116 (cpu generated)  101 (cpu tracked)
117 (gpu generated)  102 (gpu tracked)
```

The drift is inert. The publish workflow regenerates before building
(`.github/workflows/publish-base-images.yml:91`), user deployments consume the *published*
base images by tag, and the copy that rides along in a deployment build context as
`archi_code/` is never installed from. So this is a truth-in-the-tree problem, not a
runtime one: the tracked bytes look authoritative and are not.

**Why now, and why not as part of #246.** #246 removes a dead `duckdb==0.8.1` pin from
`requirements-base.txt` and from these same two derived files. The obvious way to delete a
line from a generated file is to regenerate it — which would have swept this unrelated
33-line drift into that PR and made a one-line removal read as a dependency bump. #246
therefore forbids regeneration and deletes the line surgically; this change owns the
reconciliation.

## Goals / Non-Goals

**Goals**
- The tracked derived files equal their generator's output, byte for byte.
- Drift cannot recur silently: a test fails, names the file, and says how to fix it.
- The guard survives environments that are not git checkouts.

**Non-Goals**
- Changing any pin, or any published image. Both are out of scope and are acceptance
  criteria in the negative (`git diff origin/dev...HEAD -- requirements/` must be empty).
- Changing how base images are built or published.
- Deciding whether these files should be tracked at all (see Decision 5).

## Decisions

### Decision 1: Compare bytes, not decoded text

The guard asserts `generated_path.read_bytes() == header.read_bytes() + base.read_bytes()`.

`Path.read_text()` opens in text mode with universal newlines, so it silently translates
`\r\n` to `\n` on read — a derived file saved with CRLF endings would compare *equal* to a
LF source while `cat` would still rewrite every line of it. The generator is a byte
concatenation; the guard must be one too, or it certifies files the generator disagrees
with. The observed drift already includes a byte-level-only case (a trailing space on
`beautifulsoup4==4.12.3 `), which is the same class of miss.

Bytes are for the *assertion*. The failure *message* decodes both sides
(`errors="replace"`) to render a `difflib.unified_diff`, because a byte-level diff of a
100-line requirements file is unreadable. Decoding for display cannot weaken the assertion,
which has already been made on the raw bytes.

### Decision 2: A new test module, not `tests/unit/test_repo_hygiene.py`

`test_repo_hygiene.py:41` sets a **module-level** `pytestmark = pytest.mark.skipif(...)`
that disables the whole module outside a git working copy — correct there, because those
tests inspect git metadata (`git check-ignore`, tracking status), which has no meaning
without a repo.

This guard reads file *contents*, which exist in any checkout. Adding it to that module
would inherit a skip it does not need, and would skip it precisely in an unpacked sdist or
container build context — the environments where a stale derived artifact is the only
evidence left. A skipped guard reports green, which is worse than no guard. Same reasoning
as PR #237's `design.md` Decision 3.

### Decision 3: One parametrized case per file pair, from a module-level mapping

The two pairs live in a module-level constant and drive `pytest.mark.parametrize`, so the
suite reports `…[base-python-image]` and `…[base-pytorch-image]` independently.

A single test looping over both would collapse two independent artifacts into one
pass/fail: the first mismatch would mask the second, and the issue's red step — *confirm
both cases fail* — would be unobservable. Independent cases also mean a third derived file
is a one-line addition to the mapping rather than a new test.

### Decision 4: Reconcile by running the generator, and keep the guard's implementation independent of it

The fix runs the generator's own `cat` command (the exact lines from
`build_docker_images.sh:80-86`). The guard re-expresses the same contract in Python
(`read_bytes` + concatenation) rather than shelling out to the script.

This is deliberate: two independent expressions of one contract cross-check each other. A
guard that invoked `build_docker_images.sh` would be circular — it would pass by definition
and would also drag in the script's tag argument, its image-build side effects, and a
container runtime, none of which belong in a unit test. The cost is that a future change to
the generator's *shape* (a third input file, a different order) must be mirrored in the
mapping; the guard failing loudly is the intended signal for that.

Hand-editing the derived files to match is forbidden. It is the mechanism that produced the
drift, and a hand-reconciled file is indistinguishable from a correct one right up until the
next publish silently rewrites it.

### Decision 5: Declare a new capability rather than extending #246's `dependency-pin-hygiene`

#246's change introduces a `dependency-pin-hygiene` capability. That capability exists only
inside an open, unmerged change (PR #251) — it is not yet in `openspec/specs/`.

Writing this delta against it would couple two changes the issue states are independent and
may land in either order, and would create an archive-ordering dependency between them. The
subjects also differ: #246's requirement is *a named dead pin must not reappear*; this one
is *a derived artifact must equal its generator's output*, which holds for any package set
and would still hold if no pin were ever dead. Separate capabilities, no ordering
constraint, either may merge and archive first.

### Decision 6: Keep the files tracked, and keep the generator regenerating them

Two alternatives were considered and rejected in the issue; recorded here so they are not
re-litigated.

*Untrack the files and generate them purely at build time.* Defensible — they have no
consumer — but it removes them from the sdist and from the `archi_code/` payload and
silently breaks any image build that does not go through `build_docker_images.sh`. Too much
exposure for a cosmetic gain.

*Declare the tracked copies hand-maintained and delete the regeneration from the generator.*
This inverts today's reality: CI regenerates on every publish, so the hand edits are already
being discarded. Choosing it would make the base images start honoring the abridged list —
i.e. it *would* change published images, which this change is defined not to do.

## Risks / Trade-offs

- **The diff looks like a dependency bump.** 33 changed lines naming `pandas`, `markitdown`
  and `python-dotenv` reads as a version bump to a reviewer skimming it. Mitigation: the PR
  body must lead with *no published image changes, because CI regenerates these files before
  building*, and quote the before/after reproduce commands. This is an acceptance criterion.
- **`diff-cover` reports no measurable lines.** The `src/` paths in this diff are `.txt`
  files with no executable lines and the remainder is the new test, so patch coverage has
  nothing to measure and the gate passes with *no lines with coverage information*. That is
  a legitimate pass for a derived-artifact change, and the PR body should say so explicitly
  so it is not read as a bypassed gate.
- **Conflict with PR #251.** Both changes rewrite the same two files. They are independent
  in content but not in text, so whichever lands second rebases and re-runs its own checks.
  If #246 lands first, regenerate from whatever `requirements-base.txt` then says and do not
  reintroduce the duckdb pin — the regeneration propagates its absence automatically.
- **The guard pins the generator's current shape.** If someone adds a third input to the
  concatenation, the guard fails until the mapping is updated. Intended: that failure is the
  review prompt.
