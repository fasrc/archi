## ADDED Requirements

### Requirement: A GPU render with a chatbot SHALL build the config-seed and chatbot image tag from one build block
In a render with a chatbot and `gpu_ids`, `config-seed` and `chatbot` SHALL use one image tag and equal `build` blocks.
`config-seed` MUST build
`archi_code/cli/templates/dockerfiles/Dockerfile-chat{{ '-gpu' if gpu_ids else '' }}`
with `args: {APP_VERSION: <app_version>}`, the same as the `chatbot` service, so
`docker compose up --build` gives both services the GPU chat image whichever build
finishes last. No image tag in the render MUST be built from two different `build` blocks.
Renders without `gpu_ids`, and renders with no chatbot, MUST be byte-identical to the
render from the `origin/dev` template that this change starts from.

#### Scenario: a GPU chatbot render builds each shared tag once
- **WHEN** `base-compose.yaml` renders with `enabled_services=["chatbot", "benchmarking"]`
  and `gpu_ids="all"`
- **THEN** `config-seed` and `chatbot` have the same `image` and equal `build` blocks
  `{context: ., dockerfile: .../Dockerfile-chat-gpu, args: {APP_VERSION: ...}}`,
  `config-seed`'s `environment` has no `APP_VERSION` key, and no image tag is built from
  two different `build` blocks

#### Scenario: the strict xfail record is gone
- **WHEN** `grep -n xfail tests/unit/test_base_compose_image_tags.py` runs
- **THEN** it prints nothing, and
  `python -m pytest -q tests/unit/test_base_compose_image_tags.py` prints `9 passed` with
  no `xfailed` and no `failed`

#### Scenario: CPU and benchmark-only renders are byte-identical to origin/dev
- **WHEN** the template renders `["chatbot", "benchmarking"]`, `["chatbot"]`, and
  `["benchmarking"]` with no `gpu_ids`, and `["benchmarking"]` with `gpu_ids="all"`, from
  the `origin/dev` template and from the changed template
- **THEN** each pair of rendered texts is byte-identical

#### Scenario: a GPU chatbot render changes only the config-seed dockerfile line
- **WHEN** the template renders `["chatbot", "benchmarking"]` and `["chatbot"]` with
  `gpu_ids="all"`, from the `origin/dev` template and from the changed template
- **THEN** each pair differs in exactly one line, the `config-seed` `dockerfile:` line,
  from `.../Dockerfile-chat` to `.../Dockerfile-chat-gpu`

#### Scenario: the chatbot container runs the GPU image on a GPU deploy (operator, post-merge)
- **WHEN** an operator deploys a chatbot with `--gpu-ids all` on a host with Docker and
  then runs `docker inspect -f '{{json .Config.Env}}' <chatbot container>`
- **THEN** the printed environment contains `VLLM_WORKER_MULTIPROC_METHOD=spawn`, which
  only `Dockerfile-chat-gpu` sets
