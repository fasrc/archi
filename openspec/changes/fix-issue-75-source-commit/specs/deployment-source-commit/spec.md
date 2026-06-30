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

### Requirement: Deployment records the source commit

When `archi create` prepares deployment artifacts, the system SHALL record the resolved archi
source commit. It MUST emit the resolved value to the deploy log, and it MUST write the value to
a `SOURCE_COMMIT` file in the rendered deployment-artifacts directory. Recording the source
commit MUST be best-effort and MUST NOT cause artifact preparation to fail.

#### Scenario: Source commit logged and written during artifact preparation
- **WHEN** `prepare_artifacts` runs for a deployment
- **THEN** the deploy log contains the resolved archi source commit
- **AND** a `SOURCE_COMMIT` file exists in the artifacts directory whose contents are the
  resolved value (the short SHA, optionally `-dirty`, or `unknown`)

#### Scenario: Resolution failure does not break the deploy
- **WHEN** the source commit cannot be resolved (non-git checkout or git unavailable)
- **THEN** artifact preparation still completes successfully
- **AND** the recorded value is `unknown`
