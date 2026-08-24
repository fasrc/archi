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

### The trailing annotation is parsed out of `suffix`, not out of the reference

`_update_line` splits `suffix` into a leading part and a trailing comment, at the first `#`
that follows whitespace. The reference itself can never contain the `#`, because the
`\S+` group stops at the space before it.

The write policy is a four-way table on (what we are writing, what was there):

| Target reference | Source reference | Comment written |
|---|---|---|
| digest | anything | `  # <tag>` from `--tag`, or none if `--tag` was omitted |
| tag | had a digest | none — the old annotation is dropped |
| tag | had a tag | `suffix` is passed through unchanged |

The last row is deliberate. Dropping a comment from every rewritten line would be a wider
behaviour change than the issue asks for, and the annotation is only ever written next to a
digest, so a comment on a tag line is somebody else's and is left alone.

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

- **A tag-to-tag rewrite still carries a stale comment through, if one exists** → Accepted,
  and pinned by a spec scenario rather than left implicit. No template has such a comment
  today, and widening the rule would silently delete comments this issue never discussed.
- **Comment-only rewrites now write files that previously were skipped** → Bounded to lines
  the script already matched and already intended to update. The printed `Updated <path>`
  line stays truthful, which is what the CI operator reads.
- **The comment heuristic could misread a `#` that is not a comment** → A Dockerfile `FROM`
  line has no other use for a `#` after whitespace, and the reference token is consumed
  before `suffix` starts. The `AS builder` scenario is the guard against over-eager
  stripping.
- **No coverage signal on this file** → `--cov=src` means `diff-cover` cannot vouch for
  these lines. Mitigated by making the scenario list the acceptance bar: every scenario in
  the spec delta becomes a named test, and the gate still fails on a red test.
