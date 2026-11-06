## 1. Build config-seed from the chatbot's Dockerfile on GPU deploys (fasrc/archi#810)

- [x] 1.1 Red then green in one task (do not commit a red state). Write Appendix A to
      `/tmp/810-A.diff` with the Write tool and run `git apply -p1 /tmp/810-A.diff` from the
      repository root. It removes the strict xfail marker, adds one test in
      `tests/unit/test_base_compose_image_tags.py`, and changes the `config-seed`
      `dockerfile:` line and its Jinja comment in `src/cli/templates/base-compose.yaml`
      (design D2, D3). Do not rewrite the patch; copy it. To see the red step first, run
      `git stash push src/cli/templates/base-compose.yaml`, run
      `python -m pytest -q tests/unit/test_base_compose_image_tags.py` and confirm EXACTLY
      `2 failed, 7 passed` (the failures are
      `test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks` and
      `test_a_gpu_chatbot_render_builds_config_seed_as_the_chatbot_image`), then
      `git stash pop` and confirm EXACTLY `9 passed`. Confirm that
      `grep -n xfail tests/unit/test_base_compose_image_tags.py` prints nothing. Run
      `python -m pytest -q tests/unit/ -k compose` and confirm `69 passed` with no
      `xfailed` and no `failed` (the deselected count can differ). Then write Appendix B's
      script to `/tmp/810-render.py` with the Write tool, run Appendix B's commands from the
      repository root, and confirm the output lines listed there. Confirm that
      `git status --porcelain` lists only the two files of the patch (and this
      `tasks.md`). If a count or a line differs, STOP and write what you saw under
      "## Measurement" in `design.md`. Tick this box. Run `bash scripts/gate.sh` in the
      foreground (never in the background) and wait for exit 0. Commit the patch and the
      tick together only when the gate is green (message:
      `fix(compose): build config-seed from the chatbot's dockerfile on gpu deploys`).

## 2. Publish

- [ ] 2.1 Tick this box, commit the tick through the hook (message: `mark #810 tasks
      complete`), then push and open the PR, so the push carries every commit. Push with
      `git push -u origin fix/issue-810-gpu-config-seed-dockerfile`. Open the PR with
      `gh pr create --repo fasrc/archi --base dev --head fix/issue-810-gpu-config-seed-dockerfile`,
      the title `fix(compose): build config-seed from the chatbot's dockerfile on gpu deploys`,
      and `Closes #810` in the BODY (not the title). Pass the body with `--body` or a file
      under `/tmp`; do NOT commit a PR-body file to the repository. In the body: copy the
      "## Measurement" tables of `design.md`; say that every render without `gpu_ids`, and
      every benchmark-only render, is byte-identical to `origin/dev`, and that a GPU
      chatbot render changes one line; say that the PR also adds this plan folder
      (`openspec/changes/fix-issue-810-gpu-config-seed-dockerfile/`), the same as PR #811,
      so `git diff --name-only origin/dev...HEAD` lists it besides the two code files;
      give the archive note from design D4 in one sentence; say that fasrc/archi#814 tracks
      the base-image preflight, which still always requires the python base; add the
      operator check after merge: deploy a chatbot with `--gpu-ids all`, then
      `docker inspect -f '{{json .Config.Env}}' <chatbot container>` must contain
      `VLLM_WORKER_MULTIPROC_METHOD=spawn`. Confirm
      `git ls-remote origin refs/heads/fix/issue-810-gpu-config-seed-dockerfile` prints the same SHA as `git rev-parse HEAD`.
      STOP at the open PR - do not merge.

## Appendix (Loop 1 scratch evidence, 2026-11-06)

Loop 1 ran every command below in scratch copies of `origin/dev` `82487ada` (full
`git archive` tree) and recorded the output. The patch applies to `origin/dev` with
`git apply --check -p1`, and the result is byte-identical to the staged files. Copy the
text exactly. These appendix sections have no checkboxes.

### Appendix A (task 1.1, test and `base-compose.yaml`)

````diff
--- a/src/cli/templates/base-compose.yaml
+++ b/src/cli/templates/base-compose.yaml
@@ -145,12 +145,14 @@
           tag, so it builds the benchmark image's Dockerfile: compose builds services
           in parallel and the last build to finish owns a shared tag
           (fasrc/archi#791). The benchmark image does not bake APP_VERSION in, so
-          the environment block below passes it for the deployment record. #}
+          the environment block below passes it for the deployment record. With a
+          chatbot, config-seed carries the chatbot's tag, so it builds the chatbot's
+          Dockerfile, GPU suffix included (fasrc/archi#810). #}
       {%- set config_seed_on_benchmark_image = chatbot_image is not defined %}
       {%- if config_seed_on_benchmark_image %}
       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-benchmarks{{ '-gpu' if gpu_ids else '' }}
       {%- else %}
-      dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat
+      dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat{{ '-gpu' if gpu_ids else '' }}
       args:
         APP_VERSION: {{ app_version }}
       {%- endif %}
--- a/tests/unit/test_base_compose_image_tags.py
+++ b/tests/unit/test_base_compose_image_tags.py
@@ -6,7 +6,9 @@
 containers start from that image. In a deploy with no chatbot, ``config-seed``
 fell back to the benchmark image's tag but built Dockerfile-chat, so the
 ``benchmark`` container could start ``service_chat.py`` instead of
-``service_benchmark.py``.
+``service_benchmark.py``. A GPU deploy with a chatbot had the same race
+between ``config-seed`` (Dockerfile-chat) and ``chatbot`` (Dockerfile-chat-gpu)
+(fasrc/archi#810).
 """
 
 from pathlib import Path
@@ -126,15 +128,23 @@
     assert "APP_VERSION" not in config_seed["environment"]
 
 
-@pytest.mark.xfail(
-    strict=True,
-    raises=AssertionError,
-    reason=(
-        "fasrc/archi#810: with a GPU chatbot, config-seed builds Dockerfile-chat "
-        "and the chatbot builds Dockerfile-chat-gpu under one tag"
-    ),
-)
 def test_a_gpu_chatbot_render_builds_no_tag_from_two_build_blocks(tmp_path):
     compose = _render_compose(tmp_path, ["chatbot", "benchmarking"], gpu_ids="all")
 
     assert _conflicting_builds(compose) == {}
+
+
+def test_a_gpu_chatbot_render_builds_config_seed_as_the_chatbot_image(tmp_path):
+    """config-seed builds the chatbot's GPU Dockerfile (fasrc/archi#810)."""
+    compose = _render_compose(tmp_path, ["chatbot", "benchmarking"], gpu_ids="all")
+    config_seed = compose["services"]["config-seed"]
+    chatbot = compose["services"]["chatbot"]
+
+    assert config_seed["image"] == chatbot["image"]
+    assert config_seed["build"] == chatbot["build"]
+    assert config_seed["build"] == {
+        "context": ".",
+        "dockerfile": f"{_DOCKERFILES}/Dockerfile-chat-gpu",
+        "args": {"APP_VERSION": _APP_VERSION},
+    }
+    assert "APP_VERSION" not in config_seed["environment"]
````

### Appendix B (task 1.1, render diff)

Script for `/tmp/810-render.py`:

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
mkdir -p /tmp/810-base
git show "$(git merge-base HEAD origin/dev)":src/cli/templates/base-compose.yaml > /tmp/810-base/base-compose.yaml
for combo in chatbot,benchmarking:none chatbot:none benchmarking:none benchmarking:all chatbot,benchmarking:all chatbot:all; do
  s=${combo%%:*}; g=${combo##*:}
  if diff -q <(PYTHONPATH=. python /tmp/810-render.py /tmp/810-base "$s" "$g") <(PYTHONPATH=. python /tmp/810-render.py src/cli/templates "$s" "$g") >/dev/null; then echo "$s gpu=$g identical"; else echo "$s gpu=$g differs"; fi
done
diff <(PYTHONPATH=. python /tmp/810-render.py /tmp/810-base chatbot,benchmarking all) <(PYTHONPATH=. python /tmp/810-render.py src/cli/templates chatbot,benchmarking all)
diff <(PYTHONPATH=. python /tmp/810-render.py /tmp/810-base chatbot all) <(PYTHONPATH=. python /tmp/810-render.py src/cli/templates chatbot all)
````

Expected output, exactly:

````text
chatbot,benchmarking gpu=none identical
chatbot gpu=none identical
benchmarking gpu=none identical
benchmarking gpu=all identical
chatbot,benchmarking gpu=all differs
chatbot gpu=all differs
101c101
<       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat
---
>       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat-gpu
101c101
<       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat
---
>       dockerfile: archi_code/cli/templates/dockerfiles/Dockerfile-chat-gpu
````
