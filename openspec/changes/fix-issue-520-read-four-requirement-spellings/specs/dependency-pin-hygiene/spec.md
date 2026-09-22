## ADDED Requirements

### Requirement: The base-image guard SHALL read a comment, a detached extras list, a continued marker and pip's short option as pip reads them
The base-image dependency guard SHALL NOT continue a physical line that pip's comment rule matches, SHALL read an environment marker that a backslash continues onto the next physical line, SHALL cut pip's short `-C` per-requirement option in both its attached and detached spellings, and SHALL NOT report an archive-looking project name that carries a detached extras list in front of a comparison.
Every guard in this module SKIPS when its subject is absent, so a misread line is a silent skip. Two of these four spellings hide a protected package that way: a comment line ending in `\` swallowed the wheel that follows it, and a marker on the next physical line made a conditional pin read as unconditional. The other two failed legitimate work: a detached extras list made a legal project name read as an opaque archive, and pip's `-C` option defeated the anchored pin pattern so an exact pin read as unpinned. Measured on 2026-09-22 at `376b5867` against pip 26.1.2, from four P2 findings posted on merged PR #506 (threads 4058046837, 4058046838, 4058046843 and 4058046844) 93 seconds after its merge, which no review round read. `COMMENT_RE` is `(^|\s+)#.*$`, so only a whole-line comment stops a continuation; `SUPPORTED_OPTIONS_REQ` carries exactly `--hash` and `-C`/`--config-settings`, so `-C` is pip's only short per-requirement option.

#### Scenario: A comment line ending in a backslash does not swallow the requirement after it
- **WHEN** a requirement file read by the guard contains `# comment \` followed by `vllm-0.9.0-py3-none-any.whl`
- **THEN** the wheel is reported as a requirement with no readable project name
- **AND** the comment line is yielded on its own and never opens a continuation
- **AND** an indented comment ending in a backslash behaves the same way

#### Scenario: A comment arriving inside an open continuation is appended and flushed
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0 \`, then `# note \`, then `numpy==2.0.0`
- **THEN** the guard reads the exact pin `vllm==0.9.0` and the exact pin `numpy==2.0.0`
- **AND** the comment tail is cut by pip's comment rule rather than read as a requirement
- **AND** the behaviour matches pip's own `join_lines`, which appends the comment to the open buffer and flushes it as one logical line

#### Scenario: An inline comment ending in a backslash still continues
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0  # note \` followed by `numpy==2.0.0`
- **THEN** the two physical lines are joined into one logical line, as pip joins them
- **AND** the guard keys its decision on pip's comment rule rather than on the presence of a `#` anywhere in the line

#### Scenario: An environment marker continued onto the next line is read
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0 \` followed by `; python_version < "3.11"`
- **THEN** the conditional-pin check reports vllm with the marker `python_version < "3.11"`
- **AND** the pin reader still records the exact pin `0.9.0`
- **AND** the test suite fails rather than reading the pin as unconditional

#### Scenario: pip's short config-settings option is cut from a requirement
- **WHEN** a requirement file read by the guard contains `vllm==0.9.0 -Cfoo=bar`, `vllm==0.9.0 -C foo=bar`, or the same option continued onto the next physical line
- **THEN** the pin reader records the exact pin `0.9.0` for each spelling
- **AND** the unpinned-protected check reports nothing
- **AND** the long spelling `--config-settings`, which already passed, keeps passing

#### Scenario: A bare trailing short option is not cut as an option
- **WHEN** a requirement line ends in a bare `-C` with no value after it
- **THEN** the option pattern does not cut it, because pip's option takes a value
- **AND** no project name is cut by the short-option branch, because that branch requires leading whitespace

#### Scenario: A detached extras list in front of a comparison is not an archive
- **WHEN** a requirement file read by the guard contains `example.zip [foo] ==1.0` or `example.tar [foo] (>=1)`
- **THEN** neither line is reported as a requirement with no readable project name
- **AND** the verdict matches the attached spelling `example.zip[foo] ==1.0`, which is already accepted
- **AND** pip reads the project name `example.zip` with the extras `foo` from the same line

#### Scenario: An archive with no comparison after its extras list stays reported
- **WHEN** a requirement file read by the guard contains `example.zip [foo]` or `example.zip`
- **THEN** each line is reported as a requirement with no readable project name
- **AND** the widened pattern has not made the guard blind to a bare archive

#### Scenario: The monitored files stay green
- **WHEN** the guard reads the five monitored requirement files of the clean tree
- **THEN** it reports no requirement with an unreadable project name, no unpinned protected package, and no conditional protected package
- **AND** the suite passes without any guard being skipped by these four rules

### Requirement: The guard's logical-line reader SHALL be the single source of logical lines for every reader that classifies requirements
Every reader in the base-image guard that classifies a requirement line — the pin reader, the requirement reader, the unreadable-requirement reader and the conditional-pin reader — SHALL take its lines from the module's logical-line reader, so that one physical-to-logical rule decides what all of them see.
The conditional-pin reader walked the raw physical lines while its siblings walked the joined ones, and that split is what let a continued marker read as unconditional. A reporter-side repair would have to re-split a joined line, which is the same parser written twice, and it would leave the other readers on the wrong lines. Fixing the shared seam repairs every reader at once, and it keeps the module honest about having exactly one continuation rule.

#### Scenario: The conditional-pin reader reads joined lines
- **WHEN** the conditional-pin reader is given text whose marker is continued onto the next physical line
- **THEN** it reads the marker from the joined logical line
- **AND** it still skips blank lines, whole-line comments and option lines as before

#### Scenario: One continuation rule decides every verdict
- **WHEN** the comment rule changes which physical lines become one logical line
- **THEN** the pin reader, the requirement reader, the unreadable-requirement reader and the conditional-pin reader all change verdict together
- **AND** no reader carries a second continuation rule of its own
