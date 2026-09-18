## ADDED Requirements

### Requirement: A path or archive requirement SHALL fail the base-image guard closed
The base-image dependency guard SHALL report a requirement line whose first token names a local path or an archive file — a token starting with `.`, a token containing `/` or `\`, or a token ending in `.whl`, `.zip`, `.tgz`, `.tbz2`, `.txz`, `.tar`, `.tar.gz`, `.tar.bz2` or `.tar.xz` — as a requirement whose project name the guard cannot read, so the test suite fails instead of reading the package as absent.
pip installs a local project path and an archive path exactly as it installs a named requirement. Every pairwise guard in this module SKIPS when its subject is absent, so a protected package supplied in either form does not merely go unchecked — it silently disables every guard that names it, and the module reports green while pip installs an unmeasured build. Measured at `4b253e26`: a path line is dropped by the name reader unrecorded, and an archive filename records a nonsense project such as `vllm-0-9-0-py3-none-any-whl` or `dist`. Both readings mean "absent".

#### Scenario: A relative local project path is reported
- **WHEN** a requirement file read by the guard contains `./local_vllm` or `../pkgs/vllm`
- **THEN** the line is reported as a requirement with no readable project name
- **AND** the test suite fails rather than reading vllm as absent

#### Scenario: An absolute path is reported
- **WHEN** a requirement file read by the guard contains `/opt/vllm.whl`
- **THEN** the line is reported as a requirement with no readable project name

#### Scenario: A bare archive filename is reported
- **WHEN** a requirement file read by the guard contains `vllm-0.9.0-py3-none-any.whl` or `dist/vllm-0.9.0.tar.gz`
- **THEN** the line is reported as a requirement with no readable project name
- **AND** the guard does not record a project named after the archive filename

#### Scenario: A Windows-style path is reported
- **WHEN** a requirement file read by the guard contains `.\win_vllm` or `C:\pkgs\vllm`
- **THEN** the line is reported as a requirement with no readable project name
- **AND** it is reported exactly once, even though two patterns match it

#### Scenario: A protected package supplied as a path fails rather than skips
- **WHEN** `./local_vllm` is appended to the generated GPU requirement text
- **THEN** the guard reports a non-empty list of unreadable requirements
- **AND** the pairwise vllm compatibility guards do not report a pass by skipping

#### Scenario: An ordinary requirement is not reported
- **WHEN** a requirement file read by the guard contains an exact pin `vllm==0.9.0`, an extras pin `markitdown[pdf,pptx]==0.1.5`, a range `langgraph-prebuilt<1.0.9`, or a line carrying an environment marker
- **THEN** none of them is reported as a requirement with no readable project name
- **AND** the test suite passes

#### Scenario: A dotted project name is not mistaken for an archive
- **WHEN** a requirement file read by the guard contains `backports.tarfile==1.2.0`, `zope.interface==5.4.0` or `ruamel.yaml==0.18.6`
- **THEN** none of them is reported as a requirement with no readable project name

#### Scenario: The monitored files stay green
- **WHEN** the guard reads the five monitored requirement files of the clean tree
- **THEN** it reports no requirement with an unreadable project name
- **AND** the suite passes without any guard being skipped by this rule

### Requirement: The guard's unreadable-requirement docstring SHALL state the widened class
The docstring of the guard's unreadable-requirement check SHALL name every shape it reports — VCS reference, URL, `file://`, Windows drive letter, local path and archive file — and SHALL carry the date the class was measured.
A docstring that claims a broader class than the code delivers is how this hole survived review: the existing text claimed it reported every line *"whose project name this module cannot read"* while it matched only VCS and URL forms. The documentation must make the guard's boundary auditable at a glance, and it must state that the check reports a line rather than resolving a project name from it.

#### Scenario: The docstring matches the code
- **WHEN** the unreadable-requirement check's docstring is read
- **THEN** it names the VCS, URL, `file://`, drive-letter, local-path and archive shapes
- **AND** it states that a matching line is reported rather than parsed into a project name
- **AND** it carries the date the widened class was measured
