## ADDED Requirements

### Requirement: The pin guard SHALL monitor every requirements file the base-image generator reads or writes
The guard SHALL scan the generator's inputs as well as its tracked outputs, and the set of files it monitors SHALL equal the set of requirements files the generator references. `scripts/dev/build_docker_images.sh` concatenates `requirements/cpu-requirementsHEADER.txt` and `requirements/gpu-requirementsHEADER.txt` ahead of `requirements/requirements-base.txt` to produce the two base-image requirements files, so a duckdb declaration added to a header would leave the tracked outputs — and a guard that watched only those outputs — green until the next base-image build regenerated them, which is the moment the package would be installed. The comparison SHALL be exact equality rather than a subset check: if the generator is restructured so that its paths can no longer be discovered, a subset check still passes while coverage silently evaporates, whereas exact equality turns the restructuring into a failure that names the difference. This check is a drift alarm and not a proof of coverage, because a path the generator builds from a variable or a command substitution cannot be discovered by inspection.

#### Scenario: The generator references a requirements file the guard does not scan
- **WHEN** the generator reads or writes a requirements file that the guard's monitored set omits
- **THEN** the test suite fails
- **AND** the failure names the unmonitored file and says to add it to the monitored set

#### Scenario: The guard monitors a file the generator no longer references
- **WHEN** the guard's monitored set contains a requirements file that the generator no longer appears to reference
- **THEN** the test suite fails
- **AND** the failure distinguishes a restructured generator, whose path discovery must be updated, from a genuinely deleted file
- **AND** it states that deleting the path to silence the failure is how coverage is lost

#### Scenario: A duckdb declaration in a generator input is caught
- **WHEN** a generator input header file declares a duckdb dependency while the tracked output files do not
- **THEN** the test suite fails
- **AND** the failure names the header file and the line number

### Requirement: The pin guard SHALL fail closed on a requirement line whose project name it cannot read
The guard SHALL report a monitored requirement line as a failure when the line installs a distribution but declares no project name it can resolve, rather than treating the unresolved line as "not duckdb". pip accepts bare archive, version-control and local-path requirements — a wheel URL, a `git+https://host/duckdb.git#egg=duckdb`, a path into a vendor directory — each of which installs a distribution without naming it in the requirement. A guard that resolves no name from such a line and moves on fails open: the package is installed and the suite stays green, which is indistinguishable from the clean state the guard exists to assert. The monitored files are plain pinned lists, so any other shape is a change a human SHALL approve rather than one the guard passes through silently.

#### Scenario: A bare archive or version-control requirement appears in a monitored file
- **WHEN** a monitored requirements file contains a wheel URL, a `git+` reference, or a local-path requirement
- **THEN** the test suite fails
- **AND** the failure names the file and line and says to pin the dependency by name or to extend the guard

#### Scenario: A named requirement carrying extras or a marker stays readable
- **WHEN** a monitored requirements file declares a dependency by name with extras, an environment marker, a version specifier, or a trailing comment
- **THEN** the guard resolves its project name
- **AND** the fail-closed check does not flag the line

#### Scenario: Comments, blank lines and pip option lines declare no requirement
- **WHEN** a monitored requirements file contains a comment, a blank line, or a pip option line
- **THEN** the guard resolves no project from it
- **AND** the fail-closed check does not flag it
