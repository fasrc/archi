## Why

`base_reference` (`src/cli/managers/base_image_preflight.py:123`) returns the **first**
reference whose text names the requested image and stops:

```python
directory = template_dir or TEMPLATE_DIR
for dockerfile in sorted(directory.glob("Dockerfile-*")):
    for match in _FROM_BASE_RE.finditer(dockerfile.read_text()):
        reference = match.group("ref")
        if image in reference:
            return reference
return None
```

Both entry points call it once per required image name — `required_base_images` (`:162`) and
`enforce_base_images` (`:583`) — so exactly one reference per base ever reaches
`run_preflight`, no matter how many templates declare one. Nothing anywhere checks that the
templates agree.

Measured on this branch's base (`origin/dev` at `7c9915d0`), against a temporary directory
holding two top-level service templates pinning the python base at different digests:

```
service_templates:                 ['Dockerfile-chat', 'Dockerfile-piazza']
templates_missing_base_reference:  []
base_reference:                    ghcr.io/fasrc/a2rchi-python-base@sha256:aaaa…aaaa
required_base_images:              ['ghcr.io/fasrc/a2rchi-python-base@sha256:aaaa…aaaa']
```

The `bbbb…bbbb` digest that `Dockerfile-piazza` builds from is never named, never probed, and
never version-checked. The preflight returns AVAILABLE, `create --force` runs
`remove_existing_deployment()` (`cli_main.py:294`), and the piazza build then fails on an
image nobody checked — the exact post-teardown failure this module exists to prevent
(fasrc/archi#266, and the ordering contract from #287).

This is the governing invariant of the module, stated in its own docstring, being broken by
the module itself: *every path either establishes that a base image is usable, refuses, or
says out loud that it could not tell*. A split pin passes silently on the assumption that the
one reference read is the one every template builds from.

**Why the risk is latent in-tree, and why that is not enough.**
`test_service_templates_pin_one_explicit_base_tag`
(`tests/unit/test_python_version_declaration.py:389`) asserts `len(builds) == 1` over every
`# base-image-pin:` annotation, so a split pin reddens CI. Measured against the real template
directory on this base, the guard holds: 15 service templates, **1 distinct reference** for
`a2rchi-python-base` and **1** for `a2rchi-pytorch-base`. Two gaps remain:

1. The guard keys on the **annotation**, not on the reference. Two templates carrying the same
   `# base-image-pin: dev-abc1234` line above different digests satisfy it.
2. It is a repository test. It never runs against the templates installed in an operator's
   environment, which is where `enforce_base_images` actually decides whether to tear a
   working deployment down.

Found by an adversarial review pass on PR #388 during the 2026-08-29 nightly review round 2.
The defect predates that PR — it is in `base_reference` on `dev`, not in #388's traversal
change — so it was deferred to this change rather than expanding a scoped review diff.

## What Changes

The preflight refuses, before any image work, when two service templates name the same
required base image at different references.

- A new `base_references(image, template_dir)` returns **every** distinct reference the
  declared service set gives for that image, in first-seen order, instead of stopping at the
  first.
- `base_reference` keeps its signature and its "first match" contract, and is redefined as the
  first element of `base_references`. The two cannot disagree by construction; today they
  cannot disagree by measurement either, because none of the four templates
  `NON_SERVICE_TEMPLATES` excludes carries an `a2rchi-*-base` `FROM` line (`Dockerfile-base`
  is on `docker.io/library/python:3.11`, `Dockerfile-base-gpu` on
  `docker.io/pytorch/pytorch:2.0.1-cuda11.7-cudnn8-devel`, `Dockerfile-postgres` on
  `docker.io/pgvector/pgvector:pg17`, `Dockerfile-grafana` on
  `docker.io/grafana/grafana-enterprise:10.2.0`).
- A new `_refuse_divergent_base_references(names, template_dir)` is called from the same two
  entry points that already call `_refuse_uncoverable_templates`, so the two entry points
  cannot disagree about what is refused. In `enforce_base_images` it lands after `names` is
  derived (`:576-578`) and before the reference loop (`:580`) — therefore before
  `run_preflight` (`:602`), and therefore before the teardown.
- The check is scoped to the images `required_base_image_names` returns. A split pytorch pin
  is not refused on a CPU-only create, because refusing on a base nothing will build is
  over-refusal. The docstring says so out loud, since the alternative reading is defensible.
- One reference per template. Divergence is a disagreement **between** templates; a single
  multistage template naming the same base twice is #382's subject, not this change's.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None at the spec-directory level. The `service-base-images` capability is declared by change
directories that are **merged but not yet archived**, so `openspec/specs/service-base-images/`
does not exist. This change therefore carries an `## ADDED Requirements` delta under
`specs/service-base-images/`, never `## MODIFIED`.

## Impact

- `src/cli/managers/base_image_preflight.py` — new `base_references` and
  `_refuse_divergent_base_references`; `base_reference` (`:123`) re-expressed over the first;
  `required_base_images` (`:138`) and `enforce_base_images` (`:537`) each gain one call.
- `tests/unit/test_base_image_preflight.py` — new divergence tests at the helper level and at
  the `enforce_base_images` entry point, plus an unchanged-behaviour test for the agreeing
  case on both the CPU and the GPU/grader selections.
- `docs/docs/developer_guide.md` — the new refusal case, next to what the preflight already
  refuses (`:538-544` on this base). AGENTS.md requires the docs change in the same PR as the
  user-facing behaviour change.
- No file under `src/cli/templates/dockerfiles/` is added, removed, moved, or edited. The
  in-tree templates agree today, so this change refuses nothing that is currently deployable.

**Two open PRs touch the same module and will conflict on merge; neither blocks this change.**
- #388 (`fix/issue-383-nested-service-templates`) makes `service_templates` recurse. This
  change reads `service_templates()` rather than re-deriving a traversal, so it inherits that
  fix on merge with no edit.
- #387 (`fix/issue-382-placeable-base`) makes the preflight judge a multistage template by the
  stage the deployment runs. This change reads one reference per template through a single
  seam for exactly that reason: after #387, that seam is the only place that has to learn
  about final stages.
