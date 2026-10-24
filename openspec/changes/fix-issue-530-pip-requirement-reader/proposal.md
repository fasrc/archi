# Read requirement lines as pip does in the base-image dependency guard

## Why

`tests/unit/test_base_image_dependency_compatibility.py` guards the two base images against a
protected dependency (`PROTECTED_PACKAGES`, `:278`) that arrives unpinned, conditional,
duplicated, or hidden behind a spelling the module cannot read. Every guard in the module
**skips** when its subject is absent, so a misread line is a silent skip.

The module reads a requirement line with a set of regexes that approximate PEP 508 and pip's
option grammar. That approach took nine review-round commits on #527 and still has holes.
Measured on this branch at `5564e016` (Python 3.11.15, pip 26.1.2, packaging 26.2):

| Part | Line | pip 26.1.2 | Guard at `5564e016` |
|---|---|---|---|
| 1 options | `vllm==0.9.0 -Cfoo=bar -Cbad` | rejects | pin `{'vllm': '0.9.0'}`, not reported |
| 1 options | `vllm==0.9.0 --hash=sha256:aa --bogus` | rejects | pin `{'vllm': '0.9.0'}`, not reported |
| 1 options | `vllm==0.9.0 --hash=sha256:aa -Cbad` | rejects | pin `{'vllm': '0.9.0'}`, not reported |
| 1 options | `vllm==0.9.0 -Cfoo"="bar` | accepts (shlex gives `-Cfoo=bar`) | no pin (false report) |
| 2 specifiers | `example.zip [foo] ==`, `example.zip [foo] (>=)`, `example.zip ==`, `example.zip >=` | rejects | not reported |
| #528 extras | `example.zip[foo bar]==1.0` | rejects | pin `{'example-zip': '1.0'}`, not reported |
| #528 extras | `numpy [foo,] @ https://host/numpy.whl` | rejects | not reported |
| #528 non-ASCII | `example.zip [K] ==1.0` (U+212A) | rejects | not reported |

The first three rows are the hiding kind: an exact pin is recorded from a line that makes the
image build fail. The fourth fails legitimate work.

The operator resolved the design on the issue on 2026-09-26: give the PEP 508 grammar to
`packaging.requirements.Requirement`, read the option half with `shlex` against a closed
table, and order the classifier like pip's `install_req_from_line`.

## What Changes

- The option half of a logical line is split off the way pip's `break_args_options` does it
  (literal spaces, first token that starts with `-`) and read with `shlex.split`. Every token
  that starts with `-` must be in a closed table: `--hash=<algo>:<value>`,
  `--hash <algo>:<value>`, `-C KEY=VAL`, `-CKEY=VAL`, `--config-settings KEY=VAL` or
  `--config-settings=KEY=VAL`. Any other dash token keeps the option half on the line, so no
  pin is read from it.
- The requirement half is classified in pip's order: `${VAR}`, VCS or URL scheme, local path,
  archive suffix (unless it is `name@<scheme>…`), then
  `packaging.requirements.Requirement(line)`. `InvalidRequirement` makes the line reported.
- The pin is the single `==` or `===` clause of `req.specifier`. A wildcard, a compound, or a
  range reads as unpinned. The marker is `req.marker`.
- `_parse_pins`, `_opaque_requirements`, `_parse_requirements`, `_declarations` and
  `_conditional_protected` all read through the same reader, so the marker reader and the pin
  reader cannot diverge.
- Nine regex constants are deleted: `_PIN_PATTERN`, `_REQUIREMENT_PATTERN`,
  `_COMPARISON_AFTER_SUFFIX`, `_ATTACHED_EXTRAS`, `_EXTRA_NAME`, `_VALID_EXTRAS_LIST`,
  `_NAME_WITH_SPACED_EXTRAS`, `_NAMED_URL_REFERENCE`, and the two lookaheads in
  `_PATH_OR_ARCHIVE_REQUIREMENT` (the pattern itself goes too).
- Four rows stay stricter than pip on purpose. Each is pinned by a test with a
  `stricter than pip` comment: `vllm-0.9.0-py3-none-any.whl[foo]` and `evil@../pkgs/vllm`
  (design D4 of the guard), `vllm==0.9.0 --no-binary :all:` and `vllm==0.9.0 --has=sha256:aa`.

No `src/` file changes. No requirements file changes. No new dependency: `packaging` is
already imported at `:60-61`, and `pip._internal` stays banned from the module.

## Capabilities

### New Capabilities

### Modified Capabilities
- `dependency-pin-hygiene`: the base-image guard reads each requirement line in pip's order,
  with `packaging` for the requirement half and a closed table for the option half. The
  capability is not yet in `openspec/specs/` (the #520 change is unarchived), so the delta is
  `## ADDED Requirements`.

## Impact

- One file changes: `tests/unit/test_base_image_dependency_compatibility.py`.
- The five monitored files keep 0 opaque lines and the pin counts measured at `5564e016`:
  1, 5, 105, 106, 110. (The issue body says 1, 5, 104, 105, 109. Those numbers are from
  `0ddc96e1`; the requirements files grew by one line each since then.)
- Out of scope: `_joined_lines` and the comment-continuation separator (#529, PR #631), the
  service-template download guard (#519), and any requirements file.

Closes #530.
