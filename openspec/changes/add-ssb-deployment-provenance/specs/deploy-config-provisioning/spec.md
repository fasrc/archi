## MODIFIED Requirements

### Requirement: Config provenance is recorded
Every deploy SHALL record the provisioned config state — `git -C config rev-parse HEAD`, whether it matches `CONFIG_SHA`, and a name-status listing of any dirty paths — in the deploy output AND in the deployment's own database, so the exact config that produced any deployment is reconstructable after the fact and is readable by the running deployment itself.

The persisted record SHALL carry the pin ref, the pin commit, the deployed `HEAD`, the
match verdict, the dirty paths, the application version, and the time of the deploy.
Persistence SHALL happen during config seeding, which already runs once per deploy with a
database connection. A failure to persist SHALL be logged and SHALL NOT fail the deploy,
because the config is the product and the record is provenance.

#### Scenario: Off-pin deploy is reconstructable
- **WHEN** a deploy runs with a dirty tree (off the pin)
- **THEN** the deploy output records the config HEAD, the pin mismatch, and the dirty
  paths with their statuses

#### Scenario: The running deployment can report its own pin
- **WHEN** a deploy completes
- **THEN** a deployment-record row exists carrying the pin ref, the pin commit, the
  deployed `HEAD`, the match verdict, the dirty paths, the application version, and the
  deploy time

#### Scenario: A live-edited deploy is distinguishable from a clean one
- **WHEN** a deploy proceeds with tracked edits while `HEAD` sits at the pin
- **THEN** the persisted record reports the match verdict together with the dirty paths,
  so a reader can tell the deployed config from the pinned config

#### Scenario: Provenance persistence never fails the deploy
- **WHEN** the deployment-record write raises during config seeding
- **THEN** the failure is logged and the deploy continues
