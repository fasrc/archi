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
reference are alternatives, not layers: a line pinned to a digest has no tag for
`--orig-tag` to compare against, and a rewrite that put both on one line would produce a
reference no runtime accepts.

#### Scenario: A digest-pinned reference is rewritten to a tag

- **WHEN** a template line reads `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<hex>` and the script runs with `--tag pr-7 --switch-source ghcr --orig-tag all`
- **THEN** the line reads `FROM ghcr.io/fasrc/a2rchi-python-base:pr-7`
- **AND** the digest no longer appears on the line

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
diff stops being reviewable. The one exception is trailing whitespace on a line that
receives an annotation — see the normalization scenario below.

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
- **THEN** the python-base line reads `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<64 hex>  # dev-abc1234`
- **AND** the line does not carry a bare tag reference

#### Scenario: A digest with no tag is written without a comment

- **WHEN** the same command is run with no `--tag`
- **THEN** the line carries the digest reference and no trailing comment

#### Scenario: An unknown --digest name is refused

- **WHEN** the script runs with `--digest java=sha256:<64 hex>`
- **THEN** the script exits non-zero
- **AND** the error names `python` and `pytorch` as the valid keys
- **AND** no template file is written

#### Scenario: A malformed digest is refused

- **WHEN** the script runs with `--digest python=deadbeef`
- **THEN** the script exits non-zero
- **AND** no template file is written

#### Scenario: A base image with no --digest keeps its tag

- **WHEN** the script runs with `--digest python=sha256:<64 hex> --tag dev-abc1234 --orig-tag all`
- **THEN** the pytorch-base lines carry the tag `dev-abc1234`
- **AND** they carry no digest

### Requirement: A digest annotation never outlives the digest it names

When the script replaces a digest reference with a tag reference, it SHALL remove the trailing `# <tag>` comment from that line.

The comment exists only to say in words which build the digest is. Once the digest is gone
the comment names a build the line no longer references, and the next reader — human or
script — has two disagreeing answers on one line.

Only a trailing comment is removed, and only on a line whose reference carried a digest. Any
other trailing content on the `FROM` line is not an annotation and SHALL survive the
rewrite.

#### Scenario: The stale annotation is dropped on the way back to a tag

- **WHEN** a line reading `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<hex>  # dev-4314ac4` is rewritten with `--tag pr-7 --orig-tag all`
- **THEN** the line reads `FROM ghcr.io/fasrc/a2rchi-python-base:pr-7`
- **AND** `dev-4314ac4` does not appear on the line

#### Scenario: Other trailing content survives the rewrite

- **WHEN** a digest-pinned line also carries a build-stage name, as in `FROM ghcr.io/fasrc/a2rchi-python-base@sha256:<hex> AS builder  # dev-4314ac4`
- **THEN** the rewritten line still carries `AS builder`
- **AND** it no longer carries the comment

#### Scenario: Trailing whitespace is normalized on a line that gains an annotation

- **WHEN** a line ending in a stray space, as 13 of the 15 service templates do today, is pinned to a digest with a `--tag`
- **THEN** the annotation is separated from the reference by exactly two spaces
- **AND** pinning the same line again changes nothing further

An annotation cannot be appended to a line that ends in whitespace and then read back: the
stray space and the comment's own separator run together, and nothing in the line says
where one ends and the other begins. The whitespace is therefore normalized at the moment
an annotation is written — once, on the first pin — and every rewrite after that is
byte-stable. This is the single exception to the rule that other trailing content survives,
and it is forced rather than chosen: the alternative is a reference the script cannot read
back correctly.

#### Scenario: A line whose only change is its comment is still written

- **WHEN** a rewrite leaves the image reference identical but removes or replaces the trailing comment
- **THEN** the file is written to disk
- **AND** the script reports the file as updated
