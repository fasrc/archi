## ADDED Requirements

### Requirement: Pip install directives SHALL fail the guard closed
The guard SHALL treat pip install directives in a monitored requirements file — editable (`-e`/`--editable`), requirement (`-r`/`--requirement`) and constraint (`-c`/`--constraint`) lines, in space-separated, `=`-attached and attached-value forms — as unreadable-requirement shapes that fail the test suite, rather than skipping them as option lines.
These directives pull installable content into pip's resolution without naming a project the guard can read, so skipping them is the same fail-open hole as waving through a bare VCS URL. Requirement and constraint includes are reported rather than followed recursively; no monitored file uses one today, and the failure message tells a maintainer how to proceed.

#### Scenario: An editable VCS directive with an egg fragment is flagged
- **WHEN** a monitored requirements file contains `-e git+https://host/duckdb.git#egg=duckdb`
- **THEN** the line is reported as an unreadable requirement shape
- **AND** the test suite fails naming the offending file and line

#### Scenario: An editable local path is flagged
- **WHEN** a monitored requirements file contains `--editable ./vendor/duckdb` or `-e ./vendor/duckdb`
- **THEN** the line is reported as an unreadable requirement shape
- **AND** the test suite fails

#### Scenario: A recursive requirements include is flagged, not followed
- **WHEN** a monitored requirements file contains `-r extra-requirements.txt` or `--requirement=extra-requirements.txt`
- **THEN** the line is reported as an unreadable requirement shape rather than being silently skipped
- **AND** the include target is not itself opened or scanned

#### Scenario: A constraint include is flagged
- **WHEN** a monitored requirements file contains `-c constraints.txt` or `--constraint=constraints.txt`
- **THEN** the line is reported as an unreadable requirement shape

#### Scenario: Inert options still pass unflagged
- **WHEN** a monitored requirements file contains only inert option lines such as `--extra-index-url https://pypi.org/simple`, `-i https://pypi.org/simple`, `--find-links wheels/`, `--trusted-host pypi.org` or `--hash=sha256:abc`
- **THEN** none of them is reported as an unreadable requirement shape
- **AND** the test suite passes

#### Scenario: A directive that names no duckdb still fails closed
- **WHEN** a monitored requirements file contains `-r extra-requirements.txt` whose target never mentions duckdb
- **THEN** the guard still fails, because it cannot prove the include is duckdb-free without reading it
- **AND** the failure demands a human look rather than passing as "not duckdb"

### Requirement: The guard's docstrings SHALL state its actual coverage
The guard module's docstring and the docstring of its unreadable-shape check SHALL state which line shapes the guard reads, which option lines it skips as inert, and that requirement and constraint includes are reported rather than followed recursively.
A docstring claiming broader coverage than the code delivers is how the last hole survived review; the documentation must make the guard's boundary auditable at a glance.

#### Scenario: No overbroad coverage claim
- **WHEN** the module docstring and the unreadable-shape check's docstring are read
- **THEN** they describe the inert-option/install-directive split and the not-followed rule for includes
- **AND** no "every shape" claim exceeds what the code delivers
