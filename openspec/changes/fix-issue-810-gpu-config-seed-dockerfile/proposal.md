## Why

A GPU deploy that includes a chatbot can start the `chatbot` container from the CPU chat
image, with no PyTorch or CUDA stack (fasrc/archi#810).

In `src/cli/templates/base-compose.yaml` on `origin/dev` `82487ada`, `config-seed`
(`:140`) uses the image
`{{ chatbot_image | default(benchmarking_image) }}:{{ chatbot_tag | default(benchmarking_tag) }}`.
When a chatbot is deployed, its `{%- else %}` build branch (`:152-155`) builds
`archi_code/cli/templates/dockerfiles/Dockerfile-chat` with no GPU suffix. The `chatbot`
service (`:239-245`) uses `{{ chatbot_image }}:{{ chatbot_tag }}` and builds
`Dockerfile-chat{{ '-gpu' if gpu_ids else '' }}`. With `--gpu-ids`
(`src/cli/cli_main.py:89`, `:769`), the two services build two different Dockerfiles under
one tag. `Dockerfile-chat` starts `FROM ghcr.io/fasrc/a2rchi-python-base:v2026.08.0` and
`Dockerfile-chat-gpu` starts `FROM ghcr.io/fasrc/a2rchi-pytorch-base:v2026.08.0`.
`docker compose up --build` runs the builds in parallel, and the last build to finish owns
the tag.

The fix for fasrc/archi#791 (PR #811) left this case alone on purpose and recorded it as the
strict xfail test
`tests/unit/test_base_compose_image_tags.py::test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks`.

## What Changes

- In `base-compose.yaml`, the `config-seed` `{%- else %}` branch builds
  `archi_code/cli/templates/dockerfiles/Dockerfile-chat{{ '-gpu' if gpu_ids else '' }}`,
  the exact string that the `chatbot` service builds. The two build blocks are then equal,
  so the order in which the builds finish does not matter. The Jinja comment above the
  block names fasrc/archi#810. A Jinja comment renders nothing.
- In `tests/unit/test_base_compose_image_tags.py`, remove the strict xfail marker (the test
  now passes) and add
  `test_a_gpu_chatbot_render_builds_config_seed_as_the_chatbot_image`.
- CPU renders do not change: every render without `gpu_ids`, and every benchmark-only
  render, is byte-identical to `origin/dev`.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `compose-image-tags` (added by `fix-issue-791-config-seed-image-race`, not yet archived):
  a GPU render with a chatbot builds the `config-seed` and `chatbot` tag from one build
  block. The delta uses `ADDED` because the capability is not yet in `openspec/specs/`
  (see design D4).

## Impact

- `src/cli/templates/base-compose.yaml` (one `dockerfile:` line and one Jinja comment) and
  `tests/unit/test_base_compose_image_tags.py`. No Dockerfile, no
  `src/cli/utils/service_builder.py`, no `deploy/**`.
- The PR also adds this plan folder. The nightly loop reads its task list from here, the
  same as PR #811.
- A GPU chatbot deploy builds the GPU chat image twice under one tag (once for each
  service). The second build hits the layer cache. `config-seed` runs only
  `python -m src.cli.tools.config_seed` (`:193`), which the GPU image can run.
- `diff-cover` scores `src/**/*.py` lines only, so this diff scores no lines.
