## ADDED Requirements

### Requirement: The declared Python floor SHALL NOT be below the type-checked target
The project SHALL declare a `requires-python` floor greater than or equal to the interpreter version its static type checker is configured against, so the project never advertises support for an interpreter no tool in the repository has ever checked.

#### Scenario: The declaration contradicts the type-checker target
- **WHEN** `requires-python` in `pyproject.toml` admits an interpreter older than the `pythonVersion` configured for pyright in the same file
- **THEN** the test suite fails
- **AND** the failure message names both values and the file they were read from

#### Scenario: The declaration agrees with the type-checker target
- **WHEN** the `requires-python` floor equals or exceeds the configured `pythonVersion`
- **THEN** the test suite passes

### Requirement: The running interpreter SHALL satisfy the declared floor
The project SHALL keep `requires-python` satisfiable by the interpreter that runs its test suite, so a declaration cannot drift ahead of the environment that is actually exercised.

#### Scenario: The declaration excludes the running interpreter
- **WHEN** `requires-python` is set to a specifier the running interpreter does not satisfy
- **THEN** the test suite fails

#### Scenario: The declaration admits the running interpreter
- **WHEN** the running interpreter satisfies `requires-python`
- **THEN** the test suite passes

### Requirement: The floor SHALL be read as a version specifier rather than matched as text
The guard SHALL parse `requires-python` as a PEP 440 version specifier and compare versions, so a semantically equivalent respelling of the same floor is not reported as a regression.

#### Scenario: A bounded specifier declaring the same floor
- **WHEN** `requires-python` is spelled with an upper bound or a compatible-release operator that still floors at the type-checked version
- **THEN** the guard treats the floor as satisfied and the test suite passes

### Requirement: Documentation SHALL NOT state a superseded Python floor
Contributor-facing documentation SHALL NOT state a minimum Python version lower than the declared `requires-python` floor, so a reader is not told the project supports an interpreter it refuses to install on.

#### Scenario: A documentation page names the old floor
- **WHEN** a documentation page states a Python minimum below the declared floor
- **THEN** that statement is corrected to the declared floor
