## Why

A benchmark-only deploy (`archi evaluate`) can start the `benchmark` container from the
chat image, so the container runs `service_chat.py` and never writes a benchmark artifact
(fasrc/archi#791).

In `src/cli/templates/base-compose.yaml` (`origin/dev` `5f3d80e9`), `config-seed`
(`:140-146`) uses the image
`{{ chatbot_image | default(benchmarking_image) }}:{{ chatbot_tag | default(benchmarking_tag) }}`
and builds `Dockerfile-chat`. The `benchmark` service (`:782-786`) uses
`{{ benchmarking_image }}:{{ benchmarking_tag }}` and builds
`Dockerfile-benchmarks{{ '-gpu' if gpu_ids else '' }}`. With no chatbot in the deploy,
`chatbot_image` is not defined (`src/cli/utils/service_builder.py:156-160` sets the
`<service>_image` keys only for enabled services). Then the two services build two
different Dockerfiles under one tag. `docker compose up --build` runs the builds in
parallel, and the last build to finish owns the tag. Both containers start from that
image.

The issue records the failure on the FASRC dev host on 2026-10-29: the `embtrial-a` stack's
`benchmarking-<name>:2000` image had the CMD `python -u src/bin/service_chat.py`, and the
container stopped after 5 s on `Connection in use: ('0.0.0.0', 7861)`. On a host where
port 7861 is free, the container would serve a chat app and never finish.

## What Changes

- In `base-compose.yaml`, when `chatbot_image` is not defined (the same condition that
  makes `config-seed` use the benchmark image's tag), `config-seed` builds the same build
  block as the `benchmark` service:
  `Dockerfile-benchmarks{{ '-gpu' if gpu_ids else '' }}` with no build `args`. The two
  builds are then identical, so the order in which they finish does not matter.
- In the same case, `config-seed` gets `APP_VERSION: "{{ app_version }}"` in its
  `environment`. `Dockerfile-chat` bakes `APP_VERSION` into the image (`:45-46`), and
  `config_seed` writes it to the deployment record
  (`src/utils/deployment_record.py:80`). `Dockerfile-benchmarks` does not bake it in.
- Add `tests/unit/test_base_compose_image_tags.py`: render the real template and assert
  that no image tag is built from two different build blocks, for benchmark-only renders
  with and without `gpu_ids`.
- Deploys that include a chatbot do not change: their rendered compose text is
  byte-identical before and after, with and without `gpu_ids`.

## Capabilities

### New Capabilities
- `compose-image-tags`: in a rendered compose file, the services that share an image tag
  build it from one build block, so a parallel build cannot give a service another
  service's image.

### Modified Capabilities
<!-- None. -->

## Impact

- `src/cli/templates/base-compose.yaml` (the `config-seed` `build:` and `environment:`
  blocks) and one new test file. No Dockerfile, no `service_builder.py`, no `deploy/**`.
- A benchmark-only deploy builds the benchmark image twice under one tag (once for each
  service). The second build hits the layer cache.
- `diff-cover` scores `src/**/*.py` lines only, so the template change is not scored.
- Known sibling, out of scope: a GPU deploy WITH a chatbot has the same race between
  `config-seed` (`Dockerfile-chat`) and `chatbot` (`Dockerfile-chat-gpu`). The issue's
  rule keeps chatbot deploys unchanged, so this change records it as a strict xfail test
  and fasrc/archi#810 tracks the fix.
