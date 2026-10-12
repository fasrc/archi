# Pin what the benchmark image's own pip steps install

## Why

A fresh build of the benchmark image produces a container that dies before it asks one
question (fasrc/archi#681, P1). `src/cli/templates/dockerfiles/Dockerfile-benchmarks:32-35`
(and `Dockerfile-benchmarks-gpu:80-83`) runs `pip install pandas`, `pip install ragas==0.3.5`,
`pip install langchain-openai==1.0.1` and `pip install 'argilla>=2.5,<3'` after `pip install .`.
Each call resolves only the packages it names, so it neither pins their transitive closure nor
enforces the upper bounds of packages already in the image. Measured on the dev host on
2026-10-11:

| What floated | What it broke |
|---|---|
| `datasets` 5.0.1 → **5.1.0**, which requires `huggingface-hub>=1.31` | pip moved `huggingface_hub` 0.36.2 → **2.2.0**; `transformers==4.53.3`, `tokenizers 0.21.4` and `langchain-huggingface==1.0.0` all require `<1.0`, so `sentence_transformers` cannot be imported and `benchmarking-<name>` exits 1 at startup |
| `pyarrow` 25.0.1 → **26.0.0** | `ImportError: pyarrow requires NumPy 2.0 or newer, found 1.26.4` (`numpy==1.26.4`, `requirements/requirements-base.txt:101`). pyarrow does not declare that floor, so **no resolver can catch it** |

Every `archi evaluate` that builds a fresh benchmark image is affected: golden-set runs and
feature-matrix arms cannot start. Images built before the drift keep working, and no
workflow builds this image, so nothing caught it before a deploy did.

The same measurement shows the problem is wider than two packages. Comparing the image
after `pip install .` (built 2026-10-11) with the last working final image
(`benchmarking-prompt-sweep-r0-atoms:2000`, 2026-09-29), the benchmark-only steps add 12
packages and move 10. Eight of those moves override a pin the rest of the project
declares, and every one is **forced**: `instructor 1.17.0`, an unpinned dependency of
`ragas`, requires `aiohttp>=3.14.3`, `click>=8.3.3`, `idna>=3.15`, `jinja2>=3.1.4`,
`requests>=2.33.0`, `rich<15` and `urllib3>=2.7.0`. So a constraints file cannot simply
agree with `requirements-base.txt`. It has to record which pins the benchmark image
overrides, and why.

## What Changes

- New `src/cli/templates/dockerfiles/benchmark-constraints.txt`: exact `name==version`
  pins for every package the benchmark-only pip steps install or change, at the versions of
  the last working image. That includes `datasets==5.0.1`, `huggingface-hub==0.36.2`,
  `pyarrow==25.0.1`, `instructor==1.17.0` and the forced overrides. It lives under `src/`
  because the deployment build context receives only `src/` (as `archi_code/`),
  `pyproject.toml` and `LICENSE` (`TemplateManager.copy_source_code`,
  `src/cli/managers/templates_manager.py:1330-1334`).
- `Dockerfile-benchmarks` and `Dockerfile-benchmarks-gpu`: the four benchmark-only pip steps
  become one `pip install -c src/cli/templates/dockerfiles/benchmark-constraints.txt ...`
  with the same requirement specifiers. `pip install .` stays unconstrained.
- New guard `tests/unit/test_benchmark_dockerfile_pins.py`, a static net in the style of
  `tests/unit/test_base_image_dependency_compatibility.py`, reusing its pip-style line reader.
  It checks that:
  - both Dockerfiles constrain every benchmark-only pip step;
  - the constraints file holds only exact pins;
  - the packages that broke, and the one that forces the overrides, are pinned;
  - measured compatibility tables hold: `huggingface-hub` < 1.0 while `transformers==4.53.3`
    is pinned, and `pyarrow` < 26 while `numpy` < 2 is pinned;
  - every constraint that differs from a `requirements-base.txt` or `pyproject.toml` pin is
    listed, with its measured cause, in an override table, and no table entry is stale.

## Capabilities

### New Capabilities
- `benchmark-image-dependencies`: what the benchmark image installs beyond the archi
  package. It covers the pinned constraints file, its use in both benchmark Dockerfiles,
  and the static guard. The guard checks the file's form, its compatibility with the base
  pins, and the recorded overrides of project pins.

### Modified Capabilities
<!-- None: no existing spec covers the benchmark image's build-time dependencies. -->

## Impact

- **Files:** `src/cli/templates/dockerfiles/Dockerfile-benchmarks`,
  `src/cli/templates/dockerfiles/Dockerfile-benchmarks-gpu`, the new
  `src/cli/templates/dockerfiles/benchmark-constraints.txt`, the new
  `tests/unit/test_benchmark_dockerfile_pins.py`, and one negation line in `.gitignore`:
  its blanket `*.txt` rule would otherwise ignore the constraints file, following the
  `!examples/qa_eval/sentinel_value.txt` precedent. No Python under `src/` changes, so
  diff-cover has no lines to measure.
- **Benchmarks:** a fresh benchmark image starts again, and its benchmark-only
  dependencies stop moving between builds. That makes two images built on different days
  comparable on dependencies, which the A/B and replicate methodology assumes.
- **Not touched:** `requirements/requirements-base.txt`, the `*HEADER.txt` files and the
  base images (changing them forces a base-image rebuild, and their pins are coupled).
  Also untouched: `pip install .` and the specifiers `ragas==0.3.5`,
  `langchain-openai==1.0.1`, `argilla>=2.5,<3`. This change freezes what they resolve to;
  it upgrades nothing.
- **Known, pre-existing, out of scope (recorded, not fixed):**
  - The benchmark image runs different versions than production for the overridden
    packages. Most notably it uses `langchain-openai` 1.0.1, the client that talks to the
    system under test, where the base pins 1.1.10.
  - `pip check` reports archi's own `click`/`jinja2`/`requests` pins as violated.
  - No CI job builds this image.
