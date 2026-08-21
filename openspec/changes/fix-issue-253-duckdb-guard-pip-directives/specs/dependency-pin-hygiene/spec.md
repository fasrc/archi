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

### Requirement: Abbreviated long install directives SHALL fail the guard closed
The guard SHALL treat any prefix of `--editable`, `--requirement` or `--constraint` as the directive it abbreviates.
pip's option parser resolves an unambiguous long-option abbreviation, so `--edit git+https://host/duckdb.git` installs exactly what `--editable` installs. Matching only the full spelling leaves every abbreviation fail-open. A prefix that pip itself rejects as ambiguous is also flagged: failing closed on a line pip refuses to parse is correct for a guard.

#### Scenario: An abbreviated editable directive is flagged
- **WHEN** a monitored requirements file contains `--edit git+https://host/duckdb.git#egg=duckdb` or `--editabl=git+https://host/duckdb.git`
- **THEN** the line is reported as an unreadable requirement shape

#### Scenario: An abbreviated include is flagged
- **WHEN** a monitored requirements file contains `--requirem extra-requirements.txt` or `--constrain constraints.txt`
- **THEN** the line is reported as an unreadable requirement shape

#### Scenario: An inert option that shares a prefix with a directive stays inert
- **WHEN** a monitored requirements file contains `--require-hashes`, `--extra-index-url https://pypi.org/simple`, `--cert /etc/ssl/ca.pem` or `--client-cert /etc/ssl/client.pem`
- **THEN** none of them is reported as an unreadable requirement shape

### Requirement: An option name built from an environment variable SHALL fail the guard closed
The guard SHALL treat a hyphen-led line whose option name contains a `${...}` reference as an unreadable requirement shape.
pip expands environment variables before it parses a line, so `-${DIRECTIVE} extra.txt` is a live include in any build that sets `DIRECTIVE=r`. The guard cannot read the environment of every future image build, so it reports the shape rather than guessing the value. Only the option name decides this; a variable inside an option's value is ordinary.

#### Scenario: A variable-bearing option name is flagged
- **WHEN** a monitored requirements file contains `-${DIRECTIVE} extra-requirements.txt` or `--${DIRECTIVE} git+https://host/duckdb.git`
- **THEN** the line is reported as an unreadable requirement shape

#### Scenario: A variable inside an inert option's value is not flagged
- **WHEN** a monitored requirements file contains `--extra-index-url https://${TOKEN}@pypi.example.invalid/simple`
- **THEN** the line is not reported as an unreadable requirement shape

### Requirement: The guard SHALL join backslash continuations before reading a line
The guard SHALL join a physical line ending in a backslash onto the next one before it classifies the line, as pip does, and SHALL report the joined line at the number of its first physical line.
Read physically, `--edit\` looks like an inert option and the following `able git+https://host/duckdb.git` parses as a requirement named `able`, so both the duckdb name check and the fail-closed check pass while pip installs duckdb. A comment line ends a join, matching pip.

#### Scenario: A directive split across a continuation is flagged
- **WHEN** a monitored requirements file contains `--edit\` on one line and `able git+https://host/duckdb.git#egg=duckdb` on the next
- **THEN** the joined line is reported as an unreadable requirement shape
- **AND** the failure names the first of the two physical line numbers

#### Scenario: A project name split across a continuation is still read
- **WHEN** a monitored requirements file contains `duck\` on one line and `db==1.0` on the next
- **THEN** the guard resolves the project name `duckdb`
- **AND** the duckdb pin check fails
