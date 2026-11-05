## ADDED Requirements

### Requirement: A render with no chatbot SHALL build the config-seed and benchmark image tag from one build block
In a render with no chatbot, `config-seed` and `benchmark` SHALL use one image tag and equal `build` blocks.
`config-seed` MUST build
`archi_code/cli/templates/dockerfiles/Dockerfile-benchmarks{{ '-gpu' if gpu_ids else '' }}`
with no build `args`, the same as the `benchmark` service, so `docker compose up --build`
gives both services the benchmark image whichever build finishes last. No image tag in the
render MUST be built from two different `build` blocks. `config-seed` MUST keep its
explicit `command: ["bash", "-lc", "python -m src.cli.tools.config_seed"]`.

#### Scenario: a CPU benchmark-only render builds each shared tag once
- **WHEN** `base-compose.yaml` renders with `enabled_services=["benchmarking"]` and no
  `gpu_ids`
- **THEN** `config-seed` and `benchmark` have the same `image` and equal `build` blocks
  that name `Dockerfile-benchmarks`, and no image tag is built from two different `build`
  blocks

#### Scenario: a GPU benchmark-only render builds each shared tag once
- **WHEN** `base-compose.yaml` renders with `enabled_services=["benchmarking"]` and
  `gpu_ids="all"`
- **THEN** `config-seed` and `benchmark` have the same `image` and equal `build` blocks
  that name `Dockerfile-benchmarks-gpu`, and no image tag is built from two different
  `build` blocks

#### Scenario: the benchmark container runs the benchmark service on every deploy (operator, post-merge)
- **WHEN** an operator runs `archi evaluate -n <name> ... --hostmode --force` on a host
  with Docker and then runs `docker inspect -f '{{json .Config.Cmd}}' benchmarking-<name>`
- **THEN** the printed CMD contains `service_benchmark.py`

### Requirement: A render with no chatbot SHALL pass APP_VERSION to config-seed in its environment
In a render with no chatbot, the `config-seed` `environment` SHALL contain `APP_VERSION` as a string.
The value is the rendered `app_version`. The benchmark image does not bake `APP_VERSION` in, and `config_seed` writes it to the
deployment record (`src/utils/deployment_record.py`).

#### Scenario: a benchmark-only render passes the version
- **WHEN** `base-compose.yaml` renders with `enabled_services=["benchmarking"]` and
  `app_version="v2026.10.0-7-g5f3d80e9"`
- **THEN** `services["config-seed"]["environment"]["APP_VERSION"]` is
  `"v2026.10.0-7-g5f3d80e9"`

### Requirement: A render with a chatbot SHALL keep config-seed unchanged
A render with a chatbot SHALL be byte-identical to the render from the `origin/dev` template.
The `origin/dev` template is the one that this change starts from. `config-seed` MUST keep the image `{{ chatbot_image }}:{{ chatbot_tag }}`,
the build block `{context: ., dockerfile: .../Dockerfile-chat, args: {APP_VERSION: ...}}`,
and no `APP_VERSION` key in its `environment`.

#### Scenario: a CPU chatbot render is unchanged
- **WHEN** `base-compose.yaml` renders with `enabled_services=["chatbot", "benchmarking"]`
  and no `gpu_ids`
- **THEN** `config-seed` and `chatbot` have the same `image` and equal `build` blocks that
  name `Dockerfile-chat`, and `config-seed`'s `environment` has no `APP_VERSION` key

#### Scenario: chatbot renders are byte-identical to origin/dev
- **WHEN** the template renders `["chatbot", "benchmarking"]` and `["chatbot"]`, each with
  no `gpu_ids` and with `gpu_ids="all"`, from the `origin/dev` template and from the
  changed template
- **THEN** each pair of rendered texts is byte-identical

#### Scenario: the GPU chatbot race stays recorded until fasrc/archi#810 fixes it
- **WHEN** the template renders `["chatbot", "benchmarking"]` with `gpu_ids="all"`
- **THEN** `test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks` fails with an
  `AssertionError` and is marked `xfail(strict=True, raises=AssertionError)` with a reason
  that names fasrc/archi#810
