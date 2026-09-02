# Markdown Structural Chunking Specification (delta)

## ADDED Requirements

### Requirement: Header hierarchy in chunk metadata
Under `data_manager.chunking.strategy: markdown`, ingestion SHALL record each chunk's Markdown header hierarchy in a `header_path` metadata key on the parent node and on every child chunk derived from it.

#### Scenario: Nested headers produce the ancestor path

- **WHEN** a Markdown document with nested headers (`#`, `##`, `###`) is ingested under the markdown strategy
- **THEN** each section's parent node and child chunks carry a `header_path` metadata value that names the section's ancestor headers in order

#### Scenario: Preamble and top-level sections always carry the key

- **WHEN** chunk text sits before the first header, or directly under a top-level (`#`) header
- **THEN** its metadata carries `header_path` with the root value `/` — the key is never absent under the markdown strategy

#### Scenario: Document metadata is preserved alongside

- **WHEN** `header_path` is added to a chunk's metadata
- **THEN** the document-level metadata keys (url, title, filename, and the rest) remain present and unchanged

### Requirement: Section size cap
A header section whose text exceeds the configured `parent_chunk_size` SHALL split into multiple parent nodes, each at most `parent_chunk_size` tokens as persisted (a single fenced code block that alone exceeds the budget excepted); every resulting parent SHALL carry the section's `header_path`, and the pieces SHALL NOT overlap in content.

#### Scenario: Oversized section yields multiple parents

- **WHEN** one header section is longer than `parent_chunk_size`
- **THEN** ingestion produces more than one parent node for that section, all with the same `header_path`, with no content repeated between the pieces

#### Scenario: Packed pieces respect the budget

- **WHEN** the pieces of an oversized section pack back into parents
- **THEN** each packed parent's token count, the separators written between pieces included, is at most `parent_chunk_size`

#### Scenario: Packed parents are verbatim slices of the section

- **WHEN** several pieces of an oversized section pack into one parent
- **THEN** the parent text is the section's source text from the first piece's start to the last piece's end, with nothing inserted between the pieces (a URL or hash the splitter cut mid-token comes back intact)

#### Scenario: A fence larger than the budget stays whole

- **WHEN** a fenced code block, delimited by ``` or by `~~~`, alone exceeds `parent_chunk_size`
- **THEN** it becomes one oversized parent on its own instead of a bisected fence

#### Scenario: Only the opening marker closes a fence

- **WHEN** a `~~~` fenced block contains a line that starts with ``` (or the reverse)
- **THEN** that line does not end the fence; the block stays whole under the cap

#### Scenario: Section within the cap stays whole

- **WHEN** a header section fits within `parent_chunk_size`
- **THEN** it becomes exactly one parent node

### Requirement: Structural fidelity for fences and empty headings
The markdown strategy SHALL NOT treat a `#` line inside a fenced code block as a section start, and SHALL process a document with empty headings (a heading marker with no text) without error.

#### Scenario: Fenced code does not split sections

- **WHEN** a Markdown document contains a ``` fenced block whose lines start with `#`
- **THEN** those lines stay inside their section and start no new section

#### Scenario: Empty heading is tolerated

- **WHEN** a document contains an empty heading such as `### ` (marker with no text)
- **THEN** ingestion completes and produces sections without error

### Requirement: Per-file dispatch
With `strategy: markdown`, ingestion SHALL apply the markdown parser only to Markdown files and SHALL chunk every other file with the `sentence` strategy. A file counts as Markdown when its recorded suffix or filename extension, lowercased and with any leading dot removed, equals `md` or `markdown`.

#### Scenario: Markdown file takes the markdown parser

- **WHEN** a file with suffix `md` (or `.md`, `MD`, `markdown`) is ingested under `strategy: markdown`
- **THEN** its chunks come from the header-aware markdown parse

#### Scenario: A `.markdown` file loads

- **WHEN** a file with suffix `markdown` is ingested
- **THEN** the text loader accepts it, so it reaches the markdown dispatch instead of failing as an unsupported format

#### Scenario: Non-Markdown file falls back to sentence

- **WHEN** a `.py`, `.txt`, or PDF-derived file is ingested under `strategy: markdown`
- **THEN** its chunks come from the sentence strategy, identical to `strategy: sentence` output for that file

### Requirement: Opt-in only
The markdown strategy SHALL remain opt-in. The shipped default for `data_manager.chunking.strategy` SHALL stay `sentence`, and completion of the markdown strategy SHALL NOT change the chunk output of the `sentence` or `character` strategies.

#### Scenario: Default deployment is unaffected

- **WHEN** a deployment does not set `chunking.strategy` (or the template renders the default)
- **THEN** ingestion chunks with the `sentence` strategy, byte-identical to its behavior before this change
