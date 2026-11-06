## Context

`config-seed` runs one command, `python -m src.cli.tools.config_seed`
(`base-compose.yaml:193`, explicit `command:`), so the image CMD does not matter for it.
It needs the `src` tree and the Python dependencies. `Dockerfile-chat` and
`Dockerfile-chat-gpu` both `COPY archi_code src`, run `pip install .`, and declare
`ARG APP_VERSION=unknown` with `ENV APP_VERSION=${APP_VERSION}`. So `config-seed` gets the
same code and the same baked `APP_VERSION` from either image.

The `chatbot` service has no explicit `command:`. It depends on the image, and on a GPU
deploy it needs the PyTorch and CUDA stack of `a2rchi-pytorch-base`. So the `chatbot`
container is the one that the tag collision breaks.

## Goals / Non-Goals

Goals:
- In a GPU render with a chatbot, `config-seed` and `chatbot` build the image tag from equal
  build blocks.
- Every render without `gpu_ids`, and every benchmark-only render, is byte-identical to
  `origin/dev`.

Non-Goals:
- No change to any Dockerfile or to `src/cli/utils/service_builder.py` (issue constraint).
- No change to the base-image preflight (see Risks; fasrc/archi#814).
- No live deploy. The `docker inspect` check is an operator step after merge.

## Decisions

D1. Make the two builds identical (the same approach as #791 D1), not a separate tag for
`config-seed`. Identical build blocks make the parallel build order harmless and keep the
tag scheme unchanged.

D2. Copy the `chatbot` service's `dockerfile:` string exactly:
`archi_code/cli/templates/dockerfiles/Dockerfile-chat{{ '-gpu' if gpu_ids else '' }}`.
The `args: APP_VERSION` lines in the `{%- else %}` branch already equal the `chatbot`
service's `args`, so the `dockerfile:` line is the only difference. With no `gpu_ids`, the
expression renders an empty string, so CPU renders are byte-identical.

D3. Keep the existing strict xfail test and remove only its marker. Strict mode turns a pass
into a failure, so the marker and the template change must land in one commit (task 1.1).
Add one more test that pins the full `config-seed` build block on a GPU chatbot render, so
a later change that makes both services build the wrong Dockerfile cannot pass on equality
alone.

D4. The spec delta uses `ADDED`. `compose-image-tags` exists only in the unarchived change
`fix-issue-791-config-seed-image-race`, so there is no `openspec/specs/compose-image-tags/`
to modify. Archive note: that change's requirement "A render with a chatbot SHALL keep
config-seed unchanged" has two GPU scenarios ("chatbot renders are byte-identical to
origin/dev" with `gpu_ids="all"`, and "the GPU chatbot race stays recorded until
fasrc/archi#810 fixes it") that this change reverses. Archive #791 first. Then, when you
archive this change, change those two scenarios to CPU-only (or remove them) in
`openspec/specs/compose-image-tags/spec.md`.

## Measurement

Loop 1 measured these numbers on 2026-11-06 in scratch copies of `origin/dev` `82487ada`
(full `git archive` tree, the patch in tasks.md Appendix A applied).

`python -m pytest -q tests/unit/test_base_compose_image_tags.py`:

| Template | Test file | Result |
|---|---|---|
| `origin/dev` | `origin/dev` | `7 passed, 1 xfailed` |
| `origin/dev` | patched | `2 failed, 7 passed` |
| patched | patched | `9 passed` |

The 2 failures on the unchanged template are
`test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks` (the conflict names
`chatbot-demo:dev`) and `test_a_gpu_chatbot_render_builds_config_seed_as_the_chatbot_image`.

`python -m pytest -q tests/unit/ -k compose`: `67 passed, 1 xfailed` on `origin/dev`;
`69 passed` with the patch.

Render diff (tasks.md Appendix B), `origin/dev` template against the patched template:

| Services | `gpu_ids` | Result |
|---|---|---|
| `chatbot`, `benchmarking` | none | identical |
| `chatbot` | none | identical |
| `benchmarking` | none | identical |
| `benchmarking` | `all` | identical |
| `chatbot`, `benchmarking` | `all` | differs: line 101 only, `Dockerfile-chat` to `Dockerfile-chat-gpu` |
| `chatbot` | `all` | differs: line 101 only, `Dockerfile-chat` to `Dockerfile-chat-gpu` |

Mutation check: a template that hard-codes `Dockerfile-chat-gpu` in the `config-seed`
branch fails `test_a_chatbot_render_builds_no_tag_from_two_build_blocks` and
`test_a_chatbot_render_keeps_config_seed_on_the_chat_build` (`2 failed, 7 passed`).

The base-image preflight tests (`tests/unit/test_base_image_preflight.py` and the other
`*preflight*` files) stay green with the patch: `288 passed, 1 skipped`.

## Risks / Trade-offs

- A GPU chatbot deploy builds the GPU chat image twice under one tag. The second build hits
  the layer cache.
- The base-image preflight over-requires the python base. `required_base_image_names`
  (`src/cli/managers/base_image_preflight.py`) always requires `a2rchi-python-base`, and
  its docstring says "`config-seed` builds `Dockerfile-chat` whether or not the chatbot is
  enabled". After #791 and this change, a GPU deploy of the chatbot or the benchmark
  builds no python-base Dockerfile (`config-seed`, `chatbot`, `benchmark`, and
  `data-manager` all build `-gpu` Dockerfiles). Only `piazza` (`:476`) and `slack`
  (`:614`) still build a python-base Dockerfile on a GPU deploy. The check is a superset,
  so it never lets a missing base through. It can refuse a GPU deploy for an image that
  the deploy does not use. The issue limits this change to two files, so fasrc/archi#814
  tracks it.
