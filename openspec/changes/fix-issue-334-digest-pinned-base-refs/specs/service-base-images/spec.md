## ADDED Requirements

### Requirement: The base-reference rewriter reads digest-pinned references

`scripts/dev/update_service_base_images.py` SHALL parse a `<prefix><image>@sha256:<hex>` base reference into its prefix, its image name, and its digest, and SHALL treat that reference as carrying no tag.

The script is the only writer of the `FROM` lines in the service templates, and PR-preview
CI calls it on every base-image change (`.github/workflows/pr-preview.yml:274`). Today
`_split_image_spec` (`scripts/dev/update_service_base_images.py:44`) splits on the last
`:`, so `ghcr.io/fasrc/a2rchi-python-base@sha256:c068…` yields the image
`…a2rchi-python-base@sha256` at the tag `c068…`. Both halves are wrong, and the corrupted
image name then fails the `image != base_name` guard
(`scripts/dev/update_service_base_images.py:100`).

The failure is silent, which is what makes it worth a requirement rather than a bug fix in
passing. A skipped line is indistinguishable from a line that needed no change: the script
prints nothing, exits zero, and CI goes on to smoke-test the base image the PR replaced.
Nothing downstream can catch it, because the reference it reads is well-formed — it just
names last week's build.

"Carrying no tag" is the load-bearing half of the parse. A digest reference and a tag
reference are alternatives for the rewriter, not layers: a line pinned to a digest has no
tag for `--orig-tag` to compare against.

A runtime does accept `<image>:<tag>@sha256:<hex>` — the scenario below requires the script
to read one — but there the digest decides which image is pulled and the tag beside it is
informational. This rewriter never *emits* the combined form, because carrying a tag
alongside a digest would give `--orig-tag` something to match on a line whose reference the
tag does not determine.

#### Scenario: A digest-pinned reference is rewritten to a tag

- **WHEN** a template line reads `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<hex>` and the script runs with `--tag pr-7 --switch-source ghcr --orig-tag all`
- **THEN** the line reads `FROM ghcr.io/fasrc/a2rchi-python-base:pr-7`
- **AND** the digest no longer appears on the line

#### Scenario: A reference carrying both a tag and a digest is digest-pinned

- **WHEN** a template line reads `FROM ghcr.io/fasrc/a2rchi-python-base:dev-4314ac4@sha256:<hex>` and the script runs with `--tag pr-7 --switch-source ghcr --orig-tag all`
- **THEN** the line reads `FROM ghcr.io/fasrc/a2rchi-python-base:pr-7`
- **AND** processing that same line with `--orig-tag dev-4314ac4` leaves it unchanged

A reference may carry both. The digest decides which image is pulled and the tag beside it
is informational, so the reference is read as digest-pinned and that tag is dropped on
rewrite. Read naively the tag stays glued to the image name, the name comparison fails, and
the line is passed over in silence — the same failure the first requirement exists to end.

#### Scenario: A specific --orig-tag leaves a digest-pinned line alone

- **WHEN** the same digest-pinned line is processed with `--orig-tag dev-4314ac4`
- **THEN** the line is unchanged
- **AND** the file is not rewritten

A digest reference has no tag, so no literal `--orig-tag` value can match it. Only
`--orig-tag all` reaches it. An operator narrowing a rewrite to one tag is naming a set the
digest-pinned line is not in.

#### Scenario: Tag, digest, and back again returns the original line

- **WHEN** a tag reference is rewritten to a digest and that result is rewritten back with the original tag
- **THEN** the final line equals the original line

The reader and the writer have to agree. A round trip that drifted by a stray comment or a
lost prefix would leave the templates a little different after every pin, which is how a
diff stops being reviewable.

### Requirement: The rewriter can pin a base image by digest

The script SHALL accept a repeatable `--digest <name>=sha256:<64 hex>` option, and for each named base image SHALL write the reference as `<prefix><image>@<digest>`.

`<name>` is a key of `BASE_IMAGE_MAP` (`scripts/dev/update_service_base_images.py:15`):
`python` or `pytorch`. Without this option the pin that #333 puts in the templates has no
maintenance path — an operator would have to hand-edit 15 files to move it.

The option rejects what it cannot honour, rather than writing it out. An unrecognised name
and a malformed digest both produce a reference no runtime can pull, and both would be
discovered only at build time in CI, far from the command that caused them.

#### Scenario: A digest is written with the tag beside it

- **WHEN** the script runs with `--digest python=sha256:<64 hex> --tag dev-abc1234 --switch-source ghcr --orig-tag all`
- **THEN** the line above the python-base line reads `# base-image-pin: dev-abc1234 (managed by update_service_base_images.py)`
- **AND** the python-base line reads `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex>`
- **AND** the line does not carry a bare tag reference

#### Scenario: An empty --tag is refused

- **WHEN** the script runs with `--tag ""`, with or without `--digest`
- **THEN** the script exits non-zero
- **AND** no template file is written

Both workflow call sites pass `--tag "${{ ... }}"` from a job output, so an empty value is
one unset output away. It is not a tag. Without `--digest` the reference is rebuilt with no
tag at all, giving a bare `FROM <repo>` that resolves to `latest` at build time — the
unpinned base this whole capability exists to prevent. With `--digest` the annotation names
no build, which the script no longer recognises as its own and the repository guard rejects.

#### Scenario: A digest with no --tag is refused

- **WHEN** the script runs with `--digest python=sha256:<64 hex>` and no `--tag`
- **THEN** the script exits non-zero
- **AND** the error names `--tag`
- **AND** no template file is written

A digest names no build, so `--tag` is what supplies one for the annotation. Without it the
script would write a pin recording nothing about which build the services are on — and the
repository guard `test_service_templates_pin_one_explicit_base_tag` rejects exactly that, so
the command would leave the tree failing CI. Refusing at the command puts the error where
the operator can act on it.

#### Scenario: Re-running the same pin changes nothing

- **WHEN** a digest-pinned line is processed with `--digest` and `--tag` naming what it already carries
- **THEN** the file is not rewritten and no update is reported

The annotation survives exactly when the digest does not move and no new tag is given to
name it — which, since `--digest` requires `--tag`, means a rewrite that names no new
reference at all, such as a bare `--switch-source`. A digest that *does* move takes the old
annotation with it and gets the new `--tag` in its place, because the old one names the
wrong build.

#### Scenario: An unknown --digest name is refused

- **WHEN** the script runs with `--digest java=sha256:<64 hex>`
- **THEN** the script exits non-zero
- **AND** the error names `python` and `pytorch` as the valid keys
- **AND** no template file is written

#### Scenario: A malformed digest is refused

- **WHEN** the script runs with `--digest python=deadbeef`
- **THEN** the script exits non-zero
- **AND** no template file is written

#### Scenario: A rewrite that names no new reference keeps the digest

- **WHEN** a digest-pinned line is processed with `--switch-source dockerhub --orig-tag all` and no `--tag` and no `--digest`
- **THEN** the line carries the same digest under the new prefix
- **AND** the annotation naming that digest survives

With no `--tag` and no `--digest` there is nothing to put in the reference's place.
Rebuilding it from the prefix and image alone yields a bare `FROM <repo>`, which resolves to
`latest` at build time — an unpinned base, written out and reported as a successful update.
A rewrite that names no new reference moves the registry; it does not decide the image is
no longer pinned.

#### Scenario: A digest for a base excluded by --bases is refused

- **WHEN** the script runs with `--digest python=sha256:<64 hex> --bases pytorch`
- **THEN** the script exits non-zero
- **AND** the error names `python` and the bases the run selected
- **AND** no template file is written

The rewriter only walks the bases named by `--bases`, so a digest for any other base can
never be applied. Accepting it would exit zero having written nothing, which is the silent
partial failure this option exists to end — an operator would read a clean exit as a
successful pin.

#### Scenario: A base image with no --digest keeps its tag

- **WHEN** the script runs with `--digest python=sha256:<64 hex> --tag dev-abc1234 --orig-tag all`
- **THEN** the pytorch-base lines carry the tag `dev-abc1234`
- **AND** they carry no digest

### Requirement: The annotation is a line of its own, and never outlives its digest

The script SHALL write the annotation as a `# base-image-pin: <tag> (managed by update_service_base_images.py)` line directly above the `FROM` line, and SHALL remove that line whenever the reference it writes is a tag.

The annotation cannot ride on the `FROM` line. A Dockerfile recognises `#` as a comment only
at the start of a line, so a trailing `# <tag>` is read as a second `FROM` argument; docker
and podman both reject the file with "FROM requires either one or three arguments", and
every service build fails before an image is pulled. This was measured against both builders
on 2026-08-24.

The annotation exists only to say in words which build the digest is. Once the digest is gone
the annotation names a build the file no longer references, and the next reader — human or
script — has two disagreeing answers.

An annotation names the build a digest is. A tag reference names its own build, so the
annotation goes whenever a tag is written — whatever the reference it replaced. Leaving one
above a tag line would label a reference that has no digest for it to name.

The wording includes the name of the script that owns the line, and the script SHALL treat a
line as its own only when the whole wording matches. A template's own comment is therefore
never deleted for merely sitting above a `FROM` line. Nothing on the `FROM` line itself is
the script's to touch beyond the reference: a build-stage name and a stray trailing space
both SHALL survive the rewrite.

The annotation SHALL end up directly above its `FROM` line, and the script SHALL find an
existing one even when a blank line separates the two. It SHALL also write a line ending
after the annotation when the `FROM` line is the last in a file and carries none, rather
than running the two together into a single commented-out line.

#### Scenario: A blank line does not hide an annotation from the rewrite

- **WHEN** an annotation, a blank line, and a digest-pinned `FROM` line appear in that order, and the reference is rewritten
- **THEN** the old annotation is gone
- **AND** a rewrite that writes a new annotation leaves exactly one, directly above the `FROM` line

#### Scenario: An annotation never survives above a tag reference

- **WHEN** an annotation sits above a tag-pinned line and that line is rewritten to another tag
- **THEN** the annotation is gone

#### Scenario: A final FROM line with no line ending still gets a separator

- **WHEN** the matching `FROM` line is the last in the file and has no trailing newline, and the script writes an annotation
- **THEN** a line ending separates the annotation from the `FROM` line

#### Scenario: The stale annotation is dropped on the way back to a tag

- **WHEN** an annotation line above `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<hex>` is rewritten with `--tag pr-7 --orig-tag all`
- **THEN** the line reads `FROM ghcr.io/fasrc/a2rchi-python-base:pr-7`
- **AND** `dev-4314ac4` does not appear in the file

#### Scenario: Other trailing content survives the rewrite

- **WHEN** a digest-pinned line also carries a build-stage name, as in `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<hex> AS builder`
- **THEN** the rewritten line still carries `AS builder`
- **AND** the annotation line above it is gone

#### Scenario: No rewrite ever puts a comment on a FROM line

- **WHEN** any combination of `--tag`, `--digest`, and `--switch-source` rewrites a template
- **THEN** no `FROM` line in the result contains a `#`

#### Scenario: A stray trailing space survives a pin

- **WHEN** a line ending in a stray space, as 13 of the 15 service templates do today, is pinned to a digest with a `--tag`
- **THEN** the annotation appears on the line above and the stray space is still there
- **AND** pinning the same line again changes nothing further

#### Scenario: A comment the script did not write is left alone

- **WHEN** a template's own comment sits directly above a `FROM` line that the script rewrites, including one shaped like `# base image: DO-NOT-EDIT`
- **THEN** that comment is still there afterwards

The script matches its whole annotation wording, script name included. Removing a comment
for its position, or for a loose resemblance, would delete words the template owns.

#### Scenario: A line whose only change is its comment is still written

- **WHEN** a rewrite leaves the image reference identical but removes or replaces the annotation line
- **THEN** the file is written to disk
- **AND** the script reports the file as updated
