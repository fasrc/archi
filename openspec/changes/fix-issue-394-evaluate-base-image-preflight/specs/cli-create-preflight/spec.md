## ADDED Requirements

### Requirement: The benchmarking runtime is base-image safe before it is torn down

`archi evaluate --force` SHALL establish that every `a2rchi-*-base` image its declared service templates build FROM is obtainable, before it performs any destructive action against an existing benchmarking runtime.

The guarantee `archi create` carries (`cli_main.py:282` above `cli_main.py:294`) did not
extend to `evaluate`: the module had two teardown call sites and one `enforce_base_images`
call site, and nothing between the compose plan (`cli_main.py:890`) and the teardown
(`cli_main.py:900`) could refuse the run. A benchmarking run could therefore destroy a
working runtime and only then fail on a base image the preflight would have refused.

The scope is the declared service set as `enforce_base_images` already defines it, using the
same call shape as `create`. The existing two-image rule already tracks what `evaluate`
builds — `base-compose.yaml:675` selects the GPU benchmarking template when `gpu_ids` is
truthy, and `required_base_image_names()` returns the pytorch base under exactly that
condition — so no narrowing is needed to reach the right a2rchi images. A refusal caused by
a template this run would not build is acceptable and deliberate: it happens before the
teardown, and it is the breadth `create` already carries. Narrowing
`_refuse_uncoverable_templates` would re-open the fail-open that `fasrc/archi#381` closed.

**What this requirement deliberately does not cover.** `NON_SERVICE_TEMPLATES`
(`base_image_preflight.py:43-51`) excludes `Dockerfile-postgres` and `Dockerfile-grafana`
because they build on third-party images, so `docker.io/pgvector/pgvector:pg17` is never
probed even though every `evaluate` plan enables postgres (`cli_main.py:855`). A host that
cannot obtain that image therefore still reaches the teardown. That gap is pre-existing,
identical on the `create` path, and closing it requires an availability-only probe class —
`run_preflight` floor-checks every AVAILABLE reference (`base_image_preflight.py:935-938`),
which a non-Python base cannot satisfy. It is tracked as `fasrc/archi#444` and is out of
scope here; this requirement claims only the a2rchi bases.

The operator-visible contract is that an `archi evaluate --force` which was always going to
fail on a base image leaves the existing benchmarking runtime exactly as it found it.

#### Scenario: Forced evaluate whose base image cannot be obtained

- **WHEN** `archi evaluate --force -n smoke` is invoked against an existing benchmarking
  runtime, and the container probe reports the required base image as unobtainable because
  the registry refuses the credentials
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing runtime directory and its contents are left intact

#### Scenario: Forced evaluate with an uncoverable service template

- **WHEN** `archi evaluate --force -n smoke` is invoked against an existing benchmarking
  runtime, and one service template under the template directory declares a base reference
  the preflight cannot cover, even though the templates this run builds are all healthy
- **THEN** the command exits non-zero
- **AND** the error names the uncoverable template, so the operator can act on it
- **AND** `delete_deployment()` is never called
- **AND** the existing runtime directory and its contents are left intact
- **AND** no image was pulled, because the refusal precedes all image work

#### Scenario: Forced evaluate whose recorded source checkout is unusable

- **WHEN** `archi evaluate --force -n smoke` is invoked from a non-editable install whose
  recorded checkout no longer holds the `src`, `pyproject.toml` and `LICENSE` that
  `copy_source_code()` copies, or no longer holds the template directory
- **THEN** the command exits non-zero
- **AND** the error names the recorded checkout and what is missing from it
- **AND** `delete_deployment()` is never called
- **AND** the existing runtime directory and its contents are left intact

This scenario exists because the preflight must read the tree the build reads. Resolving the
template directory from the recorded checkout and then *falling back* to the installed
templates when that checkout is unreadable would establish nothing: `_stage_source_copy`
copies from the recorded checkout below the teardown and raises there. The fallback is
therefore available only when no checkout is recorded at all, where the installed location is
the build tree.

#### Scenario: The Python floor is read from the tree the Dockerfiles came from

- **WHEN** the preflight resolves the service Dockerfiles from a recorded source checkout,
  and that checkout's `pyproject.toml` declares a `requires-python` floor above the Python
  version the base image carries
- **THEN** the command exits non-zero
- **AND** the refusal names that floor, not the floor recorded in the installed
  distribution's metadata
- **AND** `delete_deployment()` is never called

Every question the preflight asks about "the tree being deployed" SHALL resolve from one
recorded root. Reading the Dockerfiles from the recorded checkout while reading
`requires-python` from the installed distribution's metadata is a fail-open on the exact
check this module performs: the metadata is frozen at install time, so an operator who
raises the floor in the checkout afterwards gets a base image approved against the old
floor, the teardown, and then a `pip install .` inside the image that rejects the
interpreter. Where a caller pins the template directory explicitly, it owns both halves of
that tree and the recorded checkout SHALL NOT supply the floor.

#### Scenario: No recorded checkout resolves one package root, for both the probe and the copy

- **WHEN** `archi evaluate --force` runs from a source tree that records no checkout, so the
  preflight accepts the installed template location as the build tree
- **THEN** the source copy that runs below the teardown resolves that same package root
- **AND** the deployment does not fail staging its source after the existing runtime is gone

The no-recorded-checkout answer is only sound if both halves agree on it. The source copy's
fallback resolved the module *file* rather than a directory, so the path it looked for could
never exist and `--force` destroyed the runtime and then failed deterministically. The
preflight's fallback and the source copy's fallback SHALL derive the same root.

#### Scenario: An evaluate that passes the preflight still tears down as before

- **WHEN** `archi evaluate --force -n smoke` is invoked against an existing benchmarking
  runtime and every declared base image resolves and verifies
- **THEN** the teardown proceeds exactly as it did before this requirement existed
- **AND** the post-removal existence guard still refuses when the directory survived removal

#### Scenario: The create path keeps its own guarantee unchanged

- **WHEN** `archi create --force` is invoked against an existing deployment with an
  unobtainable base image, or with an uncoverable service template
- **THEN** the command exits non-zero
- **AND** `delete_deployment()` is never called
- **AND** the existing deployment directory and its contents are left intact

This scenario is listed because the change adds a second caller of a shared entry point. A
fix that reached the evaluate guarantee by altering `enforce_base_images` itself could
satisfy the scenarios above and break this one.
