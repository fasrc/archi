## ADDED Requirements

### Requirement: Embed-stage loader handles Jupyter notebooks without output blobs

The system SHALL provide a document loader for `.ipynb` (Jupyter notebook) files
collected by git, so notebooks the operator allow-listed in
`data_manager.sources.git.code_suffixes` are embedded rather than rejected at embed time
as an unsupported format.

Notebook loading SHALL extract **cell source and markdown content only**. Execution
outputs — stream text, `execute_result` payloads, base64 image blobs, and error
tracebacks — MUST NOT appear in the loaded `page_content`, because embedding them
pollutes retrieval with non-semantic content. This is the "clean extraction" bar that
decision D3 of `index-git-code-examples` set when it deferred `.ipynb` from that change.

Suffix matching SHALL be case-insensitive, consistent with the other loader branches.

#### Scenario: Notebook files are loadable

- **WHEN** `select_loader()` is called for a file named `analysis.ipynb`
- **THEN** it returns a notebook-capable loader (not `None`), so the file is embedded
  rather than marked `failed` with `"Unsupported file format"`

#### Scenario: Cell source and markdown are extracted

- **WHEN** a notebook containing a markdown cell and a code cell is loaded via
  `select_loader(...).load()`
- **THEN** the resulting `page_content` contains both the markdown text and the code
  cell's source

#### Scenario: Execution outputs are excluded

- **WHEN** a notebook whose code cell carries a recorded stdout output blob is loaded via
  `select_loader(...).load()`
- **THEN** the resulting `page_content` does **not** contain that output blob's text,
  while still containing the cell's source

#### Scenario: Notebook suffix participates in the collection/loader parity guard

- **WHEN** the drift-guard test enumerates every suffix git collection accepts
- **THEN** `.ipynb` is among them and `select_loader()` returns a loader (not `None`) for
  it, so collection and loading cannot silently disagree about notebooks
