## ADDED Requirements

### Requirement: The source-root check refuses an unreadable root and says whether a path is missing or unreadable
The base-image preflight SHALL refuse a source root when any of `src`, `pyproject.toml` or `LICENSE` is missing or cannot be read by the current user, or when any file or directory under `src` cannot be read, before any teardown. A directory counts as readable only when the user can both list it and enter it. The refusal text SHALL name the missing paths on a line of the form "is missing {names}" and the unreadable paths on a line of the form "has unreadable {names}", and SHALL keep the existing teardown explanation and remedy text.

#### Scenario: A root with no src directory
- **WHEN** the preflight checks a recorded checkout that holds `pyproject.toml` and `LICENSE` but no `src`
- **THEN** it raises `BaseImagePreflightError`
- **AND** the message contains "is missing src"

#### Scenario: A root whose src directory cannot be read
- **WHEN** the preflight checks a recorded checkout whose `src` directory exists but has mode `0o000`, as a non-root user
- **THEN** it raises `BaseImagePreflightError`
- **AND** the message contains "unreadable src"
- **AND** the message does not contain "missing"

#### Scenario: An unreadable file inside src
- **WHEN** the preflight checks a recorded checkout in which one file under `src` has mode `0o000`, as a non-root user
- **THEN** it raises `BaseImagePreflightError`
- **AND** the message names that file by its path relative to the checkout

#### Scenario: A missing path and an unreadable path together
- **WHEN** the preflight checks a recorded checkout with no `LICENSE` and an unreadable `pyproject.toml`
- **THEN** the message contains "is missing LICENSE"
- **AND** the message contains "has unreadable pyproject.toml"

#### Scenario: A healthy root
- **WHEN** the preflight checks a recorded checkout in which every copied path exists and can be read
- **THEN** it does not raise, and `build_source_root()` returns that checkout as before
