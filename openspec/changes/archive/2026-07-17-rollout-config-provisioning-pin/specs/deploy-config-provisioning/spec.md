## ADDED Requirements

### Requirement: A pin bump preserves the live source manifest

A pin bump SHALL preserve the live deployment's source manifest: before a new
`CONFIG_REF`/`CONFIG_SHA` pin is deployed to a host whose corpus is built by
re-ingestion, the reconciler SHALL verify that the pinned `lists/sources.list`
reproduces the source manifest that produced the currently-running corpus — including
git sources (`git-` prefixed lines), not only web pages. A pin whose source list drops
sources the live deployment ingests SHALL NOT be deployed, because a redeploy re-ingests
from scratch (`reset_collection: true`) and would silently erase the dropped corpus.

The existing version-pinning and post-provision-verification requirements guarantee the
checkout's *version and shape* (right commit, expected files present); this requirement
adds the missing guarantee of *content parity* with the running deployment, established
at pin-authoring time rather than deploy time.

#### Scenario: Pin drops git sources

- **WHEN** a candidate pin's `lists/sources.list` contains fewer `git-` sources than the
  source list that produced the running corpus
- **THEN** the pin is rejected and not tagged, and the missing git sources are named,
  before any redeploy is run against it

#### Scenario: Pin matches the live manifest

- **WHEN** a candidate pin's `lists/sources.list` contains every web and `git-` source
  present in the running deployment's source manifest
- **THEN** the pin may be tagged and rolled out

#### Scenario: Post-rollout corpus parity is confirmed

- **WHEN** a redeploy completes against a freshly bumped pin
- **THEN** the deploy is verified by comparing post-ingest corpus counts (web documents,
  git documents embedded, git documents failed) against the pre-bump baseline, and a
  drop in embedded git documents is treated as a rollout failure
