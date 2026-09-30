# Pin the 15 service Dockerfile templates to ghcr digests

## Why

The 15 service templates under `src/cli/templates/dockerfiles/` all reference their base
image by tag: `FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4`, and the pytorch
equivalent. A tag is a mutable pointer. Anyone who can push to `ghcr.io/fasrc` can move
`dev-4314ac4` onto a different image, and from that moment a clean `archi create` on an
unchanged checkout builds a different base than the one this repository was tested on. No
commit records the change, and nothing in the tree can detect it.

That was not a theoretical exposure. Issue #333 found that PR-triggered CI jobs held
registry write credentials, so any PR could move a tag the deployments depend on. The CI
half of that shipped separately (PR #336, merged). This change closes the consumer side:
once the templates name a digest, moving a tag is harmless to every deployment, because a
digest is the image's content address and cannot be repointed.

The writer this needs landed first. `scripts/dev/update_service_base_images.py` is the only
writer of these `FROM` lines and PR-preview CI calls it on every base-image change; before
#334 it mis-parsed a digest reference and skipped the line in silence. #334 merged at
`3bab8aeb` (PR #342), so the script now reads, writes, and round-trips digests. The
templates are the last piece still on a tag.

## What Changes

- Every `FROM ghcr.io/fasrc/a2rchi-*-base:dev-4314ac4` line in the 15 service templates
  becomes `FROM ghcr.io/fasrc/a2rchi-*-base@sha256:<digest>`, written by
  `scripts/dev/update_service_base_images.py` rather than by hand.
- Each rewritten `FROM` line gains a managed annotation line directly above it recording
  the build the digest is: `# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)`.
- `tests/unit/test_base_image_preflight.py` gains a repository guard: the reference
  `base_reference()` reads out of the real templates is digest-pinned, for both base images.
- No source module changes. `src/cli/managers/base_image_preflight.py` already parses a
  digest reference (`_FROM_BASE_RE`, `src/cli/managers/base_image_preflight.py:33`), and
  `tests/unit/test_python_version_declaration.py` already reads a build name out of the
  managed annotation (`test_base_pins_reads_the_build_from_a_digest_annotation`), both
  delivered by #266 and #334 in anticipation of this change.

**The two digests, operator-verified on 2026-08-24 and re-verified against ghcr on
2026-08-27:**

| Image | Tag | Digest |
|---|---|---|
| `a2rchi-python-base` | `dev-4314ac4` | `sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8` |
| `a2rchi-pytorch-base` | `dev-4314ac4` | `sha256:c29c6e8b4262736e3a5d3d47756b0d483db88254a91b16932d37f498bc704b5e` |

The re-verification used `gh api /orgs/fasrc/packages/container/<image>/versions` with a
`read:packages` token. Both tags still resolve to the digests above, so the tag has not
moved and the pin records the same image the templates build on today.

### One deviation from issue #335, and why

Issue #335 specifies the annotation as `# base image: dev-4314ac4`. This change writes
`# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)` instead. The
issue's wording predates #334 and is superseded by the code that merged with it, in three
independent places:

1. `scripts/dev/update_service_base_images.py:124` emits exactly this wording. The issue
   directs the implementer to that script ("The #334 script writes the corrected two-line
   form for you"), so the script's wording is what the issue's own instruction produces.
2. `test_service_templates_pin_one_explicit_base_tag`
   (`tests/unit/test_python_version_declaration.py:387`) reads the build name out of the
   annotation with `_MANAGED_ANNOTATION_RE`. The issue's shorter wording does not match it,
   so a hand-written `# base image: dev-4314ac4` fails that guard: every template reports as
   "a digest with no annotation".
3. `docs/docs/developer_guide.md:515` documents the managed wording as the contract.

The wording is also load-bearing rather than cosmetic. The script removes an annotation only
when the whole wording matches, script name included, so a comment a template owns is never
deleted. An annotation in any other wording is invisible to the script: a later re-pin would
add its own line and leave the old one behind, naming a build the file no longer references.
The two-line shape the issue asks for, and the reason it asks for it, are both honoured.

## Capabilities

### New Capabilities

- `service-base-images`: what reference the service templates declare for their base images.
  The capability directory does not exist under `openspec/specs/` yet — the changes that
  introduce it, #266 (`fix-issue-266-ghcr-base-images`) and #334
  (`fix-issue-334-digest-pinned-base-refs`), are both merged but not archived. This change
  therefore adds its requirement rather than modifying one.

### Modified Capabilities

None.

## Impact

- `src/cli/templates/dockerfiles/Dockerfile-*` — 15 files, one `FROM` line and one new
  annotation line each.
- `tests/unit/test_base_image_preflight.py` — new repository guards. The file holds 69 tests
  today; the issue directs extending it rather than adding a file.
- **Not** edited: any workflow file, `deploy/**`, `scripts/dev/update_service_base_images.py`
  (#334 owns it), `src/cli/managers/base_image_preflight.py` (already digest-ready), and the
  base image content itself.
- Coverage: the diff is Dockerfile templates plus a test file. `scripts/gate.sh:146` measures
  `--cov=src` over Python only, so this diff reports no lines to `diff-cover` and the 80%
  patch-coverage gate passes on "no lines with coverage information". The guards are the
  evidence here, not a percentage.
- Release and PR-preview CI keep working unchanged; the reasoning is in `design.md` under
  "The release flow still round-trips".
