## ADDED Requirements

### Requirement: The shared base requirements SHALL NOT pin duckdb
The project SHALL NOT declare a `duckdb` dependency in `requirements/requirements-base.txt`, in `src/cli/templates/dockerfiles/base-python-image/requirements.txt`, or in `src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt`. No module under `src/` or `tests/` imports duckdb, `pyproject.toml` does not declare it, and nothing resolves it transitively, so the pin obligates every base image to carry a package no code can reach. It is also the one pin in the shared base requirements with no cp312 wheel and no buildable source in a slim image, which is why a workaround exists to strip it and why it blocks a later Python 3.12 move.

#### Scenario: No base requirements file declares duckdb
- **WHEN** the three shared base requirements files are read
- **THEN** none of them contains a line declaring a `duckdb` dependency at any version

#### Scenario: No published or built image installs duckdb from these files
- **WHEN** a base image is built from the shared base requirements
- **THEN** duckdb is not installed as a declared dependency of that image
- **AND** no service loses a capability, because no module imports it

### Requirement: A reintroduced duckdb pin SHALL fail the test suite
The system SHALL fail its test suite when a `duckdb` dependency reappears in any of the three shared base requirements files, so that a merge from upstream cannot silently restore the removed pin. The upstream project still carries this pin in its own base requirements, which makes reintroduction a routine expectation rather than a hypothetical one; without an enforcing test, the restoration produces no failing test, no broken image, and no reviewer signal.

The guard SHALL identify a dependency by distribution name rather than by an exact version string, so a reintroduction at a different version or with a different specifier is caught. It SHALL NOT flag a distinct distribution whose name merely begins with `duckdb`.

#### Scenario: The pin returns at its original version
- **WHEN** a shared base requirements file again declares `duckdb` at the previously removed version
- **THEN** the test suite fails
- **AND** the failure message names the offending file and the line number

#### Scenario: The pin returns at a different version or specifier
- **WHEN** a shared base requirements file declares duckdb at any other version, or with a non-equality specifier such as a lower bound
- **THEN** the test suite fails, exactly as it does for the original version

#### Scenario: Every offending file is reported in one run
- **WHEN** more than one of the three files declares duckdb
- **THEN** the failure message names all of the offending locations
- **AND** it does not report only the first one found

#### Scenario: A different distribution beginning with duckdb is not flagged
- **WHEN** a shared base requirements file declares a distinct distribution whose name begins with `duckdb` followed by a hyphen
- **THEN** the guard does not flag it
- **AND** the test suite passes

#### Scenario: A clean tree passes
- **WHEN** none of the three files declares duckdb
- **THEN** the guard passes

### Requirement: The pin guard SHALL run in every environment the gating suite runs in
The guard SHALL be collected and executed wherever the gating test suite runs, and SHALL NOT be disabled by any environment-conditional skip. A guard that skips outside a git checkout reports green while asserting nothing, which is indistinguishable from the regression it exists to catch and is most likely to happen in exactly the environments — an sdist, a container that copied the tree in, a shallow checkout — where no reader would think to question a green run.

The guard SHALL therefore locate the files it inspects by a path resolved relative to the test module itself, not by consulting version control, and SHALL live outside any test module that carries a module-level skip for non-git-checkout environments.

#### Scenario: The guard runs without a version-control checkout
- **WHEN** the test suite is run from a tree that is not a version-control checkout
- **THEN** the guard is collected and executed rather than skipped
- **AND** it reports pass or fail on the actual contents of the three files

#### Scenario: The guard is not placed behind an existing conditional skip
- **WHEN** the guard's test module is loaded
- **THEN** no module-level skip condition governs it

### Requirement: Removing the pin SHALL NOT regenerate the derived requirements files
The change that removes the pin SHALL alter the two generated base-image requirements files by deleting the duckdb line alone, and SHALL NOT regenerate them from their generator. Those tracked files have drifted from their generator's output by a substantial margin, so regeneration would add unrelated packages, change unrelated version pins, and rewrite comment blocks — presenting a dependency-bump-shaped diff for review under an issue about deleting one dead line. Reconciling that drift is separate work with its own review.

#### Scenario: The diff is exactly three deleted lines
- **WHEN** the change's diff against the trunk is inspected across the requirements directory and the base-image template directories
- **THEN** it contains exactly three deleted lines and no added lines
- **AND** a larger count indicates the derived files were regenerated and the change must be redone surgically

#### Scenario: The pre-existing generator drift is left in place
- **WHEN** the two derived files are compared against their generator's output after the change
- **THEN** they still differ by the drift that predated the change
- **AND** no unrelated package, version pin, or comment block was altered

#### Scenario: The workaround that strips the pin is left untouched
- **WHEN** the change's diff is inspected for control-plane files
- **THEN** the container definition carrying the duckdb-stripping filter is unchanged
- **AND** the now-redundant filter is recorded as a follow-up for a human rather than removed
