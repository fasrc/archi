## Context

At deploy time, `archi evaluate` renders `./configs` on the host, and the benchmark image
copies the same directory at build time, so the two are identical. After
`reseed_arm.sh` rewrites the host's `configs/config.yaml`, only the host copy is current.
Provenance already comes from `selected_file_digest`, so the baked copy is not a
deliberate record; it is only a stale input.

## Goals / Non-Goals

Goals:
- The benchmark container sees the host's `./configs` at `/root/archi/configs`, so a
  re-seeded arm runs with the re-seeded file and records an empty
  `divergence_from_selected_file`.

Non-Goals:
- No change to `reseed_arm.sh`, `archive_run.sh`, `service_benchmark.py`, or the
  Dockerfiles.
- No change to `deploy/**` or `config/**`.
- The live re-seed check is a post-merge operator step (it needs the live stack).

## Decisions

D1. Mount read-only: `- ./configs:/root/archi/configs:ro`. The issue lets the PR choose
`:ro` if the benchmark never writes under `/root/archi/configs`. It does not:
`service_benchmark.py` only reads that directory (`get_all_configs` walks it,
`load_new_configuration` opens each file with mode `"r"`), and its one config write goes to
`CONFIG_PATH = "/root/archi/config.yaml"` (`:81`, `:1509`), which is outside the mount. The
other writes (`:843`, `:865`) go to the results directory. Read-only makes sure a benchmark
run can never change the host's rendered config. The other six services keep their
read-write mounts; this change does not touch them.

D2. Put the line in the existing benchmark `volumes:` block, after the
`./{{ prompt_file }}` entry, outside every `{% if %}` guard, so every benchmarking render
gets it.

D3. Test by rendering the real template with Jinja and `ServiceBuilder`, the pattern in
`tests/unit/test_base_compose_mcp_mounts.py`, with `benchmarking` in the enabled services.
Assert on the parsed YAML, not on raw text.

## Risks / Trade-offs

- A running feature-matrix campaign locks the code tree; it needs `--relock` after the
  redeploy. The PR body says so.
- A deployment whose host `./configs` is missing a file that the image had will now see the
  host directory. `archi evaluate` renders `./configs` before it starts the containers, so
  this is the same contract the other six services already depend on.
