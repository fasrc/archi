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
The system SHALL fail its test suite when a `duckdb` dependency reappears in any of the five monitored requirements files, so that a merge from upstream cannot silently restore the removed pin. The monitored set is the three shared base requirements files that carried the pin plus the two generator header files, `requirements/cpu-requirementsHEADER.txt` and `requirements/gpu-requirementsHEADER.txt`, which the generator concatenates ahead of `requirements/requirements-base.txt`: a declaration added to a header would leave the tracked outputs green until the next base-image build installed it. The upstream project still carries this pin in its own base requirements, which makes reintroduction a routine expectation rather than a hypothetical one; without an enforcing test, the restoration produces no failing test, no broken image, and no reviewer signal.

The guard SHALL identify a dependency by comparing PEP 503-normalized project names rather than by matching an exact version string or a literal name prefix. Normalization case-folds the name and folds runs of `-`, `_` and `.` to a single `-`, so a reintroduction at a different version, in different case, carrying an extras bracket, or guarded by an environment marker all resolve to the same distribution and are caught. It SHALL NOT flag a distinct distribution whose normalized name merely begins with `duckdb`, since normalization folds separators rather than deleting them.

The guard SHALL fail closed on a requirement line whose project name it cannot read. A bare archive, version-control or local-path requirement installs a distribution without naming it, so treating such a line as "not duckdb" would let the pin return in a shape the name comparison cannot see. The guard SHALL report those lines and fail rather than pass over them.

The guard SHALL also verify that its own monitored set matches the requirements files the generator references, by exact equality in both directions rather than by a subset check. A subset check fails open: were the generator restructured so its paths became undiscoverable, the discovered set would shrink and the check would still pass while coverage silently evaporated. Requiring equality turns any such restructuring into a failure that names the difference. This is a drift alarm rather than a proof of coverage, because paths built from variables or command substitution are not discoverable.

#### Scenario: The pin returns at its original version
- **WHEN** a shared base requirements file again declares `duckdb` at the previously removed version
- **THEN** the test suite fails
- **AND** the failure message names the offending file and the line number

#### Scenario: The pin returns at a different version or specifier
- **WHEN** a shared base requirements file declares duckdb at any other version, or with a non-equality specifier such as a lower bound
- **THEN** the test suite fails, exactly as it does for the original version

#### Scenario: Every offending file is reported in one run
- **WHEN** more than one of the monitored files declares duckdb
- **THEN** the failure message names all of the offending locations
- **AND** it does not report only the first one found

#### Scenario: A different distribution beginning with duckdb is not flagged
- **WHEN** a monitored file declares a distinct distribution whose name begins with `duckdb` followed by a separator
- **THEN** the guard does not flag it
- **AND** the test suite passes

#### Scenario: The pin returns in a case, extras or marker variant
- **WHEN** a monitored file declares duckdb with different letter case, with an extras bracket, or followed by an environment marker
- **THEN** the test suite fails, because each variant normalizes to the same project name

#### Scenario: A requirement whose project name cannot be read is reported
- **WHEN** a monitored file declares a bare archive, version-control or local-path requirement that names no project
- **THEN** the test suite fails and names that line
- **AND** the line is not passed over as though it were not duckdb

#### Scenario: The monitored set drifts from the generator's inputs
- **WHEN** the generator references a requirements file the guard does not monitor, or the guard monitors one the generator no longer references
- **THEN** the test suite fails and names the difference
- **AND** the failure is not silenced by dropping the path from the monitored set

#### Scenario: A clean tree passes
- **WHEN** none of the monitored files declares duckdb
- **THEN** the guard passes

### Requirement: The pin guard SHALL run in every environment the gating suite runs in
The guard SHALL be collected and executed wherever the gating test suite runs, and SHALL NOT be disabled by any environment-conditional skip. A guard that skips outside a git checkout reports green while asserting nothing, which is indistinguishable from the regression it exists to catch and is most likely to happen in exactly the environments — an sdist, a container that copied the tree in, a shallow checkout — where no reader would think to question a green run.

The guard SHALL therefore locate the files it inspects by a path resolved relative to the test module itself, not by consulting version control, and SHALL live outside any test module that carries a module-level skip for non-git-checkout environments.

#### Scenario: The guard runs without a version-control checkout
- **WHEN** the test suite is run from a tree that is not a version-control checkout
- **THEN** the guard is collected and executed rather than skipped
- **AND** it reports pass or fail on the actual contents of the monitored files

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
