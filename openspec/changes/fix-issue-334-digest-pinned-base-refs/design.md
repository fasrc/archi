# Design — digest-aware base-reference rewriting

## Context

`scripts/dev/update_service_base_images.py` is about 160 lines with four moving parts:
`_split_image_spec` (read a reference), `_build_image_spec` (write one), `_update_line`
(match a `FROM` line and swap the reference), and `update_base_tags` (walk
`DOCKERFILES_DIR.glob("Dockerfile*")`). It has no test file.

`_update_line` matches with
`r"(?P<intro>\s*FROM\s+(?:--platform=\S+\s+)?)(?P<image>\S+)(?P<suffix>.*)"`
(`scripts/dev/update_service_base_images.py:87`). Because `image` is `\S+`, the whole
reference — digest included — lands in one group, and everything after it lands in
`suffix`. The 15 service templates today read
`FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4` and several carry a trailing space, so
`suffix` is already non-empty in the tree.

Issue #333 will replace those tags with `@sha256:` digests plus a `  # dev-4314ac4`
annotation. This change makes the writer ready for that; it does not make the change to
the templates.

Constraint that shapes the whole design: `scripts/gate.sh:146` runs coverage with
`--cov=src`, so nothing in `scripts/` reports coverage to `diff-cover`. The unit tests are
the only evidence this change works. Black and isort, by contrast, do enforce `scripts/`
(`scripts/gate.sh:47`), so the file must stay format-clean.

## Goals / Non-Goals

**Goals:**

- Read a digest reference correctly, so the `image != base_name` guard stops rejecting it.
- Write a digest reference, so the pin in #333 has a maintenance path.
- Keep the annotation comment and the reference on a line telling the same story.
- Leave the CI call site working unchanged:
  `--tag "pr-<N>" --switch-source ghcr --orig-tag all`.

**Non-Goals:**

- Rewriting the service templates. That is #333's sibling issue.
- Any `.github/workflows/**` edit. The operator owns that.
- Any change to `src/cli/managers/base_image_preflight.py`. Its `_FROM_BASE_RE`
  (`src/cli/managers/base_image_preflight.py:33`) already captures a digest suffix,
  verified 2026-08-24.
- Resolving a tag to a digest against a registry. The digest is supplied on the command
  line. The tests use `tmp_path` fixtures only: no registry, no network.

## Decisions

### A digest is a fourth component, not another kind of tag

`_split_image_spec` returns `(prefix, image, tag, digest)`. Exactly one of `tag` and
`digest` is set for a well-formed reference.

The alternative — keep the 3-tuple and carry the digest inside `tag` — was rejected because
two separate pieces of `_update_line` read `tag` for different purposes: the `--orig-tag`
comparison at `scripts/dev/update_service_base_images.py:102`, and the `target_tag`
fallback on the line below. A digest smuggled through `tag` would make `--orig-tag
dev-4314ac4` match a digest-pinned line, which the spec forbids, and would make an omitted
`--tag` copy the digest into a tag position.

### Cut the digest off before the tag split

`_split_image_spec` does `rsplit("@", 1)` first, and only then applies the existing
`rsplit(":", 1)` to the left half. An OCI reference uses `@` for exactly one thing, so a
single split is unambiguous, and a reference with no `@` follows the untouched original
path. This is a smaller change than a full reference grammar, and it keeps the existing
prefix/image segmentation code as the one place that logic lives.

### The annotation is a line of its own, above the `FROM` line

The issue specified a trailing `  # <tag>` on the `FROM` line, and this design followed it
until a pre-PR review round tested the result against a real builder. **That form is not a
valid Dockerfile.** A Dockerfile recognises `#` as a comment only at the start of a line, so
the trailing text is read as a second `FROM` argument:

```
$ docker build -f Dockerfile.trailing .
dockerfile parse error on line 1: FROM requires either one or three arguments
$ podman build -f Dockerfile.trailing .
Error: FROM requires either one argument, or three: FROM <source> [AS <name>]
```

Measured against docker 29.5.1 and podman 5.8.2 on 2026-08-24. Had it shipped, #333's pin
would have broken every service build at parse time, before any image was pulled.

The annotation therefore goes on its own line, `# base image: <tag>`, directly above the
`FROM` line. The write policy is a three-way table on (what we are writing, what was there):

| Target reference | Source reference | Annotation line |
|---|---|---|
| digest | digest unchanged | kept as it is |
| digest | digest moved, or was a tag | `# base image: <tag>` from `--tag`, or none if `--tag` was omitted |
| tag | anything | none — an annotation never outlives its digest |

Two consequences follow, and both are simplifications. Nothing is ever appended to the
`FROM` line, so its trailing text — a build-stage name, the stray space 13 of the 15
templates carry — is passed through untouched with no special case. And the script removes
an annotation line only when it matches its own exact wording, so a comment the template
owns is never deleted for merely sitting in that position.

### Change detection compares the whole line

Today `_update_line` returns "unchanged" when `updated_spec == image_spec`
(`scripts/dev/update_service_base_images.py:110`). That test is now too narrow: re-pinning
the same digest under a new `--tag` changes only the comment and would be dropped on the
floor. The comparison becomes the rebuilt line against the original line.

### `--digest` is repeatable and validated in `parse_args`

`--digest NAME=sha256:HEX`, `action="append"`, parsed into a `dict` on `UpdateOptions`.
Both failure modes raise `SystemExit` with an explicit message — an unrecognised `NAME`
names the valid keys, a digest that does not match `sha256:[0-9a-f]{64}` is refused.

`SystemExit` rather than `parser.error` mirrors the existing idiom for a bad `--bases`
value (`scripts/dev/update_service_base_images.py:121`) and keeps the message assertable
in a test without matching argparse's usage banner.

### Tests load the script by path and redirect the templates directory

The test module loads the script with `importlib.util.spec_from_file_location`, the pattern
already used at `tests/unit/test_validate_queries_script.py:28`, because `scripts/dev` is
not a package. `update_base_tags` globs the module-level `DOCKERFILES_DIR`, so each test
monkeypatches that constant to a `tmp_path` directory holding one or two fixture
Dockerfiles. No test reads or writes the real templates.

## Risks / Trade-offs

- **The script could delete a comment the template owns** → It removes an annotation line
  only when the line matches `# base image: <value>` exactly, so position alone is never
  enough. Pinned by a scenario using a hand-written comment directly above a rewritten
  `FROM` line.
- **Annotation-only rewrites now write files that previously were skipped** → Bounded to
  lines the script already matched and already intended to update. The printed
  `Updated <path>` line stays truthful, which is what the CI operator reads.
- **The annotation could drift from the digest it names** → The two are written and removed
  together in one place, and change detection covers the pair, so a rewrite that moves only
  the annotation is still written out.
- **The output could be an invalid Dockerfile** → This already happened once, with the
  trailing-comment form the issue specified. A scenario now asserts that no `FROM` line in
  any rewrite result contains a `#`, across every option combination that writes an
  annotation. The unit suite cannot run a builder, so that invariant stands in for one.
- **No coverage signal on this file** → `--cov=src` means `diff-cover` cannot vouch for
  these lines. Mitigated by making the scenario list the acceptance bar: every scenario in
  the spec delta becomes a named test, and the gate still fails on a red test.
