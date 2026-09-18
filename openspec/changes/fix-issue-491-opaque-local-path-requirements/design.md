# Design — reject path and archive requirements as opaque

## Context

`tests/unit/test_base_image_dependency_compatibility.py` reads requirement text in three
layers. `_requirement_lines` (`:296-309`) yields the requirement lines, dropping blanks,
`#` comments and every line starting with `-`, and cutting a trailing comment and an
environment marker. `_parse_requirements` (`:350-358`) maps a normalized project name to
its specifier text with `_REQUIREMENT_PATTERN` (`:273`). `_opaque_requirements`
(`:338-347`) fails closed on a line whose project name the module cannot read.

The hole is in the third layer. `_VCS_OR_URL_REQUIREMENT` (`:333-335`) enumerates VCS
schemes, `http(s)://`, `file://` and a Windows drive letter. pip also accepts a bare local
project path and a bare archive path, and neither is in that set.

The consequence is specific to this module: `PROTECTED_PACKAGES` (`:279`) carries the
comment that for these six names *"absent must mean absent and never present-but-not-an-
exact-pin"*, because every pairwise guard skips on absence. An unreadable line that is not
reported is therefore not a missed warning — it is a silent skip of every guard that names
the package.

## Decisions

### D1 — One sibling pattern, consulted by `_opaque_requirements`; measured before authoring

Add a module-level compiled pattern for the path/archive class and have
`_opaque_requirements` report a line matching **either** it or `_VCS_OR_URL_REQUIREMENT`.
This keeps one reporting function and one fail-closed rule, and it mirrors how
`_REQUIREMENT_BEARING_PATTERN` (`:381-383`) was added alongside the existing matcher on
2026-09-16 rather than folded into it.

A recognizable shape (the implementer may adjust so long as the tests pass):

```python
_ARCHIVE_SUFFIX = r"\.(?:whl|zip|tgz|tbz2?|txz|tar|tar\.gz|tar\.bz2|tar\.xz)"
_PATH_OR_ARCHIVE_REQUIREMENT = re.compile(
    rf"^(?:\.|[^\s;]*[\\/]|[^\s;]*{_ARCHIVE_SUFFIX}(?:\s|;|$))", re.IGNORECASE
)
```

Three alternatives, one per clause of the class: a leading `.`, a token containing a
separator, a token ending in an archive suffix. The suffix alternation needs the trailing
`(?:\s|;|$)` anchor — without it `backports.tarfile==1.2.0` would match on `.tar`.

**Measured on branch `fix/issue-491-opaque-local-path-requirements` at `4b253e26`**, before
this design was written, with the candidate pattern above:

- **9 of 9 positives matched**: `./local_vllm`, `../pkgs/vllm`, `/opt/vllm.whl`,
  `vllm-0.9.0-py3-none-any.whl`, `dist/vllm-0.9.0.tar.gz`, `.\win_vllm`, `C:\pkgs\vllm`,
  `vllm-0.9.0.tar.bz2`, `vllm-0.9.0.zip`.
- **9 of 9 negatives stayed clean**: `vllm==0.9.0`, `markitdown[pdf,pptx]==0.1.5`,
  `langgraph-prebuilt<1.0.9`, `torch==2.7.0`, `opentelemetry-sdk==1.27.0`,
  `zope.interface==5.4.0`, `backports.tarfile==1.2.0`, `ruamel.yaml==0.18.6`,
  `vllm>=0.9,<0.10`.
- **All five monitored files reported zero matches**, across 327 requirement lines:
  `requirements/cpu-requirementsHEADER.txt` (1 line),
  `requirements/gpu-requirementsHEADER.txt` (5),
  `requirements/requirements-base.txt` (105),
  `src/cli/templates/dockerfiles/base-python-image/requirements.txt` (106),
  `src/cli/templates/dockerfiles/base-pytorch-image/requirements.txt` (110).

The false-positive risk is therefore measured at zero on the present tree, and the red the
implementer must produce is known reachable. `backports.tarfile` and `zope.interface` are
in the negative set on purpose: a dotted project name is the nearest real thing to an
archive filename, and it is what an unanchored suffix rule would break.

### D2 — Do not touch `_REQUIREMENT_PATTERN`; report, never parse

The archive form is worse than the path form today, because it *does* match the name reader
and records a project called `vllm-0-9-0-py3-none-any-whl`. The tempting fix — teach the
name reader to recognise a wheel filename and extract `vllm` from it — is rejected.

PEP 427 filename parsing is a second parser with its own failure modes (local version
segments, platform tags, a project name that itself contains a digit-hyphen run), and the
version it recovers is not a pin the module can check against a specifier. A wrong
extraction would restore the exact bug this change closes, this time with a plausible name
attached, which is strictly harder to notice than a skip. Report the line and stop.

`_REQUIREMENT_PATTERN` also feeds `_parse_requirements`, which feeds `_unpinned_protected`
and every guard downstream. Changing it is a blast radius the issue does not ask for.

### D3 — Spec delta uses `ADDED` requirements under `dependency-pin-hygiene`

PR #474 (`120d9619`, issue #472) introduced this whole guard module and added **no**
OpenSpec artifacts, so no capability describes it yet. `dependency-pin-hygiene` is the
right home: it already carries the fail-closed-on-an-unreadable-requirement-shape rule for
the sibling guard `tests/unit/test_requirements_hygiene.py`, established by
`fix-issue-253-duckdb-guard-pip-directives`. The behaviour class is identical — a
requirement the guard cannot name must fail the suite rather than read as silence — so a
second near-identical capability would fragment the model for no gain.

That capability's spec is **not** in `openspec/specs/` (its originating change
`fix-issue-246-remove-dead-duckdb-pin` is merged but unarchived, gated on #254), so a
`MODIFIED` delta against it cannot validate. The delta therefore `ADD`s new, distinctly
named requirements scoped to the path/archive class in the base-image guard. When the
changes archive, the requirements merge into the capability without collision.

### D4 — PEP 508 direct references are out of scope, because they already fail closed

`vllm @ https://host/vllm-0.9.0.whl` looks like the same class but is not. Measured at
`4b253e26`: `_REQUIREMENT_PATTERN` reads the name `vllm` and the specifier
`@ https://host/vllm-0.9.0.whl`, which is not an exact pin, so `_unpinned_protected`
(`:475`) already turns it into a failure. Adding it to the opaque set would change a
specific "vllm is not exactly pinned" failure into a vaguer "opaque requirement" one. Left
alone deliberately, and recorded here so the next reviewer does not read it as an omission.

## Risks

- **Tree stays green**: measured, D1 — zero matches across all five monitored files.
- **Over-matching a dotted project name**: the archive clause is anchored on the right, and
  `backports.tarfile==1.2.0` is a standing negative test case that fences it.
- **Windows drive letters double-report**: `C:\pkgs\vllm` matches both the existing pattern
  and the new one. `_opaque_requirements` returns lines, not match objects, so a line is
  reported once regardless. Harmless, and the test asserts on the reported line.

## Boundary, stated rather than implied

`_opaque_requirements` reports; it never resolves a name from a path, a wheel filename or an
archive. It reads only the text handed to it, and only the lines `_requirement_lines`
yields — so an option line (anything starting with `-`) is still the business of
`_requirement_bearing_directives` (`:388-403`), not this pattern. The docstring says so, so
the next reviewer does not have to rediscover it.
