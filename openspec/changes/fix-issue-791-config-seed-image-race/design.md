## Context

`config-seed` runs one command, `python -m src.cli.tools.config_seed`
(`base-compose.yaml:180`, explicit `command:`), so the image CMD does not matter for it.
It needs only the `src` tree and the Python dependencies, which both the chat image and the
benchmark image have. The issue reports that the `embtrial-dry` stack's `config-seed` ran
on the benchmark image and exited 0.

The `benchmark` service has no explicit `command:`. It depends on the image CMD
(`Dockerfile-benchmarks:39`, `Dockerfile-benchmarks-gpu:87`:
`python -u src/bin/service_benchmark.py`). So the benchmark container is the one that the
tag collision breaks.

## Goals / Non-Goals

Goals:
- In a render with no chatbot, `config-seed` and `benchmark` build the image tag from equal
  build blocks, with and without `gpu_ids`.
- `config-seed` still records the deployed `APP_VERSION` in a benchmark-only deploy.
- Every render that includes a chatbot is byte-identical to `origin/dev`.

Non-Goals:
- No change to any Dockerfile or to `src/cli/utils/service_builder.py` (issue constraint).
- No fix for GPU deploys with a chatbot (fasrc/archi#810; see D5).
- No live deploy. The `docker inspect` check is an operator step after merge.

## Decisions

D1. Make the two builds identical (the issue's recommended option), not a separate tag for
`config-seed` (the issue's alternative). Identical builds make the race harmless with no
extra image name. A separate tag would build the full chat image in every benchmark-only
deploy only to run `config_seed`.

D2. The condition is `chatbot_image is not defined`. This is the exact condition under
which `{{ chatbot_image | default(benchmarking_image) }}` falls back to the benchmark
image. The template sets it once with `{%- set config_seed_on_benchmark_image = ... %}` in
the `build:` block, and the `environment:` block reuses it. A render with neither a chatbot
nor a benchmark (for example `enabled_services=["data-manager"]`) also takes this branch.
That render already has `image: :` on `origin/dev`, so it cannot deploy before or after
this change. A `benchmarking_image is defined` guard would protect only that render, and
no test could pin it, so the condition leaves it out.

D3. The benchmark branch copies the `benchmark` service's build block exactly:
`context: .`, `dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-benchmarks{{ '-gpu' if gpu_ids else '' }}`,
and no `args`. The `-gpu` suffix is needed: without it, a GPU benchmark-only deploy keeps
the race (CPU `Dockerfile-benchmarks` against `Dockerfile-benchmarks-gpu`). Keeping
`args: APP_VERSION` would make the two build blocks unequal, and the test compares whole
build blocks.

D4. Pass `APP_VERSION` through `environment`, quoted: `APP_VERSION: "{{ app_version }}"`.
`deployment_record.build_deployment_record` reads `APP_VERSION` from the process
environment (`src/utils/deployment_record.py:80`). Before this change, a benchmark-only
deploy recorded the version only when the chat build won the race. The value comes from
`git describe --tags --always --dirty` (`templates_manager.get_git_version`), which can
be an all-digit short SHA. The quotes keep YAML from reading it as a number. Renders with
a chatbot do not get the line, so they stay byte-identical.

D5. The GPU-with-chatbot race stays out of scope. Issue #791 says: "Do not change deploys
that include a chatbot: their rendered `config-seed` and `chatbot` services must stay
byte-identical." The test file records that race as
`test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks` with
`@pytest.mark.xfail(strict=True, raises=AssertionError, reason="fasrc/archi#810: ...")`.
`raises=AssertionError` makes sure that a render error does not count as the expected
failure. `strict=True` makes the fix for #810 remove the marker.

D6. The explanation in the template is a Jinja comment (`{#- ... #}`), not a YAML
comment, so it does not reach the rendered file and chatbot renders stay byte-identical.

D7. The test renders the real template through `ServiceBuilder.build_compose_config(...)`
and `plan.to_template_vars()`, with Jinja `ChainableUndefined`, the same as
`tests/unit/test_base_compose_benchmark_configs_mount.py` and the CLI
(`src/cli/cli_main.py:43-47`). It compares parsed YAML. `_builds_by_image` skips a service
that has `build:` and no `image:` (`postgres`, `db-migrate`): compose names those images
from the project and the service, so they share no tag.

## Measurement

Loop 1 measured these on a scratch copy of `origin/dev` `5f3d80e9` and then repeated
them in the archi-bot checkout (Appendix A applied and then reverted).

| Stage | Command | Result |
|---|---|---|
| red: test only, template from `origin/dev` | `python -m pytest -q tests/unit/test_base_compose_image_tags.py` | `5 failed, 2 passed, 1 xfailed` |
| green: Appendix A | same | `7 passed, 1 xfailed` |
| neighbours | `python -m pytest -q tests/unit/ -k compose` | `67 passed, 7952 deselected, 1 xfailed` |
| template readers | `test_base_image_preflight.py`, `test_benchmark_anchor_staging.py`, and every unit test file that names `base-compose`, `Dockerfile-chat` or `Dockerfile-benchmarks` | `746 passed, 21 skipped, 1 xfailed` |

The 5 red failures: `..._builds_no_tag_from_two_build_blocks[cpu]` and `[gpu]`,
`..._builds_config_seed_as_the_benchmark_image[cpu]` and `[gpu]`, and
`..._passes_the_app_version_to_config_seed` (`KeyError: 'APP_VERSION'`).

Render diff (Appendix B), `origin/dev` template against the patched template:

| Services | `gpu_ids` | Rendered text |
|---|---|---|
| `chatbot,benchmarking` | none | identical |
| `chatbot,benchmarking` | `all` | identical |
| `chatbot` | none | identical |
| `chatbot` | `all` | identical |
| `benchmarking` | none | differs: `dockerfile` → `Dockerfile-benchmarks`, `args` gone, `APP_VERSION: "v1"` added to `environment` |
| `benchmarking` | `all` | differs: `dockerfile` → `Dockerfile-benchmarks-gpu`, `args` gone, `APP_VERSION: "v1"` added to `environment` |

Broken variants of the template. Each one fails the named tests:

| Variant | Failing tests |
|---|---|
| no `-gpu` suffix in the benchmark branch | `..._builds_no_tag_from_two_build_blocks[gpu]`, `..._builds_config_seed_as_the_benchmark_image[gpu]` |
| `args: APP_VERSION` kept in the benchmark branch | `..._builds_no_tag_from_two_build_blocks[cpu]` and `[gpu]`, `..._builds_config_seed_as_the_benchmark_image[cpu]` and `[gpu]` |
| `APP_VERSION` env line with no condition | `test_a_chatbot_render_keeps_config_seed_on_the_chat_build` |
| no `APP_VERSION` env line | `test_a_benchmark_only_render_passes_the_app_version_to_config_seed` |
| `-gpu` suffix added to the chat branch (the #810 fix) | `test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks` (strict XPASS) |

## Risks / Trade-offs

- Render tests check structure, not run time. The runtime claim (the benchmark container
  CMD contains `service_benchmark.py` every time) needs a live `archi evaluate` and
  `docker inspect`. That is the operator check in the spec. It follows from equal build
  blocks: both builds give the same image, so either build can own the tag.
- An unused build arg is not the problem here: the benchmark branch has no `args`, so
  Docker prints no "build-arg not consumed" warning.
- The template is inside the feature-matrix campaign's locked code tree. A running campaign
  needs a `--relock` after the host redeploys with this change. The redeploy is the
  operator's.
