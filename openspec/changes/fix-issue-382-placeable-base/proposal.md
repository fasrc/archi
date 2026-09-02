# Require a service template's base to be one the preflight can actually probe

## Why

`templates_missing_base_reference` (`src/cli/managers/base_image_preflight.py:109`) decides
whether the deploy preflight covers a service template. Its docstring says a non-empty return
means "the deploy preflight cannot cover these templates", and `_refuse_uncoverable_templates`
(`:521`) turns that list into a refusal on the one path `archi create` takes —
`enforce_base_images` (`:537`), called from `src/cli/cli_main.py:282`.

The promise is "the preflight can place this template's base". The check is one regex search:

```python
if not _FROM_BASE_RE.search(template.read_text())
```

where `_FROM_BASE_RE` is `^FROM\s+(?P<ref>\S*a2rchi-\w+-base\S*)` (`:43`). Two inputs satisfy
that search without delivering the promise.

**1. `\w+` matches any base name.** `enforce_base_images` probes only what
`required_base_image_names` (`:168`) returns — `PYTHON_BASE` (`a2rchi-python-base`), and
`PYTORCH_BASE` (`a2rchi-pytorch-base`) when a GPU is requested or the grader is enabled. A
template on a third `a2rchi-<something>-base` matches the regex, so it counts as covered while
its base is never probed.

**2. `search` returns the first match anywhere in the file.** In a multistage template an early
`FROM ... a2rchi-python-base AS builder` satisfies the check even when the final stage is
`FROM docker.io/library/debian:12`. The image the deployment actually runs is never probed.

In both cases `base_reference` (`:122`) still resolves the required names from the other healthy
templates, so `enforce_base_images` returns a complete-looking answer and `archi create --force`
proceeds through `remove_existing_deployment()` (`src/cli/cli_main.py:294`) before the build
fails. That is the ordering contract from fasrc/archi#287 that this module exists to hold.

### Measured

Reproduced on this change's branch base, `origin/dev` at `7c9915d0`, against the checkout's own
module (`/home/austin/Projects/archi-bot/src/cli/managers/base_image_preflight.py`). Each fixture
is one digest-pinned `Dockerfile-chat` plus the offending template, in a temp directory:

```
[unknown a2rchi base (a2rchi-node-base)]
  missing: []
  enforce: NO REFUSAL -> ['ghcr.io/fasrc/a2rchi-python-base@sha256:aaaa...']

[multistage, final stage third-party]
  missing: []
  enforce: NO REFUSAL -> ['ghcr.io/fasrc/a2rchi-python-base@sha256:aaaa...']
```

**No existing guard catches either.** `test_two_image_rule_still_matches_every_template`
(`tests/unit/test_base_image_preflight.py:261`) is the closest and does not: the two `if`
statements at `:277-281` flag only `base == "pytorch"` on a non-`-gpu` non-grader template and
`base == "python"` on a `-gpu` template. A `base` of `node` matches neither branch and the test
passes in silence.

### Present-day risk: none

Both are gaps in the guarantee, not current faults. Measured on `7c9915d0`:

```
$ grep -ho 'a2rchi-[a-z]*-base' src/cli/templates/dockerfiles/Dockerfile* | sort -u
a2rchi-python-base
a2rchi-pytorch-base

$ for f in src/cli/templates/dockerfiles/Dockerfile*; do n=$(grep -c '^FROM' "$f"); \
    [ "$n" -gt 1 ] && echo "$f: $n"; done
(no output -- no template is multistage)
```

The trigger for either is a future change: a third base image, or a service template converted
to multistage. Both are the "silently outside every guard" failure #361 existed to end, which is
why this is worth closing rather than dropping. It is filed as P3 for that reason.

### Where this came from

Codex review of PR #380 (`fix/issue-361-declare-service-templates`), two P1 findings on
`base_image_preflight.py:119` raised against head `6a50effc` and reproduced there — comment IDs
`3879446497` and `3879446522`. Both were verified and deferred by the 4AM review pass, which had
reached its round bound. PR #380 merged on 2026-08-28 (`acce8598`), so the branch base is
`origin/dev` and every symbol named here is present on it.

## What Changes

- **A named placeable set**, beside `PYTHON_BASE` / `PYTORCH_BASE` (`:26-27`): the a2rchi base
  names the preflight knows how to probe. One name for the idea, referenced by **both**
  `required_base_image_names` and the coverage check, so the two cannot disagree about which
  bases exist. Today they disagree by construction — one enumerates two constants, the other
  accepts `\w+`.
- **The coverage check decides per template, not per first match.** `templates_missing_base_reference`
  reports a template unless the base its **final stage** resolves to is in the placeable set. A
  template with no match at all is still reported, as today.
- **Multistage resolution, with its bound stated in the code.** A final stage that is
  `FROM <earlier-stage-alias>` follows the alias back to its base. What the resolution does not
  handle is written down rather than implied — see below.
- **`_FROM_BASE_RE` is not widened.** `base_reference` (`:122`) shares it and must keep matching
  any a2rchi reference, because its job is to find the pinned reference for a name it was already
  given. The new strictness lives in `templates_missing_base_reference`.

## The decisions this change had to make

**Where the strictness goes.** Tightening `_FROM_BASE_RE` would reach `base_reference` too, and
`base_reference` is asked "what reference do the templates declare for *this* name" — it is
already given the name, so narrowing its regex would only make it fail to find pins it should
find. The two functions want opposite things from the same pattern, which is why the placeable
check belongs in the caller.

**Fail closed on anything unresolvable.** A `FROM` line the stage parser cannot resolve — an
`ARG`-substituted reference such as `FROM ${BASE_IMAGE}`, or a form the pattern does not match —
makes the template **reported**, not silently covered. The whole defect being fixed is a check
that answers "covered" when it does not know, so the unknown case must fall to the refusing side.
The cost is a false red on a template style the repo does not currently use; the benefit is that
the next unhandled form arrives as a conversation instead of a deploy that tore down a running
stack before failing.

**State the bound instead of implying totality.** The resolution handles a linear chain of named
stages. It does not interpret `ARG`, build args, `--platform` flags, or `COPY --from` provenance.
That sentence belongs in the code, because "the original defect shipped" is exactly what happens
when a partial implementation reads as a total one.

## Capabilities

### New Capabilities

- `service-base-images`: adds two requirements — one for the placeable set, one for judging a
  multistage template by the stage the deployment runs. The capability directory does not exist
  under `openspec/specs/` yet; five unarchived changes contribute to it
  (`fix-issue-266-ghcr-base-images`, `fix-issue-334-digest-pinned-base-refs`,
  `fix-issue-335-pin-service-dockerfiles-to-digests`, `fix-issue-339-release-retarget-orig-tag`,
  `fix-issue-361-declare-service-templates`). This change therefore **adds** requirements rather
  than modifying #361's, which are not in `specs/` to modify.

### Modified Capabilities

None.

## Impact

- `src/cli/managers/base_image_preflight.py` — the placeable-set constant, the rewritten
  comprehension in `templates_missing_base_reference`, and the stage-resolution helper.
  Coverage-measured (`scripts/gate.sh` runs `--cov=src`), so the new lines need the tests below
  to clear the 80% patch floor.
- `tests/unit/test_base_image_preflight.py` — four new tests: unknown-base and multistage, each
  asserted at the unit level **and** at `enforce_base_images`. The second of each pair is the one
  that matters; the unit-level check is not what protects the operator.
- **A template whose final stage aliases an earlier a2rchi stage must stay covered.** The change
  makes the check stricter, so it needs a test that it did not become strict about the wrong
  thing.
- `test_templates_missing_base_reference_on_real_directory_is_empty`
  (`tests/unit/test_base_image_preflight.py:1314`) must still pass. The 15 real service templates
  all name python or pytorch and none is multistage, measured above. If it fails, a template
  changed and that is the more interesting news.
- The Dockerfile templates, `.github/workflows/**`, and `scripts/dev/update_service_base_images.py`
  — **not** edited. This is one function's strictness.
- Every RED step runs against a `tmp_path` fixture directory, never by editing the real templates.
  A red real tree cannot be committed, and the fixture proves the same discrimination.

## Non-goals

- Widening or replacing `_FROM_BASE_RE`.
- Teaching the preflight to probe a third base image. This change makes an unknown base **refuse**;
  deciding to support one is a separate change that would add it to the placeable set and to
  `required_base_image_names` together.
- Any change to the two-image rule of design D4. `required_base_image_names` must keep returning
  python always and pytorch exactly when a GPU is requested or the grader is enabled.
