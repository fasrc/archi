## 1. Reproduce and red guard

- [x] 1.1 Reproduce on a clean tree. Build `Dockerfile-benchmarks` from `origin/dev` with
      `--no-cache`, using the CLI's staging layout (design D8). Run the import smoke and record
      the `huggingface-hub==2.2.0` / `pyarrow` NumPy failure for the PR.
- [x] 1.2 Write `tests/unit/test_benchmark_dockerfile_pins.py`. Reuse the compatibility module's
      reader (D5). Cover:
      - the Dockerfile `-c` check over both templates;
      - exact-pin-only constraints, rejecting ranges, directives, opaque lines and option lines;
      - the required breakage pins;
      - the measured, fail-closed compatibility tables (D6);
      - `BENCHMARK_OVERRIDES` with measured causes, checked for unlisted and stale entries (D7).
- [x] 1.3 Add negative cases on synthetic text, so each rule is shown to fire: an
      unconstrained step, a range pin, `-r` include, `huggingface-hub==2.2.0`, `pyarrow==26.0.0`
      with NumPy 1.x, an unmeasured `transformers` pin, an unlisted override and a stale entry.
- [x] 1.4 Run the module on the clean tree and confirm it fails for the right reasons: the
      constraints file is missing and the benchmark steps are unconstrained. Record the output.

## 2. Fix

- [x] 2.1 Create `src/cli/templates/dockerfiles/benchmark-constraints.txt` with the D3 set at
      known-good versions. Add a header with the why, a link to fasrc/archi#681, and the
      regeneration and completeness procedure.
- [x] 2.2 In `Dockerfile-benchmarks` (`:32-35`) and `Dockerfile-benchmarks-gpu` (`:80-83`),
      replace the four benchmark-only steps with one constrained `RUN` (D4), keeping the
      specifiers unchanged.
- [x] 2.3 Run the guard green; run the neighbouring dependency guards
      (`test_base_image_dependency_compatibility.py`, `test_requirements_hygiene.py`,
      `test_service_template_downloads.py`) to confirm nothing else moved.

## 3. Verify by build

- [x] 3.1 Build the CPU image from the branch with `--no-cache` in the staging layout. Run the
      import smoke and `pip show datasets huggingface-hub pyarrow`, and confirm `pip check` names
      none of huggingface-hub, datasets, pyarrow, tokenizers or transformers.
- [x] 3.2 Lock completeness: diff the `pip freeze` of the stage after `pip install .` against the
      final image, and confirm every changed package is in the constraints file. If one is
      missing, add it, update the override table if it overrides a project pin, and repeat 3.1.
- [x] 3.3 Record in the PR that the GPU variant is guarded statically only, because
      `ghcr.io/fasrc/a2rchi-pytorch-base:v2026.08.0` is not on the dev host.

## 4. Gate and PR

- [x] 4.1 Run `bash scripts/gate.sh` green (black, isort, `pytest tests/unit`, diff-cover ≥ 80 %),
      then commit with a short lowercase message and no attribution trailers.
- [x] 4.2 Push, open the PR against `fasrc/archi` `dev` with `Closes #681`, and include the
      reproduction, the build verification and the completeness diff. Post `@codex review`.
- [x] 4.3 Answer review findings in-thread; verify each against the code before acting.

## 5. Follow-ups (operator approval before filing)

- [ ] 5.1 Propose a CI job that builds `Dockerfile-benchmarks` and runs the import smoke, closing
      the tier-3 gap for this image.
- [ ] 5.2 Raise the benchmark-vs-production `langchain-openai` divergence (1.0.1 vs 1.1.10), the
      client the system under test is reached through.
