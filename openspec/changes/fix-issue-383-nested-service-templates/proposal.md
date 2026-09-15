## Why

`service_templates` (`src/cli/managers/base_image_preflight.py:87`) derives the declared
service set with `directory.glob("Dockerfile*")`. `Path.glob` does not recurse, so a
Dockerfile one directory down is never a member of the set, and every guard that reads the
declaration inherits the blind spot: `templates_missing_base_reference` (`:109`),
`_refuse_uncoverable_templates` (`:521`), `enforce_base_images` (`:537`), and the count
assertions at `tests/unit/test_base_image_preflight.py:1260` and
`tests/unit/test_python_version_declaration.py:436`.

Measured on this branch's base (`origin/dev` at `7c9915d0`), against a temporary directory
holding a digest-pinned top-level `Dockerfile-chat` plus `nested/Dockerfile-svc` on
`FROM docker.io/library/python:3.11`:

```
service_templates:                 ['Dockerfile-chat']
templates_missing_base_reference:  []
```

The nested template is invisible. Packaging and deployment are recursive — package data
ships `templates/**/*` and `TemplatesManager` copies the whole tree — so such a template is
really deployed while no guard can see it. That is the exact "silently outside every guard"
failure fasrc/archi#361 existed to end, one directory level down.

## What Changes

`service_templates` recurses, and `NON_SERVICE_TEMPLATES` is re-keyed from **filename** to
**path relative to the template directory**.

Re-keying is the actual work, not the `rglob` call. Two nested Dockerfiles already exist and
both must stay out of the service set:

```
src/cli/templates/dockerfiles/base-python-image/Dockerfile
src/cli/templates/dockerfiles/base-pytorch-image/Dockerfile
```

They define the `a2rchi-python-base` and `a2rchi-pytorch-base` images themselves — the same
role as the already-excluded top-level `Dockerfile-base` and `Dockerfile-base-gpu`. A plain
switch to `rglob` pulls both in, and a filename-keyed exclusion list cannot express them:
both files are named exactly `Dockerfile`. Adding `"Dockerfile"` as a key would exclude by a
name that says nothing about which file it means, and would also exclude any future
top-level file named `Dockerfile`.

Measured counts on this base: 21 `Dockerfile*` files in the tree, 19 of them top-level, 2
nested. After the change the exclusion list holds 6 keys and the service set stays at **15**.
A service count that moves is a finding, not an assertion to adjust.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None at the spec-directory level. The `service-base-images` capability is declared by
`openspec/changes/fix-issue-361-declare-service-templates`, which is **merged but not yet
archived**, so `openspec/specs/service-base-images/` does not exist. This change therefore
carries an `## ADDED Requirements` delta under `specs/service-base-images/`, never
`## MODIFIED`.

## Impact

- `src/cli/managers/base_image_preflight.py` — `NON_SERVICE_TEMPLATES` (`:34`),
  `service_templates` (`:87`), `stale_template_exclusions` (`:99`) and the docstrings that
  say "four".
- `tests/unit/test_base_image_preflight.py` — the 19 / 15 / 4 assertions at `:1260`, plus
  new nested-template tests.
- `tests/unit/test_python_version_declaration.py` — reads `service_templates`; the pin set
  is unchanged because the two nested files stay excluded.
- `scripts/dev/update_service_base_images.py` — audited, not changed. It walks every
  `Dockerfile*` as a text rewriter rather than reading the declaration, so narrowing it to
  the declared set would be wrong. Its own `glob` at `:320` and `:378` is non-recursive; that
  is a separate gap and is recorded, not fixed here.
- No file under `src/cli/templates/dockerfiles/` is added, removed, moved, or edited.
