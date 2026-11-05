## 1. Build config-seed from the benchmark Dockerfile when no chatbot is deployed (fasrc/archi#791)

- [x] 1.1 Red then green in one task (do not commit a red state). Write Appendix A to
      `/tmp/791-A.diff` with the Write tool and run `git apply -p1 /tmp/791-A.diff` from the
      repository root. It adds `tests/unit/test_base_compose_image_tags.py` and changes the
      `config-seed` `build:` and `environment:` blocks in
      `src/cli/templates/base-compose.yaml` (design D2-D6). To see the red step first, run
      `git stash push src/cli/templates/base-compose.yaml`, run
      `python -m pytest -q tests/unit/test_base_compose_image_tags.py` and confirm EXACTLY
      `5 failed, 2 passed, 1 xfailed`, then `git stash pop` and confirm EXACTLY
      `7 passed, 1 xfailed`. Run `python -m pytest -q tests/unit/ -k compose` and confirm
      `67 passed` and `1 xfailed` (the deselected count can differ). Then write Appendix B's
      script to `/tmp/791-render.py` with the Write tool, run Appendix B's commands from the
      repository root, and confirm the output lines listed there. If a count or a line
      differs, STOP and write what you saw under "## Measurement" in `design.md`. Tick this
      box. Run `bash scripts/gate.sh` in the foreground and wait for exit 0. Commit the patch and
      the tick together only when the gate is green (message:
      `build config-seed from the benchmark dockerfile when no chatbot is deployed`).

## 2. Publish

- [ ] 2.1 Tick this box, commit the tick through the hook (message: `mark #791 tasks
      complete`), then push and open the PR, so the push carries every commit. Push with
      `git push -u origin fix/issue-791-config-seed-image-race`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-791-config-seed-image-race`,
      the title `fix(compose): build config-seed from the benchmark dockerfile when no chatbot is deployed`,
      and `Closes #791` in the BODY (not the title). Pass the body with `--body` or a file
      under `/tmp`; do NOT commit a PR-body file to the repository. In the body: copy the
      "## Measurement" tables of `design.md`; say that every render with a chatbot is
      byte-identical to `origin/dev` (with and without `gpu_ids`); give the `APP_VERSION`
      reason from design D4 in one sentence; say that GPU deploys WITH a chatbot have the
      same race, that issue #791 keeps chatbot deploys unchanged, and that fasrc/archi#810
      tracks the fix (a strict xfail test records it); add the operator check after merge:
      `archi evaluate -n race-check -c <benchmark config> -e ~/.archi/.env.benchmark --hostmode --force`,
      then `docker inspect -f '{{json .Config.Cmd}}' benchmarking-race-check` must contain
      `service_benchmark.py`; and say that a running feature-matrix campaign needs
      `--relock` after the host redeploys. STOP at the open PR - do not merge.

## Appendix (Loop 1 scratch evidence, 2026-11-05)

Loop 1 made this patch on a scratch copy of `origin/dev` `5f3d80e9` and ran every stage in
design.md "## Measurement" on it, then repeated the stages in the archi-bot checkout.
Appendix A applies with `git apply -p1` on `origin/dev` `5f3d80e9`, and the result is
byte-identical to the tested files. `black` 24.10.0 and `isort` 6.0.1 leave the test file
unchanged. Copy the patch and the script; do not rewrite them. A blank context line in a
patch is an empty line; `git apply` accepts it.

### Appendix A (task 1.1, test and `base-compose.yaml`)

````diff
diff --git a/src/cli/templates/base-compose.yaml b/src/cli/templates/base-compose.yaml
index 97b7d64..d694268 100644
--- a/src/cli/templates/base-compose.yaml
+++ b/src/cli/templates/base-compose.yaml
@@ -141,9 +141,19 @@ services:
     image: {{ chatbot_image | default(benchmarking_image) }}:{{ chatbot_tag | default(benchmarking_tag) }}
     build:
       context: .
+      {#- With no chatbot in the deploy, config-seed carries the benchmark image's
+          tag, so it builds the benchmark image's Dockerfile: compose builds services
+          in parallel and the last build to finish owns a shared tag
+          (fasrc/archi#791). The benchmark image does not bake APP_VERSION in, so
+          the environment block below passes it for the deployment record. #}
+      {%- set config_seed_on_benchmark_image = chatbot_image is not defined %}
+      {%- if config_seed_on_benchmark_image %}
+      dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-benchmarks{{ '-gpu' if gpu_ids else '' }}
+      {%- else %}
       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat
       args:
         APP_VERSION: {{ app_version }}
+      {%- endif %}
     container_name: {{ name }}-config-seed
     depends_on:
       postgres:
@@ -173,6 +183,9 @@ services:
       ARCHI_CONFIG_HEAD: ${ARCHI_CONFIG_HEAD:-}
       ARCHI_CONFIG_PIN_MATCHED: ${ARCHI_CONFIG_PIN_MATCHED:-}
       ARCHI_CONFIG_DIRTY_PATHS: ${ARCHI_CONFIG_DIRTY_PATHS:-}
+      {%- if config_seed_on_benchmark_image %}
+      APP_VERSION: "{{ app_version }}"
+      {%- endif %}
     env_file:
       - .env
     volumes:
diff --git a/tests/unit/test_base_compose_image_tags.py b/tests/unit/test_base_compose_image_tags.py
new file mode 100644
index 0000000..373d39a
--- /dev/null
+++ b/tests/unit/test_base_compose_image_tags.py
@@ -0,0 +1,140 @@
+"""One image tag, one build, in the rendered compose file (fasrc/archi#791).
+
+``docker compose up --build`` builds every service that has a ``build:`` block,
+and it builds them in parallel. When two services build different Dockerfiles
+under one image tag, the build that finishes last owns the tag, and both
+containers start from that image. In a deploy with no chatbot, ``config-seed``
+fell back to the benchmark image's tag but built Dockerfile-chat, so the
+``benchmark`` container could start ``service_chat.py`` instead of
+``service_benchmark.py``.
+"""
+
+from pathlib import Path
+
+import pytest
+import yaml
+from jinja2 import ChainableUndefined, Environment, FileSystemLoader
+
+from src.cli.utils.service_builder import ServiceBuilder
+
+_APP_VERSION = "v2026.10.0-7-g5f3d80e9"
+_DOCKERFILES = "archi_code/cli/templates/dockerfiles"
+_GPU_CASES = [pytest.param(None, id="cpu"), pytest.param("all", id="gpu")]
+
+
+def _render_compose(tmp_path, enabled_services, gpu_ids=None):
+    repository = Path(__file__).resolve().parents[2]
+    environment = Environment(
+        loader=FileSystemLoader(str(repository / "src/cli/templates")),
+        undefined=ChainableUndefined,
+    )
+    plan = ServiceBuilder.build_compose_config(
+        name="demo",
+        verbosity=3,
+        base_dir=tmp_path,
+        enabled_services=enabled_services,
+        secrets={"PG_PASSWORD"},
+        tag="dev",
+        gpu_ids=gpu_ids,
+    )
+    template_vars = plan.to_template_vars()
+    template_vars.update(
+        app_version=_APP_VERSION,
+        postgres_port=5432,
+        data_manager_port_host=7871,
+        data_manager_port_container=7871,
+        chatbot_port_host=7861,
+        chatbot_port_container=7861,
+        prompt_files=[],
+        rubrics=[],
+        evaluation_mcp_configured=False,
+    )
+    rendered = environment.get_template("base-compose.yaml").render(**template_vars)
+    return yaml.safe_load(rendered)
+
+
+def _builds_by_image(compose):
+    """Map each image tag that a service builds to ``{service: build block}``.
+
+    A service with a ``build:`` block and no ``image:`` gets an image name that
+    compose derives from the project and service names, so it shares no tag.
+    """
+    builds = {}
+    for service_name, service in compose["services"].items():
+        if "build" in service and "image" in service:
+            builds.setdefault(service["image"], {})[service_name] = service["build"]
+    return builds
+
+
+def _conflicting_builds(compose):
+    """Return the image tags that two services build from different build blocks."""
+    conflicts = {}
+    for image, by_service in _builds_by_image(compose).items():
+        first = next(iter(by_service.values()))
+        if any(build != first for build in by_service.values()):
+            conflicts[image] = by_service
+    return conflicts
+
+
+@pytest.mark.parametrize("gpu_ids", _GPU_CASES)
+def test_a_benchmark_only_render_builds_no_tag_from_two_build_blocks(tmp_path, gpu_ids):
+    compose = _render_compose(tmp_path, ["benchmarking"], gpu_ids=gpu_ids)
+
+    assert _conflicting_builds(compose) == {}
+
+
+@pytest.mark.parametrize("gpu_ids", _GPU_CASES)
+def test_a_benchmark_only_render_builds_config_seed_as_the_benchmark_image(
+    tmp_path, gpu_ids
+):
+    compose = _render_compose(tmp_path, ["benchmarking"], gpu_ids=gpu_ids)
+    config_seed = compose["services"]["config-seed"]
+    benchmark = compose["services"]["benchmark"]
+
+    assert config_seed["image"] == benchmark["image"]
+    assert config_seed["build"] == benchmark["build"]
+    assert config_seed["command"] == [
+        "bash",
+        "-lc",
+        "python -m src.cli.tools.config_seed",
+    ]
+
+
+def test_a_benchmark_only_render_passes_the_app_version_to_config_seed(tmp_path):
+    compose = _render_compose(tmp_path, ["benchmarking"])
+
+    environment = compose["services"]["config-seed"]["environment"]
+    assert environment["APP_VERSION"] == _APP_VERSION
+
+
+def test_a_chatbot_render_builds_no_tag_from_two_build_blocks(tmp_path):
+    compose = _render_compose(tmp_path, ["chatbot", "benchmarking"])
+
+    assert _conflicting_builds(compose) == {}
+
+
+def test_a_chatbot_render_keeps_config_seed_on_the_chat_build(tmp_path):
+    compose = _render_compose(tmp_path, ["chatbot", "benchmarking"])
+    config_seed = compose["services"]["config-seed"]
+
+    assert config_seed["image"] == compose["services"]["chatbot"]["image"]
+    assert config_seed["build"] == {
+        "context": ".",
+        "dockerfile": f"{_DOCKERFILES}/Dockerfile-chat",
+        "args": {"APP_VERSION": _APP_VERSION},
+    }
+    assert "APP_VERSION" not in config_seed["environment"]
+
+
+@pytest.mark.xfail(
+    strict=True,
+    raises=AssertionError,
+    reason=(
+        "fasrc/archi#810: with a GPU chatbot, config-seed builds Dockerfile-chat "
+        "and the chatbot builds Dockerfile-chat-gpu under one tag"
+    ),
+)
+def test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks(tmp_path):
+    compose = _render_compose(tmp_path, ["chatbot", "benchmarking"], gpu_ids="all")
+
+    assert _conflicting_builds(compose) == {}
````

### Appendix B (task 1.1, render diff)

Script for `/tmp/791-render.py`:

````python
"""Render base-compose.yaml from TEMPLATE_DIR for SERVICES (comma list) and GPU_IDS."""

import sys
import tempfile
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from src.cli.utils.service_builder import ServiceBuilder

template_dir, services, gpu_ids = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
plan = ServiceBuilder.build_compose_config(
    name="demo",
    verbosity=3,
    base_dir=Path(tempfile.mkdtemp()),
    enabled_services=services,
    secrets={"PG_PASSWORD"},
    tag="dev",
    gpu_ids=None if gpu_ids == "none" else gpu_ids,
)
template_vars = plan.to_template_vars()
template_vars.update(
    app_version="v1",
    postgres_port=5432,
    data_manager_port_host=7871,
    data_manager_port_container=7871,
    chatbot_port_host=7861,
    chatbot_port_container=7861,
    prompt_files=[],
    rubrics=[],
    evaluation_mcp_configured=False,
)
environment = Environment(
    loader=FileSystemLoader(template_dir), undefined=ChainableUndefined
)
sys.stdout.write(
    environment.get_template("base-compose.yaml").render(**template_vars)
)
````

Commands, from the repository root:

````bash
mkdir -p /tmp/791-base
git show "$(git merge-base HEAD origin/dev)":src/cli/templates/base-compose.yaml > /tmp/791-base/base-compose.yaml
for combo in chatbot,benchmarking:none chatbot,benchmarking:all chatbot:none chatbot:all benchmarking:none benchmarking:all; do
  s=${combo%%:*}; g=${combo##*:}
  if diff -q <(PYTHONPATH=. python /tmp/791-render.py /tmp/791-base "$s" "$g") <(PYTHONPATH=. python /tmp/791-render.py src/cli/templates "$s" "$g") >/dev/null; then echo "$s gpu=$g identical"; else echo "$s gpu=$g differs"; fi
done
diff <(PYTHONPATH=. python /tmp/791-render.py /tmp/791-base benchmarking all) <(PYTHONPATH=. python /tmp/791-render.py src/cli/templates benchmarking all)
````

Expected output, exactly:

````text
chatbot,benchmarking gpu=none identical
chatbot,benchmarking gpu=all identical
chatbot gpu=none identical
chatbot gpu=all identical
benchmarking gpu=none differs
benchmarking gpu=all differs
101,103c101
<       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat
<       args:
<         APP_VERSION: v1
---
>       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-benchmarks-gpu
132a131
>       APP_VERSION: "v1"
````
