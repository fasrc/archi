## Context

fasrc/archi#361 replaced four independently-derived guesses at "which templates are service
templates" with one declaration: every `Dockerfile*` under the template directory, minus a
named exclusion list. The direction of that default is the point — a template added to the
repository is a service template on the day it is added, and leaving the set is a deliberate
act with a recorded reason.

The declaration is derived with `Path.glob`, which does not recurse. So the default only
holds at depth 1. At depth 2 the default silently inverts: a template is outside the set
until someone notices, which is the failure mode the declaration was written to remove.

Codex raised this on PR #380 (`src/cli/managers/base_image_preflight.py:96`, comment
`3879446510`, head `6a50effc`), and the 4AM review pass verified it and deferred it: a gap in
the guarantee rather than a current fault, and closing it needs a change to how exclusions
are expressed.

Current state on this branch's base (`origin/dev` at `7c9915d0`):

- 21 `Dockerfile*` files under `src/cli/templates/dockerfiles/`; 19 top-level, 2 nested.
- Both nested files are named exactly `Dockerfile`.
- `NON_SERVICE_TEMPLATES` holds 4 filename keys; `service_templates()` returns 15.

## Goals / Non-Goals

**Goals:**

- A Dockerfile at any depth under the template directory is a member of the declared service
  set unless it is deliberately excluded.
- The exclusion list can name the two existing nested base-image Dockerfiles, and can only
  name them precisely.
- `stale_template_exclusions` keeps its guarantee: a key naming a file that does not exist
  fails the suite.
- The service-template count stays 15. The 15 references the deploy preflight and the pin
  guard check are unchanged.

**Non-Goals:**

- Changing the template layout. The nested base-image directories are correct where they are.
- Editing any Dockerfile template.
- Making `scripts/dev/update_service_base_images.py` recursive. It is a text rewriter over
  every template, including the excluded ones, not a consumer of the declaration.
- Anything touching `templates_missing_base_reference`'s own logic — fasrc/archi#382 is open
  on that function. Separate functions, either merge order works.

## Decisions

**D1. Exclusion keys become paths relative to the template directory, compared with
`p.relative_to(directory).as_posix()`.**

This is the decision the whole change turns on. `NON_SERVICE_TEMPLATES` is keyed by
`p.name`, and the two files that must be excluded are both named `Dockerfile`. A filename key
cannot distinguish `base-python-image/Dockerfile` from `base-pytorch-image/Dockerfile`, and
cannot distinguish either from a future top-level `Dockerfile`. A relative-path key names
exactly one file.

The four existing keys are top-level, where a relative path and a filename are the same
string, so they carry over unedited. `as_posix()` keeps the key stable across platforms.

**D2. `stale_template_exclusions` needs no logic change, but must be re-proved.**

It already does `(directory / name).exists()`, and `directory / "base-python-image/Dockerfile"`
resolves correctly. The guarantee is worth re-proving against a relative-path key rather than
assumed, because the whole value of the function is that it fails when a key stops naming a
real file.

**D3. The counts are re-measured, not predicted.**

`test_service_templates_has_15_of_19_and_excluded_names_match_the_declaration`
(`tests/unit/test_base_image_preflight.py:1260`) globs the real directory non-recursively and
compares `{p.name}` sets. Both halves change: the traversal becomes recursive and the
comparison becomes relative paths, so the split reads 21 / 15 / 6.

The service count is the load-bearing number. 15 before and 15 after means the change added
two members and two exclusions that cancel — the two nested base-image files. A service count
that moved would mean the recursion pulled in something nobody has classified, which is a
finding to investigate rather than an assertion to update.

**D4. The entry-point assertion is the one that protects the operator.**

fasrc/archi#381 landed a refusal only in `required_base_images`, which has no production
caller, so the deploy path went on silently. `enforce_base_images` (`:537`) is what
`archi create` calls (`cli_main.py:282`). A nested-template test at the helper level alone
would repeat that mistake, so this change asserts at `enforce_base_images` as well.

**D5. The real-directory guard is asserted against the real directory.**

A test that the two nested base-image Dockerfiles are outside `service_templates()` is only
worth writing if it runs against `TEMPLATE_DIR`. A `tmp_path` fixture would prove the
exclusion mechanism works and would not notice a future traversal change pulling the real
files in.

## Risks / Trade-offs

- **A relative-path key is longer and easier to mistype.** `stale_template_exclusions` is the
  mitigation, and it already exists: a mistyped key names no file and fails the suite.
- **`rglob` walks the whole subtree.** The template directory holds 21 files, so cost is not a
  concern, but the set now depends on directory contents that were previously irrelevant. Any
  future non-template file matching `Dockerfile*` under a subdirectory becomes a service
  template until excluded — which is the intended default direction, and is why the failure
  names the path.
- **PR #387 (fasrc/archi#382) is open on `templates_missing_base_reference`.** Whichever
  merges second re-measures the counts. This change does not touch that function, so the
  overlap is confined to the test file.
- **The updater's own non-recursive traversal stays.** Recorded rather than fixed, so a
  nested service template would still be outside the pin rewriter's reach. That is a narrower
  gap than the one being closed here — the preflight now refuses such a template before a
  deployment can use it.
