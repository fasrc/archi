## Why

`scripts/dev/build_docker_images.sh:80-86` defines two tracked files as pure derived
artifacts — each one is a header concatenated onto `requirements/requirements-base.txt`:

```bash
cat "$ROOT_DIR/requirements/cpu-requirementsHEADER.txt" \
    "$ROOT_DIR/requirements/requirements-base.txt" \
    > "$ROOT_DIR/src/cli/templates/dockerfiles/base-python-image/requirements.txt"
cat "$ROOT_DIR/requirements/gpu-requirementsHEADER.txt" \
    "$ROOT_DIR/requirements/requirements-base.txt" \
    > "$ROOT_DIR/src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt"
```

The tracked copies do not equal that output. Measured on `origin/dev` @ `443351b6`, each
file is **6 hunks** off its generator (101 tracked lines vs 116 generated for the CPU file;
102 vs 117 for the GPU file), with the identical package *set* on both sides. Regenerating
would, in both files: add `markdownify==1.2.2`, `pymdown-extensions==11.0.1`,
`pandas==2.3.2` and `markitdown[pdf,pptx]==0.1.5`; pin the currently-unpinned
`mkdocs-material` to `==9.7.3`; bump `python-dotenv` from `1.0.0` to `1.2.2`; strip a
trailing space from `beautifulsoup4==4.12.3 `; and replace abridged "see requirements-base
for rationale" comments with the full comment blocks from `requirements-base`.

**No published image changes, which is both why this went unnoticed and why the fix is
safe.** `.github/workflows/publish-base-images.yml:91` runs
`scripts/dev/build_docker_images.sh "$TAG"`, and that script regenerates both files
(lines 80-86) *before* building — so every published base image already contains the
regenerated content. No user deployment builds a base image either: every service template
does `FROM docker.io/a2rchi/a2rchi-python-base:latest` or `…-pytorch-base:latest`
(`src/cli/templates/dockerfiles/Dockerfile-chat:2`, `Dockerfile-chat-gpu:2`).
`src/cli/managers/templates_manager.py:893-906` copies `src/` into a deployment build
context as `archi_code/`, so these files travel along as inert payload that nothing
installs from. Runtime is unaffected regardless: `pandas`, `markitdown[pdf,pptx]`,
`markdownify` and `python-dotenv` are all declared in `pyproject.toml`'s `dependencies`,
and every service image runs `pip install .`.

So the drift costs nothing today — but the tree currently lies. A reader who edits one of
these tracked files reasonably believes they are changing what the base image installs, and
they are not: the next publish silently discards the edit. The fix is to make git agree with
what CI already builds, and to add a guard so the two cannot separate again.

## What Changes

- **Add `tests/unit/test_requirements_generated_in_sync.py`** — one case per derived file,
  asserting the tracked file's bytes equal `header + requirements-base` concatenated in that
  order. The file pairs come from a module-level mapping so both cases share one
  implementation; on mismatch the failure carries a unified diff and names the regeneration
  command, so a future failure explains itself without a spelunk.
- **Regenerate both derived files** using the generator's own `cat` command — never by hand
  editing, which is the mechanism that produced the drift in the first place.
- **Nothing else.** `requirements/requirements-base.txt` and the two header files are not
  touched; no pin is changed to make the guard pass; the generator, CI workflows, and the
  control-plane paths are untouched.

The guard lives in a **new test module**, not in `tests/unit/test_repo_hygiene.py`: that
module carries a module-level `pytest.mark.skipif` (`tests/unit/test_repo_hygiene.py:41`)
for non-git-checkout environments, which would silently skip this guard exactly where a
release artifact most needs it. Same reasoning as PR #237's `design.md` Decision 3.

## Capabilities

### New Capabilities
- `generated-requirements-sync`: the tracked base-image requirements files are derived
  artifacts and MUST equal their generator's output byte for byte, enforced by a test that
  runs in any checkout rather than only in a git working copy.

### Modified Capabilities
<!-- None. -->

## Impact

- `src/cli/templates/dockerfiles/base-python-image/requirements.txt` and
  `src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt` — regenerated.
- `tests/unit/test_requirements_generated_in_sync.py` — new.
- **Exactly three paths.** `requirements/`, `Containerfile`, `.github/`, `scripts/`,
  `deploy/`, `config/` and `hooks/` are all unchanged, and that is an acceptance criterion.
- **Diff coverage:** `diff-cover` will report *no lines with coverage information*. The only
  `src/` paths in this diff are `.txt` requirements files with no executable lines, and the
  rest of the diff is the new test itself. That is a legitimate pass, not a bypassed gate.
- **No published image changes** (see Why), **no runtime change**, no new dependency, no
  config/CLI/API surface.
- **Interaction with #246** (PR #251, open, removes the dead `duckdb==0.8.1` pin from
  `requirements-base.txt` and — surgically, without regenerating — from these same two
  derived files): the two changes are independent and may land in either order, but they do
  touch the same two files, so whichever lands second needs a rebase and a re-run of its own
  checks. If #246 lands first, `requirements-base.txt` no longer carries the duckdb pin and
  regeneration simply propagates its absence; this change must not reintroduce it. This
  change deliberately declares its own capability rather than extending #246's
  `dependency-pin-hygiene`, so neither delta depends on the other being merged or archived.
