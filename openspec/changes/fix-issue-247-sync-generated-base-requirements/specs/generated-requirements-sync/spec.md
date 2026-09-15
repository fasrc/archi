## ADDED Requirements

### Requirement: Generated base-image requirements files equal their generator's output

Each tracked base-image requirements file SHALL be byte-identical to the concatenation of its header file and `requirements/requirements-base.txt`, in that order, and the repository's test suite SHALL fail when it is not.

The two files are derived artifacts, not sources. `scripts/dev/build_docker_images.sh:80-86`
regenerates both with a truncating `cat` redirect on every base-image publish
(`.github/workflows/publish-base-images.yml:91`), so a tracked copy that differs from that
output is stale by definition and its differences have already been discarded by every
image ever published. The pairs are `requirements/cpu-requirementsHEADER.txt` →
`src/cli/templates/dockerfiles/base-python-image/requirements.txt` and
`requirements/gpu-requirementsHEADER.txt` →
`src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt`.

The comparison SHALL be byte-exact, not a comparison of parsed requirements or of stripped
lines. The generator is a byte concatenation, so anything weaker would certify as "in sync" a
file the generator would still rewrite — trailing whitespace on a pin and abridged comment
text are both real observed drift, and both are invisible to a set-of-packages comparison.

Each file pair SHALL be checked independently, so that one stale file is reported as one
failure naming that file rather than as a single opaque "requirements are out of sync".

A failure SHALL name the offending path, show the difference as a unified diff, and state the
command that regenerates the file. The drift this guard catches is discovered by whoever next
touches an unrelated dependency, who has no reason to know these files are generated; a
failure that does not say so sends them to hand-edit the file, which is precisely how the
drift arose.

The guard SHALL run wherever the repository's unit tests run, and MUST NOT be conditioned on
the checkout being a git working copy. Conditioning it on git would skip it in exactly the
environments — an unpacked sdist, a container build context — where a stale derived artifact
is the only remaining evidence of the problem.

Reconciliation SHALL be performed by running the generator's own command, never by hand
editing the derived file to match. Hand editing is the mechanism that produced the drift, and
a hand-reconciled file is indistinguishable from a correct one only until the next publish.

Satisfying this requirement SHALL NOT involve editing `requirements/requirements-base.txt` or
either header file. Those are the sources; changing a source to make a derived artifact match
inverts the dependency and would silently alter what the base images install.

#### Scenario: A stale derived file fails the suite

- **WHEN** a tracked base-image requirements file differs by even one byte from its header
  concatenated with `requirements/requirements-base.txt`
- **THEN** the unit suite fails
- **AND** the failure names that file's path, shows a unified diff of expected versus tracked,
  and names the command that regenerates it

#### Scenario: A regenerated derived file passes

- **WHEN** both derived files have been produced by the generator's own `cat` command from the
  current headers and `requirements-base.txt`
- **THEN** every case of the guard passes
- **AND** the reproduce commands in the issue print no diff output

#### Scenario: Each file is reported independently

- **WHEN** one of the two derived files is stale and the other is current
- **THEN** exactly one case fails, naming the stale file
- **AND** the case for the current file still passes, so the report distinguishes one stale
  artifact from a broken generator contract

#### Scenario: A pin added to the source propagates to both derived files

- **WHEN** a requirement is added to, removed from, or re-pinned in
  `requirements/requirements-base.txt` without regenerating the derived files
- **THEN** both cases fail
- **AND** the fix is to run the generator, not to edit the derived files by hand

#### Scenario: Whitespace-only drift is still drift

- **WHEN** a derived file differs from the generator's output only by trailing whitespace on a
  pin, or only by the wording of a comment block
- **THEN** the guard still fails, because the generator would still rewrite those bytes
- **AND** the comparison is not relaxed to the set of declared packages, which is equal on both
  sides in exactly this case

#### Scenario: The guard runs outside a git checkout

- **WHEN** the unit suite runs in a checkout that is not a git working copy, such as an
  unpacked sdist or a container build context
- **THEN** the guard still executes and still enforces the invariant
- **AND** it is NOT skipped, because it reads tracked file contents rather than git metadata

#### Scenario: Published images are unaffected by reconciliation

- **WHEN** the derived files are reconciled with their generator's output
- **THEN** no published base image changes, because the publish workflow already regenerates
  both files before building
- **AND** the change is recorded as making git agree with what CI already builds, so a
  33-line dependency-shaped diff is not mistaken for a dependency bump
