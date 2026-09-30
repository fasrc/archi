## ADDED Requirements

### Requirement: The declared service set reaches templates at any depth

The declared service-template set SHALL include every `Dockerfile*` file at any depth under the template directory, and the exclusion list SHALL be keyed by each file's path relative to that directory.

fasrc/archi#361 declared the set as a derivation with an explicit exclusion list, so that a
template added to the repository is a service template on the day it is added. That default
only held at depth 1, because the derivation used `Path.glob`, which does not recurse. One
directory down the default silently inverted.

Measured on `origin/dev` at `7c9915d0`, against a directory holding a digest-pinned top-level
`Dockerfile-chat` and `nested/Dockerfile-svc` on `FROM docker.io/library/python:3.11`:

```
service_templates:                 ['Dockerfile-chat']
templates_missing_base_reference:  []
```

The nested template is in neither. Packaging ships `templates/**/*` and `TemplatesManager`
copies the whole tree, so a nested template is really deployed while being invisible to every
guard that reads the declaration.

The exclusion keys have to become relative paths, and that is the substance of this
requirement rather than the recursion itself. Two nested Dockerfiles already exist —
`base-python-image/Dockerfile` and `base-pytorch-image/Dockerfile` — and both define an
a2rchi base image themselves, the same role as the excluded top-level `Dockerfile-base` and
`Dockerfile-base-gpu`. Both are named exactly `Dockerfile`, so a filename-keyed list cannot
name either one without also naming the other and any future top-level `Dockerfile`.

#### Scenario: A nested template with no a2rchi base reference is reported

- **WHEN** a `Dockerfile*` file in a subdirectory of the template directory is not named in the exclusion list
- **AND** its `FROM` line names no `a2rchi-*-base` image
- **THEN** the declared service set contains that file
- **AND** the missing-base-reference report names it by path

#### Scenario: The two nested base-image templates stay out of the set

- **WHEN** the declared service set is derived from the real template directory
- **THEN** `base-python-image/Dockerfile` is not a member
- **AND** `base-pytorch-image/Dockerfile` is not a member

Asserted against the real directory on purpose. A temporary fixture would prove the exclusion
mechanism works and would not notice a future traversal change pulling the real files in.

#### Scenario: A stale relative-path exclusion fails rather than widening the set

- **WHEN** the exclusion list holds a relative-path key naming a file that does not exist under the template directory
- **THEN** the stale-exclusion report names that key
- **AND** the unit suite fails

A key that names nothing excludes nothing, so the set widens by one, silently, and the
widening is invisible because the guard that would notice is the one the stale key defeats.
This guarantee already existed for filename keys; it must survive the re-keying.

#### Scenario: Recursion adds members and exclusions without moving the service count

- **WHEN** the suite runs against the real template directory
- **THEN** the traversal finds 21 `Dockerfile*` files
- **AND** the exclusion list holds 6 keys, each carrying a reason string
- **AND** the declared service set has 15 members

15 before the change and 15 after means the recursion added exactly two files and the
re-keying excluded exactly those two. A service count that moved would mean the traversal
pulled in something nobody has classified — a finding to investigate, not an assertion to
adjust.

### Requirement: The deploy preflight refuses a nested service template it cannot cover

The deploy entry point SHALL refuse, and SHALL name the template, when a service template in a subdirectory of the template directory declares no base image reference the preflight can place.

"The deploy entry point" means `enforce_base_images` — the one function `archi create` calls
(`cli_main.py:282`). fasrc/archi#381 placed a refusal only in `required_base_images`, which
has no production caller, so the deploy path went on silently. A nested-template assertion at
the helper level alone would repeat that.

The failure this prevents is not an empty answer. `base_reference` returns the first matching
reference found in any template, so with one nested service template on a third-party base the
other 15 still supply both known base images. The preflight returns a complete-looking result
covering one fewer service than the build needs, and under `--force` the build failure lands
after `remove_existing_deployment()` has already run.

#### Scenario: A nested service template on a third-party base refuses the deploy

- **WHEN** a template in a subdirectory of the template directory is in the declared service set
- **AND** it declares no base reference the preflight can place
- **THEN** the deploy entry point raises rather than returning outcomes
- **AND** the refusal message names that template's path

#### Scenario: A fully covered tree is unaffected

- **WHEN** every member of the declared service set declares a base reference the preflight can place
- **THEN** the deploy entry point returns the same references it returned before this change

The refusal is a new failure path, not a new answer. A correct tree must compute exactly what
it computed before, or the change has altered the deploy behavior it was meant to guard.
