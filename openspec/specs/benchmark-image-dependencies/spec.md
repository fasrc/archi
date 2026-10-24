# benchmark-image-dependencies Specification

## Purpose
Keep what the benchmark image installs beyond the archi package pinned and reproducible. Its benchmark-only pip step runs against an exact-pin constraints file inside the staged build context, and a static guard checks that file against the base image's pins, including the measured overrides of project pins.
## Requirements
### Requirement: Benchmark-only pip steps are constrained
Each benchmark Dockerfile template SHALL pass `-c src/cli/templates/dockerfiles/benchmark-constraints.txt` on every benchmark-only `pip install`.
The templates are `src/cli/templates/dockerfiles/Dockerfile-benchmarks` and `Dockerfile-benchmarks-gpu`;
a benchmark-only step is any `pip install` that runs after `pip install .`. The `pip install .` step itself SHALL
NOT be constrained, so the archi package keeps resolving its own declared dependencies. The
guard SHALL fail closed on a `RUN` it cannot read: any heredoc `RUN`, an exec-form `RUN` that
mentions `pip`, or unbalanced shell quoting.

#### Scenario: An unconstrained benchmark step is rejected
- **WHEN** a benchmark Dockerfile runs `pip install 'argilla>=2.5,<3'` after `pip install .`
  without `-c src/cli/templates/dockerfiles/benchmark-constraints.txt`
- **THEN** the guard fails and names the Dockerfile and the unconstrained step

#### Scenario: Both templates are covered
- **WHEN** the guard runs
- **THEN** it checks both `Dockerfile-benchmarks` and `Dockerfile-benchmarks-gpu`, and fails if
  either file is missing or has no `pip install .` step

### Requirement: The constraints file holds only exact pins inside the build context
`src/cli/templates/dockerfiles/benchmark-constraints.txt` SHALL contain only comments, blank
lines and exact `name==version` requirements, read the way pip reads them. A range, an unpinned
name, a URL or path requirement, an environment variable, or an include directive (`-r`, `-c`,
`-e`) SHALL fail the guard. Each name SHALL appear on one line only, with no environment
marker and no extras: pip combines duplicate constraints, so two versions abort the build; a
marker makes the pin conditional; and pip rejects extras in a constraints file. The file SHALL
live under `src/`, because the deployment build context receives only `src/` (as
`archi_code/`), `pyproject.toml` and `LICENSE`.

#### Scenario: A range in the constraints file is rejected
- **WHEN** the constraints file contains `datasets>=5.0`
- **THEN** the guard fails and names the line

#### Scenario: An include directive is rejected
- **WHEN** the constraints file contains `-r other.txt`
- **THEN** the guard fails instead of skipping the line

#### Scenario: A duplicated name is rejected
- **WHEN** the constraints file pins `pyarrow==25.0.1` and also `pyarrow==26.0.0`
- **THEN** the guard fails and names `pyarrow` as a duplicate, even though both readers keep
  only one value per name

### Requirement: The known breakage points are pinned
The constraints file SHALL pin `datasets`, `huggingface-hub`, `pyarrow`, `instructor`, `ragas`
and `argilla`, matching names by PEP 503 normalization so that `huggingface_hub` and
`huggingface-hub` are one name.

#### Scenario: Removing a breakage pin is rejected
- **WHEN** the `pyarrow` line is deleted from the constraints file
- **THEN** the guard fails and names `pyarrow` as missing

### Requirement: Constraints stay compatible with the base image's pins
The guard SHALL check the constraints against compatibility facts measured on 2026-10-11, each
keyed by the base pin it depends on:
- while `requirements/requirements-base.txt` pins `transformers==4.53.3`, the pinned
  `huggingface-hub` SHALL satisfy `<1.0` (transformers 4.53.3 requires
  `huggingface-hub<1.0,>=0.30.0`);
- while it pins `numpy` below 2, the pinned `pyarrow` SHALL be below 26 (pyarrow 26.0.0 fails
  at import against numpy 1.26.4, and declares no NumPy floor a resolver could check).

When a keying base pin moves to a version the table has not measured, the guard SHALL fail and
ask for a re-measurement instead of passing.

#### Scenario: An incompatible huggingface-hub is rejected
- **WHEN** the constraints file pins `huggingface-hub==2.2.0` while the base pins
  `transformers==4.53.3`
- **THEN** the guard fails and cites the measured `<1.0` bound

#### Scenario: An unmeasured base pin fails closed
- **WHEN** `requirements-base.txt` moves `transformers` to a version absent from the
  measured table
- **THEN** the guard fails and asks for the compatibility fact to be re-measured

### Requirement: Overrides of project pins are recorded
The guard SHALL require an override-table entry for every constraint that differs from a project pin.
Any package pinned in the constraints file at a version different from its exact pin in
`requirements/requirements-base.txt` or `pyproject.toml` SHALL appear in the guard's override
table, together with the measured cause of the override. The guard SHALL fail on a divergence
the table does not list, and on a table entry that no longer diverges.

#### Scenario: An unlisted override is rejected
- **WHEN** the constraints file pins `aiohttp==3.14.3`, `requirements-base.txt` pins
  `aiohttp==3.9.5`, and the override table has no `aiohttp` entry
- **THEN** the guard fails and names `aiohttp` with both versions

#### Scenario: A stale override entry is rejected
- **WHEN** the override table lists a package whose constraint now equals its project pin, or
  which is no longer in the constraints file
- **THEN** the guard fails and names the stale entry

### Requirement: The constraints file covers everything the benchmark steps move
The constraints file SHALL pin every package the benchmark-only steps install or change.
For a no-cache build of the CPU benchmark image, every package whose installed version differs
between the image after `pip install .` and the final image SHALL be pinned in the constraints
file. This property needs a real build, so it SHALL be verified on every change to the
constraints file or the benchmark pip steps, and the procedure SHALL be documented in the
constraints file's header.

#### Scenario: Lock completeness after a build
- **WHEN** the CPU image is built with no cache from the CLI's staging layout, and the
  `pip freeze` of the stage after `pip install .` is diffed against the final image's
- **THEN** every package that differs appears in the constraints file

### Requirement: A freshly built benchmark image starts
A no-cache build of the CPU benchmark image SHALL produce a container in which
`sentence_transformers`, `datasets`, `pyarrow`, `ragas`, `transformers` and
`langchain_huggingface.HuggingFaceEmbeddings` import. `pip check` SHALL report no conflict
involving `huggingface-hub`, `datasets`, `pyarrow`, `tokenizers` or `transformers`.

#### Scenario: Import smoke on a no-cache build
- **WHEN** the CPU benchmark image is built with `--no-cache` and the import smoke runs in it
- **THEN** every import succeeds, and `pip check` names none of those five packages

