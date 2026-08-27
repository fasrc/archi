## ADDED Requirements

### Requirement: The service templates reference their base images by digest

Every `FROM` line in `src/cli/templates/dockerfiles/Dockerfile-*` that names a `ghcr.io/fasrc/a2rchi-*-base` image SHALL reference it by `@sha256:<hex>` digest, and SHALL NOT reference it by tag.

A tag is a mutable pointer. Anyone with push rights to the registry can move it, and from
that moment a clean `archi create` on an unchanged checkout builds a different base than the
one this repository was tested on. Nothing in the tree records it and nothing in the tree can
detect it. Issue #333 established that this was reachable in practice: PR-triggered CI jobs
held registry write credentials, so any pull request could move a tag every deployment
depends on.

A digest is a content address. It names one image and cannot be repointed, so the same
checkout builds the same base whatever happens to the tag afterwards. This requirement is
what makes a moved tag harmless rather than invisible.

The digest form must survive contact with the two readers that already parse these lines:
`base_reference()` (`src/cli/managers/base_image_preflight.py:77`), which the create
preflight uses to decide whether it can obtain the base before it tears a deployment down,
and `scripts/dev/update_service_base_images.py`, which CI calls to retarget the templates.
Both were taught the digest form ahead of this change, by #266 and #334 respectively.

#### Scenario: No template names a base image by tag

- **WHEN** the template directory is searched for a tag-shaped ghcr reference with `grep -REn '^FROM ghcr\.io/fasrc/[^@]+:[^@[:space:]]+[[:space:]]*$' src/cli/templates/dockerfiles/`
- **THEN** the search finds nothing

#### Scenario: Every service template is digest-pinned

- **WHEN** the template directory is searched with `grep -lE '^FROM ghcr\.io/fasrc/[a-z0-9-]+@sha256:[0-9a-f]{64}' src/cli/templates/dockerfiles/Dockerfile-*`
- **THEN** the search names 15 files

#### Scenario: The preflight reads a digest-pinned reference from the templates

- **WHEN** `base_reference("a2rchi-python-base")` is called against the real template directory
- **THEN** it returns a reference containing `@sha256:` followed by 64 hexadecimal characters
- **AND** the same holds for `base_reference("a2rchi-pytorch-base")`

#### Scenario: The pinned digests are the ones the operator verified

- **WHEN** the python base reference is read from the templates
- **THEN** its digest is `sha256:c068f17b8cba96682e7007c9dd5511f43fea86c796f3cbeee44e2766c5a9b8e8`
- **AND** the pytorch base reference's digest is `sha256:c29c6e8b4262736e3a5d3d47756b0d483db88254a91b16932d37f498bc704b5e`

Both were resolved from `gh api /orgs/fasrc/packages/container/<image>/versions` with a
`read:packages` token, on 2026-08-24 and again on 2026-08-27. Naming them in the spec is
deliberate: a guard that only checked the *shape* of a digest would pass a pin nobody ever
verified against the registry.

### Requirement: A digest-pinned line records the build it is, on its own line above

Each digest-pinned `FROM` line SHALL carry the annotation `# base-image-pin: <tag> (managed by update_service_base_images.py)` on the line directly above it, and no `FROM` line SHALL contain a `#`.

A digest names no build. Read alone it says nothing about which base the services are on, so
a split pin — half the templates on one build, half on another — becomes invisible. The
annotation restores that, and
`test_service_templates_pin_one_explicit_base_tag`
(`tests/unit/test_python_version_declaration.py:387`) reads the build name out of it and
fails when two builds are in the tree at once.

The annotation cannot ride on the `FROM` line. A Dockerfile recognises `#` as a comment only
at the start of a line, so a trailing `# <tag>` is read as a second `FROM` argument and both
docker and podman reject the file at parse time, before any image is pulled — every service
build fails. This was measured against docker 29.5.1 and podman 5.8.2 on 2026-08-24 during
the review of #342, which caught it before it shipped.

The wording is fixed, not decorative. `scripts/dev/update_service_base_images.py` removes an
annotation only when the whole wording matches, script name included, so a comment a
template owns is never deleted; and an annotation in any other wording is invisible to the
script, so a later re-pin would add its own line and leave the old one behind, naming a build
the file no longer references.

#### Scenario: Every digest-pinned line has its annotation directly above it

- **WHEN** a template's `FROM` line references a base image by digest
- **THEN** the line immediately above it is `# base-image-pin: dev-4314ac4 (managed by update_service_base_images.py)`

#### Scenario: No FROM line carries a comment

- **WHEN** the template directory is searched with `grep -En '^FROM .*#' src/cli/templates/dockerfiles/Dockerfile-*`
- **THEN** the search finds nothing

#### Scenario: A real builder parses the rewritten template

- **WHEN** `docker build --check -f src/cli/templates/dockerfiles/Dockerfile-chat <empty context>` is run
- **THEN** it exits 0 and reports no warnings
- **AND** `podman build --pull=never` on the same file reaches `STEP 1/16` and fails only because the image is not present locally

#### Scenario: A trailing space on a FROM line survives the pin

- **WHEN** a template whose `FROM` line ended in a space is rewritten to a digest
- **THEN** that trailing space is still there

Thirteen of the fifteen templates carry one. The rewriter never touches the text after the
reference, so nothing here needs the churn of removing them, and a diff that removed them
would hide the one-line change this work is.

### Requirement: The release retarget still moves every template off its digest

`scripts/dev/update_service_base_images.py --tag <release> --switch-source ghcr --orig-tag all` SHALL rewrite a digest-pinned template line to the given tag and SHALL remove the annotation above it.

The release workflow verifies three times that the templates name the release's base tag, and
one of those verifications runs on a fresh checkout before that job rewrites anything. Pinning
the templates to a digest is safe only because the retarget that runs earlier in the release
converts them back to a tag and pushes that commit to the dispatched ref. If the retarget
could not reach a digest-pinned line, the release would verify a stale reference and stop
after the images were already published.

`--orig-tag all` is what makes it reach. The script's default is `latest`, which matches no
line in the tree, and a specific `--orig-tag` never matches a digest-pinned line because such
a line carries no tag.

#### Scenario: The retarget reaches a digest-pinned line

- **WHEN** the release retarget runs with `--orig-tag all` over digest-pinned templates
- **THEN** every template names the release tag
- **AND** no `# base-image-pin:` annotation remains

#### Scenario: The verification after the retarget passes

- **WHEN** `--verify --tag <release> --switch-source ghcr` runs on the retargeted tree
- **THEN** it exits 0 and reports the number of references it checked
