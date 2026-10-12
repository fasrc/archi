## Context

The benchmark image is the CLI's `benchmark` service. `archi evaluate` builds it from the
deployment directory using `src/cli/templates/dockerfiles/Dockerfile-benchmarks`, or the
`-gpu` twin. That directory receives only `src/` (as `archi_code/`), `pyproject.toml` and
`LICENSE` (`src/cli/managers/templates_manager.py:1330-1334`), plus the rendered configs.

On top of `ghcr.io/fasrc/a2rchi-python-base:v2026.08.0`, which is built from
`requirements/requirements-base.txt`, the Dockerfile runs:

```dockerfile
RUN pip install --upgrade pip && pip install .     # :31
RUN pip install pandas                             # :32
RUN pip install ragas==0.3.5                       # :33
RUN pip install langchain-openai==1.0.1            # :34
RUN pip install 'argilla>=2.5,<3'                  # :35
```

Every call after `:31` floats its transitive closure. pip also does not enforce upper bounds
held by packages outside the call's resolution set. The 2026-10-11 breakage (proposal) is two
instances of that: `datasets` 5.1.0 dragged `huggingface_hub` past `transformers`' `<1.0`, and
`pyarrow` 26 needs NumPy 2.

The repo already has a tiered approach to dependency guards. It is documented in the module
docstring of `tests/unit/test_base_image_dependency_compatibility.py`:
1. Static nets catch known pairwise traps.
2. A `pip --dry-run` resolve catches unpredicted version conflicts.
3. Only a real image build catches import-time collisions.

The `pyarrow`/NumPy failure is a tier-3 fact. That module also carries a pip-faithful
requirement reader (`_requirement_lines`, `_read`, `_parse_pins`, `_parse_requirements`,
`_opaque_requirements`, `_requirement_bearing_directives`; hardened under #527/#530).

Measured inputs for this design (dev host, 2026-10-11):
- **Stage-1 freeze:** the image after `:31`, built today from cache; 304 packages.
- **Known-good final freeze:** `benchmarking-prompt-sweep-r0-atoms:2000`, built 2026-09-29. It
  ran two benchmark replicates and six QA runs; 315 packages.
- **Broken final freeze:** today's `benchmarking-ragas-retrieval-ab:2000`.
- **Measured causes of the overrides:** `instructor 1.17.0` requires `aiohttp>=3.14.3`,
  `click>=8.3.3`, `idna>=3.15`, `jinja2>=3.1.4`, `requests>=2.33.0`, `rich<15` and
  `urllib3>=2.7.0`. `datasets 5.0.1` requires `requests>=2.32.2`, and
  `langchain-community 0.4` requires `requests>=2.32.5`.

## Goals / Non-Goals

**Goals:**
- A no-cache build of the CPU benchmark image starts and imports its embedding stack.
- The benchmark-only steps install the same versions on every build until someone changes
  the constraints file on purpose.
- The divergence between the benchmark image and the project's declared pins becomes
  explicit and checked, instead of an invisible side effect of pip's resolution order.

**Non-Goals:**
- A CI job that builds the benchmark image. That would close the tier-3 gap and is proposed
  as a follow-up.
- Reconciling the benchmark image with production versions (`langchain-openai` 1.0.1 vs
  1.1.10, `requests`, `aiohttp`, and others). This change records the divergence and does
  not resolve it.
- archi's own `click`/`jinja2`/`requests` pins that `pip check` reports as violated.
- Any change to `requirements-base.txt`, the `*HEADER.txt` files, the base images or
  `pip install .`.

## Decisions

### D1 — A constraints file, applied with `-c`
Pins live in `benchmark-constraints.txt` and are applied with `pip install -c`. A constraint
restricts versions only for packages the resolver actually pulls in, which is exactly
"freeze what these steps resolve, add nothing".

Alternatives considered:
- Inline `pkg==ver` arguments in each Dockerfile: duplicated across the two templates, and
  it would *install* every listed package rather than constrain it.
- A full `requirements.txt` lock: the same install-everything problem.

### D2 — The file lives at `src/cli/templates/dockerfiles/benchmark-constraints.txt`
Only `src/` reaches the build context, and inside the image `COPY archi_code src` places
the file at the relative path the Dockerfile uses (`WORKDIR /root/archi`). It also sits next
to the two Dockerfiles that use it.

Alternative considered: add `requirements/` to `copy_source_code`. That changes staging for
every service, a larger blast radius than this fix needs.

### D3 — The file pins what the benchmark steps install or change, at known-good versions
Content is the measured set. Take every package whose version differs between the stage-1
freeze and the known-good final freeze, minus the stage-1-only drift that the benchmark steps
do not touch (`executing`, `python-discovery`, `virtualenv`, `wcwidth`; they are identical in
today's stage-1 and today's broken final). Add `huggingface-hub==0.36.2`: it is equal in
stage-1 and the good final, but the benchmark steps moved it today. Versions come from the
known-good image, the only build proven to import and to complete runs.

Alternatives considered:
- **Only the broken packages** (`datasets`, `huggingface-hub`, `pyarrow`): leaves
  `instructor`, which drives seven overrides, and the rest of the closure floating. The next
  drift is one upstream release away.
- **The full 315-line freeze:** also constrains packages the base image owns, so a benchmark
  step could silently downgrade or upgrade a base package. It also couples this file to every
  base churn.

Completeness is measured, not assumed (spec: "covers everything the benchmark steps move").
After the fix's no-cache build, the stage-1 vs final diff must be a subset of the file. Any
miss is added and the build repeated. The file header documents the procedure, so the next
regeneration follows the same steps.

### D4 — One `RUN` with one resolver pass
The four steps become
`RUN pip install -c src/cli/templates/dockerfiles/benchmark-constraints.txt pandas ragas==0.3.5 langchain-openai==1.0.1 'argilla>=2.5,<3'`,
with unchanged specifiers. One pass resolves `ragas`' and `argilla`'s shared closure jointly,
instead of letting a later step undo an earlier one.

Alternative considered: four `RUN`s, each with `-c`. The constraints would be equivalent, but
there would be four partial resolutions and four places to forget the flag.

### D5 — The guard reuses the compatibility module's reader
`tests/unit/test_benchmark_dockerfile_pins.py` imports `_parse_pins`, `_parse_requirements`,
`_opaque_requirements` and `_requirement_bearing_directives` from
`tests.unit.test_base_image_dependency_compatibility`. Cross-test imports have precedent at
`tests/unit/test_render_preflight.py:11`.

The constraints file must satisfy four checks:
- `_parse_requirements` and `_parse_pins` cover the same names, so every line is an exact pin;
- `_opaque_requirements` is empty;
- `_requirement_bearing_directives` is empty;
- no other line starts with `-`.

`pyproject.toml` pins are read with `tomllib` from `[project].dependencies` and fed through
`_parse_pins`, so both pin sources go through one reader.

The Dockerfile side is deliberately narrow:
1. Join backslash continuations and drop comment lines.
2. Fail closed on any `RUN [` exec form or `<<` heredoc that mentions `pip`.
3. Split each shell-form `RUN` on `&&` / `;`.
4. After the segment that installs `.`, require every `pip install` segment to carry `-c`
   with the constraints path.

It reads the two benchmark templates and nothing else.

Alternative considered: reuse the instruction reader from
`tests/unit/test_service_template_downloads.py`. It is built around `tar`/`wget`/`curl`, and
adapting it would widen a module with a recorded contract (#519 D31) for a different purpose.

### D6 — Compatibility facts are tables keyed by the base pin, and they fail closed
`HUB_BOUND_BY_TRANSFORMERS = {"4.53.3": "<1.0"}` and
`PYARROW_BOUND_BY_NUMPY_MAJOR = {1: "<26"}`, each with its measured date and source:
- the `transformers` bound comes from `requires_dist` (`huggingface-hub<1.0,>=0.30.0`);
- the `pyarrow` bound comes from an import in a built image
  (`pyarrow requires NumPy 2.0 or newer, found 1.26.4`), because pyarrow declares no NumPy
  requirement.

A base pin absent from the table fails with "re-measure", matching `_measured` in the
compatibility module.

### D7 — An override table with measured causes, checked in both directions
`BENCHMARK_OVERRIDES` maps each overridden name to the project pin it overrides and the cause.
The initial entries are:
- `requirements-base.txt` pins: `aiohttp` 3.9.5, `idna` 3.10, `typer` 0.15.4,
  `urllib3` 2.3.0, `langchain-openai` 1.1.10;
- `pyproject.toml` pins: `click` 8.1.7, `jinja2` 3.1.3, `requests` 2.31.0;
- `requests` is pinned 2.32.5 in the base too.

The causes are:
- for most entries, `instructor 1.17.0 requires …`;
- for `langchain-openai`, the Dockerfile's explicit `langchain-openai==1.0.1`;
- for `typer`, an indirect conflict: `typer 0.15.4` requires `click<8.2,>=8.0.0` while
  `instructor 1.17.0` requires `click>=8.3.3`, and `ragas`/`instructor` both require `typer`,
  so the resolver must move `typer`. This was measured in the stage-1 and known-good images on
  2026-10-11; no requirement excludes 0.15.4 directly.

An entry without a measured cause is not admitted.

The guard fails on any divergence missing from the table, and on any entry that no longer
diverges.

### D8 — Verification is a build, documented; this change adds no CI job
The no-cache CPU build from the CLI's staging layout is the verification:
- copy `src` to `archi_code`, plus `pyproject.toml`, `LICENSE`, empty `configs/` and
  `weblists/`, and an empty `git_info.yaml`;
- run the import smoke, `pip check` and the completeness diff;
- record the commands and outputs in the PR.

The GPU variant gets the static guard only, because
`ghcr.io/fasrc/a2rchi-pytorch-base:v2026.08.0` is not on the dev host (an 18 GB pull).

## Risks / Trade-offs

- **[Pinned versions age, missing security fixes]** → The header documents regeneration from
  a known-good build. The override table makes every divergence from project pins reviewable.
- **[The lock misses a package that floats later]** → The completeness diff runs on every
  change to the file or the steps (spec), and the procedure lives in the file header. The
  remaining exposure is a new transitive dependency introduced by an upstream release of an
  already-constrained version, which is impossible for exact pins.
- **[GPU variant unverified by build]** → The constraints touch only packages the benchmark
  steps resolve. The torch-adjacent overlap is `fsspec`, which torch requires without bounds.
  This is recorded in the PR.
- **[The guard freezes a benchmark-vs-production divergence]** → Recorded explicitly as an
  open question, with the client that talks to the system under test called out.
- **[A static guard reads green while the image is broken]** → Stated in the guard's
  docstring, which follows the compatibility module's tiers. The build is the gate; the guard
  is a regression net.

## Migration Plan

None. Existing images and running stacks are untouched until rebuilt. The next `archi evaluate`
picks up the constraints automatically. Rollback is a revert. The 2026-10-11 retrieval A/B runs
on locally pinned images and is unaffected.

## Open Questions

- Should the benchmark image run production's `langchain-openai` (1.1.10) instead of 1.0.1,
  the client the system under test is reached through? Why it was pinned to 1.0.1 (RAGAS
  compatibility?) is unrecorded. Candidate follow-up issue.
- Add a CI job that builds `Dockerfile-benchmarks` and runs the import smoke, closing the
  tier-3 gap for this image? Candidate follow-up issue, to be filed only with operator approval.
