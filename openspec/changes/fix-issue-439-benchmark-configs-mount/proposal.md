## Why

The benchmark service reads its arm config from `/root/archi/configs`, but that directory
is the copy baked into the image at build time (`COPY configs configs`,
`src/cli/templates/dockerfiles/Dockerfile-benchmarks:25`). The other six services that
read `configs` bind-mount the host's `./configs` (`src/cli/templates/base-compose.yaml`
`:79`, `:271`, `:410`, `:481`, `:550`, `:621`). The benchmark service does not: its
`volumes:` block (`base-compose.yaml:774-785`, `origin/dev` 2a92d3c3) has no configs mount.

`scripts/benchmarking/feature_matrix/reseed_arm.sh` rewrites the host's
`configs/config.yaml` and re-seeds Postgres. After that, the benchmark container still
reads the stale baked copy. `service_benchmark.py` then records a non-empty
`divergence_from_selected_file`, and `archive_run.sh` refuses the artifact (`:62`, `:92`).
A re-seeded retrieval arm can never pass the Procedure E provenance check (fasrc/archi#439).

The operator decided on 2026-09-26 to mount the host's `./configs` live, the same way the
other services do. `docker cp` from `reseed_arm.sh` and a Postgres `static_config`
comparison were rejected.

## What Changes

- Add one line to the benchmark service's `volumes:` block in
  `src/cli/templates/base-compose.yaml`: `- ./configs:/root/archi/configs:ro`.
- Add a compose-render unit test that asserts the benchmark service has this mount and
  that the six existing `./configs` mounts and the `config-seed` mount are unchanged.
- No change to `Dockerfile-benchmarks` (`COPY configs` stays; the mount overlays it at
  run time), `reseed_arm.sh`, `archive_run.sh`, or `service_benchmark.py`.

## Capabilities

### New Capabilities
- `benchmark-configs-mount`: the benchmark container reads the deployment's live rendered
  configs directory, not the copy baked into its image.

### Modified Capabilities
<!-- None. -->

## Impact

- `src/cli/templates/base-compose.yaml` (one line) and one new test file under
  `tests/unit/`.
- The template is inside the feature-matrix campaign's locked code tree (`lib.sh`
  `fm_code_tree` / `fm_require_lock`). A running campaign needs a `--relock` after the host
  redeploys with this change. The redeploy is the operator's.
- The workaround hand-added to `~/.archi/archi-fm-00/compose.yaml` becomes unnecessary on
  the next `archi evaluate`.
- `diff-cover` scores `src/**/*.py` lines only, so the YAML template line is not scored.
