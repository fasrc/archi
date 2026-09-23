# Read four more requirement spellings as pip does in the base-image dependency guard

## Why

`tests/unit/test_base_image_dependency_compatibility.py` guards the two base images against a
protected dependency (`PROTECTED_PACKAGES`, `:278` — opentelemetry-sdk, sympy, torch,
transformers, vllm, xformers) arriving unpinned, conditional, duplicated, or hidden behind a
spelling the module cannot read. Every guard in the module **skips** when its subject is
absent, so a line the module misreads as "absent" is a silent skip. That is the defect class
the module exists to close.

Four requirement spellings are still read differently from pip 26.1.2. All four were posted
as P2 findings on merged PR #506 (head `d6f52c1c`) 93 seconds after the merge, so no review
round ever read them. Re-measured on this branch at `376b5867` (Python 3.11.15, pip 26.1.2,
module loaded from the working tree):

| # | Thread on #506 | Spelling | Guard at `376b5867` | pip 26.1.2 | Direction |
|---|---|---|---|---|---|
| 1 | 4058046837 | `# comment \` then `vllm-0.9.0-py3-none-any.whl` | `_joined_lines` (`:295`) joins the comment and the wheel into one comment line; `_opaque_requirements` returns `[]` | `join_lines` never continues a line its `COMMENT_RE` matches, so the wheel is a line of its own and installs vllm | hides a protected package |
| 2 | 4058046838 | `example.zip [foo] ==1.0`, `example.tar [foo] (>=1)` | `_PATH_OR_ARCHIVE_REQUIREMENT` (`:426`) reports both as opaque archives, because the lookahead after the suffix sees `[`, not a comparison | reads the named requirement `example.zip[foo]==1.0` | fails a legitimate line |
| 3 | 4058046843 | `vllm==0.9.0 \` then `; python_version < "3.11"` | `_parse_pins` reads an exact pin; `_conditional_protected` (`:581`) walks `text.splitlines()` instead of `_joined_lines`, so it returns `{}` | the marker applies to the pin | hides a conditional pin |
| 4 | 4058046844 | `vllm==0.9.0 -Cfoo=bar`, `vllm==0.9.0 -C foo=bar`, and the continued form | `_PER_REQUIREMENT_OPTION` (`:386`) is `\s+--\S+.*$`, so `-C` stays attached, the anchored `_PIN_PATTERN` fails, `_parse_pins` records no pin and `_unpinned_protected` reports vllm | `SUPPORTED_OPTIONS_REQ` carries exactly `--hash` and `-C`/`--config-settings`; `-C` is its only short form | fails a legitimate exact pin |

Findings 1 and 3 are the hiding kind: a protected package supplied that way disables every
pairwise guard that names it while the module reports green. Findings 2 and 4 fail legitimate
work: a maintainer writing a spelling pip accepts gets a red suite with no defect behind it.

**Latent, not a live break.** Measured on the five monitored files at `376b5867` — 
`requirements/cpu-requirementsHEADER.txt`, `requirements/gpu-requirementsHEADER.txt`,
`requirements/requirements-base.txt` and both generated sets under
`src/cli/templates/dockerfiles/base-*-image/requirements.txt`: each reports
`_opaque_requirements == []`, `_unpinned_protected == {}`, `_conditional_protected == {}`.
Backslash continuations: 0. ` -C` occurrences: 0. Whitespace before `[`: 0. So the red test
written first is the whole proof; there is no existing failure to reproduce. Module baseline
at `376b5867`: **183 passed in 0.20s**.

## What Changes

Four edits, each in `tests/unit/test_base_image_dependency_compatibility.py`, in this order
(the two hiding defects first):

- **Finding 1** — `_joined_lines` consults `_COMMENT` before it treats a trailing backslash as
  a continuation, mirroring pip's `join_lines`. A comment line neither opens nor extends a
  continuation buffer. The measured edge is stated in design D1 and it is **not** what the
  issue body describes.
- **Finding 3** — `_conditional_protected` iterates `_joined_lines(text)` rather than
  `text.splitlines()`. The rest of its loop is unchanged.
- **Finding 4** — `_PER_REQUIREMENT_OPTION` also cuts `-Cfoo=bar` and `-C foo=bar`. One edit
  repairs both readers, because `_parse_pins` and `_unpinned_protected` share
  `_requirement_lines` (`:318`), which applies the pattern.
- **Finding 2** — `_PATH_OR_ARCHIVE_REQUIREMENT` allows an optional detached extras list
  between the archive suffix and the comparison lookahead, so `example.zip [foo] ==1.0` is
  exempt while `example.zip [foo]` and `example.zip` stay reported.

Each new regex or loop carries a comment naming the pip rule it mirrors and the review that
found the gap, as the module's existing comments do.

The change is **tests-only** (`tests/unit/**` plus this OpenSpec markdown). No `src/` edit, no
runtime behaviour change, no new dependency.

Explicitly **not** done:

- No new parser. Where a verdict depends on pip, the value is measured from pip and recorded
  in the test docstring, as the module's existing tests do. No hand-rolled PEP 440 or PEP 508.
- No change to `_COMMENT`, `_PIN_PATTERN`, `_REQUIREMENT_PATTERN`, `_ARCHIVE_SUFFIX` or which
  files the module reads.
- No widening of `_conditional_protected`'s comment handling. It cuts at the first `#` while
  `_requirement_lines` cuts at pip's `#`; that asymmetry is real but out of scope here, and
  design D5 records why touching it would change verdicts this change has not measured.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `dependency-pin-hygiene`: the fail-closed reading rules for the base-image compatibility
  guard now cover a comment line that ends in a backslash, a detached extras list on an
  archive-looking project name, an environment marker continued onto the next physical line,
  and pip's short `-C` per-requirement option. The delta is expressed as `ADDED` requirements
  because this capability has no spec in `openspec/specs/` — it exists only in unarchived
  changes, #491's among them (design D6).

## Impact

- **Tests**: `tests/unit/test_base_image_dependency_compatibility.py` only. Baseline at
  `376b5867` is **183 passed**; the acceptance bar is at least **187 passed, 0 failed**.
- **Runtime / src**: none.
- **Monitored files**: unchanged, and they stay green — each must still print
  `[] {} {}` after every commit.
- **Coverage**: the diff is tests-only, so `diff-cover` prints *"No lines with coverage
  information"* and the `--fail-under=80` patch-coverage gate passes. That is the documented
  behaviour for a tests-only diff, not a bypassed gate.
- **Related**: #491 / PR #506 opened the same file's fail-closed rules; this change closes the
  four findings that landed on it after the merge. The four threads on #506 each carry a reply
  naming issue #520.
