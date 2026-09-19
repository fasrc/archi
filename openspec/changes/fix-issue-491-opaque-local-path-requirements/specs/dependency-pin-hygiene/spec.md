## ADDED Requirements

### Requirement: A path or archive requirement SHALL fail the base-image guard closed
The base-image dependency guard SHALL report a requirement line that names a local path or an archive file — a line starting with `.`, a line containing `/` or `\` anywhere, or a line whose archive suffix carries every extension pip accepts (`.whl`, `.zip`, `.tgz`, `.tbz`, `.tbz2`, `.txz`, `.tlz`, `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.lz`, `.tar.lzma`) — as a requirement whose project name the guard cannot read, so the test suite fails instead of reading the package as absent.
pip installs a local project path and an archive path exactly as it installs a named requirement. Every pairwise guard in this module SKIPS when its subject is absent, so a protected package supplied in either form does not merely go unchecked — it silently disables every guard that names it, and the module reports green while pip installs an unmeasured build. Measured at `4b253e26`: a path line is dropped by the name reader unrecorded, and an archive filename records a nonsense project such as `vllm-0-9-0-py3-none-any-whl` or `dist`. Both readings mean "absent". Widened on 2026-09-19 after review: the suffix list was short of pip's own `ARCHIVE_EXTENSIONS`, and each clause stopped at the first space, so `vllm-0.9.0.tlz` and `vendor packages/vllm` both read as ordinary project names.

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

#### Scenario: Every archive suffix pip accepts is reported
- **WHEN** a requirement file read by the guard contains `vllm-0.9.0.tlz`, `vllm-0.9.0.tar.lz` or `vllm-0.9.0.tar.lzma`
- **THEN** each line is reported as a requirement with no readable project name
- **AND** the suffix set the guard reads matches pip's own `ARCHIVE_EXTENSIONS`

#### Scenario: A path carrying whitespace before its separator is reported
- **WHEN** a requirement file read by the guard contains `vendor packages/vllm`, `vendor packages/vllm-0.9.0.tar.gz` or `my package.tar.gz`
- **THEN** each line is reported as a requirement with no readable project name
- **AND** the guard does not record a project named `vendor` or `my`

#### Scenario: A dotted project name is not mistaken for an archive
- **WHEN** a requirement file read by the guard contains `backports.tarfile==1.2.0`, `zope.interface==5.4.0` or `ruamel.yaml==0.18.6`
- **THEN** none of them is reported as a requirement with no readable project name

#### Scenario: A spaced comparison operator does not make a project name an archive
- **WHEN** a requirement file read by the guard contains `example.zip ==1.0` or `example.tar >=1`
- **THEN** neither line is reported as a requirement with no readable project name
- **AND** the verdict matches the compact spelling `example.zip==1.0`, which is already accepted

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

### Requirement: A named direct reference SHALL stay readable only when its target carries a URL scheme
The base-image dependency guard SHALL NOT report `name@url` or `name @ url` — a PEP 508 direct reference whose target starts with `git+`, `hg+`, `bzr+`, `svn+`, `http://`, `https://` or `file://` — and SHALL report a direct reference whose target carries no scheme, such as `evil@../pkgs/vllm`.
The name reader reads `name` from both spellings, so design D4 leaves the class readable and `_unpinned_protected` turns a protected name into a failure. Measured on 2026-09-19: the separator inside the URL made the compact spelling opaque while the spaced spelling passed, so whitespace alone decided the verdict on the same requirement. A target with no scheme is a local path wearing a project name, which is the silent-skip shape this change exists to close, so it stays reported.

#### Scenario: A named URL reference is readable in both spellings
- **WHEN** a requirement file read by the guard contains `numpy@https://host/numpy-2.0.0-py3-none-any.whl` or the same line with spaces around `@`
- **THEN** neither line is reported as a requirement with no readable project name
- **AND** the guard records the project name `numpy`

#### Scenario: A protected package supplied as a direct reference fails closed
- **WHEN** `vllm@https://host/vllm-0.9.0-py3-none-any.whl` is read by the guard
- **THEN** the unpinned-protected check reports `vllm`
- **AND** the suite fails rather than reading vllm as absent

#### Scenario: A named reference to a bare path is reported
- **WHEN** a requirement file read by the guard contains `evil@../pkgs/vllm`, `evil@/opt/vllm` or `evil@C:\pkgs\vllm`
- **THEN** each line is reported as a requirement with no readable project name
