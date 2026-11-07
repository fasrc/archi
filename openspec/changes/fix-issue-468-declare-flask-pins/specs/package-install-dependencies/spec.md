## ADDED Requirements

### Requirement: The package metadata SHALL declare the flask pins that the src.utils import chain needs
The `[project].dependencies` list in `pyproject.toml` SHALL contain `flask`, `flask-login`, `flask-session`, and `flask-cors`, so an environment made with `pip install -e .` can import `src.utils`.

#### Scenario: A flask pin is missing from the package metadata
- **WHEN** one of `flask`, `flask-login`, `flask-session`, or `flask-cors` is absent from `[project].dependencies` in `pyproject.toml`
- **THEN** the unit test suite fails
- **AND** the failure message names the missing package

#### Scenario: All four flask pins are declared
- **WHEN** `[project].dependencies` contains all four packages
- **THEN** that check passes

### Requirement: Each declared flask pin SHALL equal the pin in requirements-base.txt
Each of the four packages SHALL be pinned in `pyproject.toml` with one `==` clause whose version equals the `==` pin for the same package in `requirements/requirements-base.txt`, read from that file at test time.

#### Scenario: The versions drift apart
- **WHEN** the `pyproject.toml` version of a flask package differs from its `requirements-base.txt` pin
- **THEN** the unit test suite fails
- **AND** the failure message names the package and both versions

#### Scenario: The base file does not pin the package exactly once
- **WHEN** `requirements-base.txt` has no `==` pin for one of the four packages, or has more than one
- **THEN** the unit test suite fails rather than skips

#### Scenario: Names differ only in spelling
- **WHEN** a package name differs between the files only in case or in `-`, `_`, or `.`
- **THEN** the names are compared after PEP 503 normalization and treated as the same package
