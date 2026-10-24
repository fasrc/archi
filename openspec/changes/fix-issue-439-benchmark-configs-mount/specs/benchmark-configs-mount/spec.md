## ADDED Requirements

### Requirement: The benchmark service SHALL mount the host's rendered configs directory read-only
The `benchmark` service SHALL mount `./configs:/root/archi/configs:ro` in a benchmarking render.
When the compose template renders a benchmarking deployment, the `benchmark` service's
`volumes` MUST include `./configs:/root/archi/configs:ro`, so the container reads the
deployment's live rendered config and not the copy baked into its image. The existing
`./configs` mounts of the other services MUST stay unchanged.

#### Scenario: a benchmarking render mounts configs into the benchmark service
- **WHEN** `base-compose.yaml` renders with `benchmarking` among the enabled services
- **THEN** the `benchmark` service's `volumes` contain `./configs:/root/archi/configs:ro`

#### Scenario: the mount is read-only
- **WHEN** the same render is parsed
- **THEN** the benchmark service's `./configs` entry ends with `:ro` and no other entry in
  that service mounts `./configs` read-write

#### Scenario: the other configs mounts are unchanged
- **WHEN** `base-compose.yaml` renders with `chatbot` and `benchmarking` enabled
- **THEN** the `chatbot` and `data-manager` services still mount
  `./configs:/root/archi/configs`, and `config-seed` still mounts
  `./configs:/rendered-config:ro`

#### Scenario: a re-seeded arm passes the provenance check (operator, post-merge)
- **WHEN** an operator re-seeds an arm with `reseed_arm.sh` on a redeployed stack and runs
  the benchmark
- **THEN** `archive_run.sh` accepts the artifact with `divergence_from_selected_file: []`
