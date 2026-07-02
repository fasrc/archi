## ADDED Requirements

### Requirement: Source commit resolution

The system SHALL provide a helper that resolves the archi source git commit for a given
repository root on a best-effort basis. The helper MUST return a short commit identifier and
MUST mark a working tree with uncommitted changes as dirty. The helper MUST NOT raise under any
circumstances; on any failure it MUST return the sentinel value `unknown`.

#### Scenario: Clean checkout
- **WHEN** the helper resolves the commit for a git checkout with no uncommitted changes
- **THEN** it returns the short commit SHA (e.g. `936a52f8`) with no suffix

#### Scenario: Dirty checkout
- **WHEN** the helper resolves the commit for a git checkout that has uncommitted changes
- **THEN** it returns the short commit SHA with a `-dirty` suffix (e.g. `936a52f8-dirty`)

#### Scenario: Not a git checkout or git unavailable
- **WHEN** the helper resolves the commit for a path that is not a git checkout, or `git` is
  not available
- **THEN** it returns `unknown` and does not raise

#### Scenario: Default repository root
- **WHEN** the helper is called without an explicit repository root
- **THEN** it resolves the commit against the checkout path recorded at install time (the same
  source the deployment is built from), so a non-editable `pip install .` still resolves the
  real commit rather than `unknown`

### Requirement: Deployment records the source commit

When the system copies the archi source into an image build (`archi create`, or `archi restart`
that rebuilds the image), it SHALL record the resolved archi source commit for that build. It
MUST emit the resolved value to the deploy log, and it MUST write the value to a `SOURCE_COMMIT`
file at the deployment root, resolved from the same repository root the source was copied from so
the recorded commit reflects the code that lands in the image. Recording the source commit MUST
be best-effort and MUST NOT cause the deploy to fail. The write MUST be tied to the source-copy
step: a restart that does not rebuild the image MUST NOT overwrite `SOURCE_COMMIT`.

#### Scenario: Source commit logged and written when source is copied
- **WHEN** the deployment source is copied for an image build
- **THEN** the deploy log contains the resolved archi source commit
- **AND** a `SOURCE_COMMIT` file exists at the deployment root whose contents are the resolved
  value (the short SHA, optionally `-dirty`, or `unknown`)

#### Scenario: Restart rebuild refreshes the recorded commit
- **WHEN** `archi restart` rebuilds the image (the default, without `--no-build`)
- **THEN** the copied source's commit is written to `SOURCE_COMMIT`, so it is not left stale

#### Scenario: Restart without rebuild leaves the recorded commit untouched
- **WHEN** `archi restart --no-build` runs (no image is rebuilt)
- **THEN** the source is not re-copied and `SOURCE_COMMIT` is not overwritten, so it continues to
  describe the code running in the current image

#### Scenario: Resolution failure does not break the deploy
- **WHEN** the source commit cannot be resolved (non-git checkout or git unavailable)
- **THEN** source copying still completes successfully
- **AND** the recorded value is `unknown`
