## ADDED Requirements

### Requirement: The benchmarking runtime is base-image safe before it is torn down

`archi evaluate --force` SHALL establish base-image safety over its whole declared service set before it performs any destructive action against an existing benchmarking runtime.

The guarantee `archi create` carries (`cli_main.py:282` above `cli_main.py:294`) did not
extend to `evaluate`: the module had two teardown call sites and one `enforce_base_images`
call site, and nothing between the compose plan (`cli_main.py:890`) and the teardown
(`cli_main.py:900`) could refuse the run. A benchmarking run could therefore destroy a
working runtime and only then fail on a base image the preflight would have refused.

The scope is the whole declared service set, using the same call shape as `create`. The
existing two-image rule already tracks what `evaluate` builds — `base-compose.yaml:675`
selects the GPU benchmarking template when `gpu_ids` is truthy, and
`required_base_image_names()` returns the pytorch base under exactly that condition — so no
narrowing is needed to reach the right images. A refusal caused by a template this run would
not build is acceptable and deliberate: it happens before the teardown, and it is the
breadth `create` already carries. Narrowing `_refuse_uncoverable_templates` would re-open
the fail-open that `fasrc/archi#381` closed.

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
