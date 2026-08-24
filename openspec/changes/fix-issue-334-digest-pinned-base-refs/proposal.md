# Teach the base-reference rewriter to parse and emit digest references

## Why

`scripts/dev/update_service_base_images.py` is the only writer of the `FROM` lines in the
15 service Dockerfile templates, and PR-preview CI calls it on every base-image change
(`.github/workflows/pr-preview.yml:274`). Issue #333 pins those templates to
`@sha256:` digest references. The script cannot read that form: `_split_image_spec`
(`scripts/dev/update_service_base_images.py:44`) splits on the last `:`, so
`ghcr.io/fasrc/a2rchi-python-base@sha256:c068…` parses as the image `…-base@sha256` at the
tag `c068…` — wrong on both halves. The image name then fails the `image != base_name`
guard at `scripts/dev/update_service_base_images.py:100`, the line is skipped, and CI
silently smoke-tests the old base instead of the one the PR built.

The script also has no way to write a digest, so the pin in #333 has no maintenance path,
and it has no test file at all — the sole existing reference to it is an error-message
assertion at `tests/unit/test_base_image_preflight.py:269`.

## What Changes

- `_split_image_spec` separates an `@sha256:…` digest before the tag split and returns it
  as its own component, so a digest-pinned reference yields the true image name.
- `_build_image_spec` gains a digest output form, `<prefix><image>@<digest>`.
- A new repeatable `--digest <name>=sha256:<64 hex>` option pins one base image by digest.
  `<name>` is a `BASE_IMAGE_MAP` key (`python`, `pytorch`); an unknown name exits non-zero
  with an error that names the valid keys.
- When the script writes a digest, it records the human-readable tag from `--tag` in a
  `# base-image-pin: <tag> (managed by update_service_base_images.py)` line directly above
  the `FROM` line. With no `--tag`, it writes no annotation.
  The annotation cannot ride on the `FROM` line, which is where this proposal first put it
  and where issue #334 specified it. A Dockerfile recognises `#` as a comment only at the
  start of a line, so a trailing `# <tag>` is read as a second `FROM` argument and both
  docker and podman reject the file with "FROM requires either one or three arguments".
  Measured against docker 29.5.1 and podman 5.8.2 on 2026-08-24.
- When the script writes a tag reference, it removes that annotation line, which has no
  digest left to name. It removes such a line only when the whole wording matches, so a
  comment belonging to the template is never deleted. Nothing on the `FROM` line itself is
  touched beyond the reference, so `AS builder` and a stray trailing space both survive.
- `--orig-tag all` keeps matching every current reference, digest-pinned included. A
  specific `--orig-tag` still matches only that literal tag, so it never matches a digest.
- A new `tests/unit/test_update_service_base_images.py` covers all of the above against
  `tmp_path` fixture Dockerfiles. No registry access and no network.

## Capabilities

### New Capabilities

- `service-base-images`: how the service templates reference their base images, and what
  the tool that rewrites those references guarantees. The capability directory does not yet
  exist under `openspec/specs/`; the change that introduces it (#266,
  `openspec/changes/fix-issue-266-ghcr-base-images/`) merged at `5e168b00` but is not
  archived. This change therefore adds its requirement rather than modifying one.

### Modified Capabilities

None.

## Impact

- `scripts/dev/update_service_base_images.py` — parse, build, and CLI surface.
- `tests/unit/test_update_service_base_images.py` — new file.
- Unblocks #333, which pins the 15 service templates to digests and needs this writer first.
- `.github/workflows/pr-preview.yml` is **not** edited here. Its existing call
  (`--tag … --switch-source ghcr --orig-tag all`) starts working on digest-pinned templates
  as a result of this change; the operator owns any workflow edit.
- `src/cli/managers/base_image_preflight.py` is **not** edited. Its `_FROM_BASE_RE`
  (`src/cli/managers/base_image_preflight.py:33`) already captures a digest suffix.
- Coverage: `scripts/gate.sh:146` measures `--cov=src` only, so these lines report no
  coverage data to `diff-cover`. The unit tests are the real evidence, not the percentage.
