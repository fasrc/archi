## ADDED Requirements

### Requirement: Embed-stage loader covers configured code file types

The system SHALL provide a document loader for every plain-text code/script file
type that git collection is configured to accept, so a file the operator allow-listed
for collection is never rejected at embed time as an unsupported format.

This closes the drift between the collection allow-list
(`data_manager.sources.git.code_suffixes`) and the loader's supported set: any
plain-text suffix in the former MUST be loadable by the latter. Coverage comprises
two groups, both case-insensitive:

- **Shipped default** — every suffix in `GitScraper`'s default `code_suffixes`:
  `.js`, `.ts`, `.tsx`, `.jsx`, `.java`, `.go`, `.rs`, `.sql`, `.cpp`, `.hpp`,
  `.toml` (the remainder — `.py .c .h .sh .json .yaml .yml .md .txt` — are already
  covered).
- **FASRC HPC additions** — the scientific-computing suffixes the deployment
  configures on top of the default: `.sbatch`, `.slurm`, `.f90`, `.f`, `.f95`,
  `.cu`, `.r`, `.rmd`, `.m`, `.jl`, `.def`.

#### Scenario: Slurm job script is loadable

- **WHEN** `select_loader()` is called for a file named `submit.sbatch`
- **THEN** it returns a text-capable loader (not `None`), so the file is embedded
  rather than marked `failed` with `"Unsupported file format"`

#### Scenario: Fortran, C++, and CUDA sources are loadable

- **WHEN** `select_loader()` is called for `solver.f90`, `kernel.cpp`, or `matmul.cu`
- **THEN** each returns a text-capable loader (not `None`)

#### Scenario: Every shipped-default code suffix is loadable

- **WHEN** `select_loader()` is called for any suffix in `GitScraper`'s default
  `code_suffixes` (e.g. `app.js`, `main.go`, `query.sql`, `lib.rs`, `mod.ts`)
- **THEN** each returns a text-capable loader (not `None`), so the collection default
  and the loader cannot silently disagree

#### Scenario: Suffix matching is case-insensitive

- **WHEN** `select_loader()` is called for `SOLVER.F90` (uppercase suffix)
- **THEN** it returns the same text-capable loader it returns for `solver.f90`

#### Scenario: Unsupported binary types still return None

- **WHEN** `select_loader()` is called for a genuinely unsupported type such as
  `figure.png`
- **THEN** it returns `None`, preserving the existing guard for file types that
  cannot be loaded as text

#### Scenario: A collected code file completes ingest end to end

- **WHEN** a git-collected `.sbatch` file is processed by `_add_to_postgres`
- **THEN** its `loader` is non-`None`, its content is embedded, and its
  `ingestion_status` becomes `embedded` rather than `failed`
