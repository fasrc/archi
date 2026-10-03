## Context

The base-image preflight (`src/cli/managers/base_image_preflight.py`) has one entry point,
`enforce_base_images` (`:1105`), called by `create` (`cli_main.py:283`) and `evaluate`
(`cli_main.py:912`) before any teardown. It derives the a2rchi base references from the
two-image rule, then `run_preflight` (`:1018`) decides each reference: availability first
(`decide_availability`, `:722`), then the Python floor (`check_python_floor`, `:768`) for
every present image. The third-party images that `Dockerfile-postgres` and
`Dockerfile-grafana` build FROM are excluded by `NON_SERVICE_TEMPLATES` (`:189`) and so are
never probed.

The operator decided the shape on 2026-09-26 (issue body, "Decision").

## Goals / Non-Goals

**Goals:**
- A host that cannot obtain a third-party base image of a planned service is refused
  before teardown, with the same refuse and could-not-tell paths as the a2rchi bases.
- One loop decides every image. The floor check is skipped only by an explicit flag.

**Non-Goals:**
- Images that compose pulls directly with no Dockerfile (for example mattermost, piazza).
- Teardown order in `cli_main.py` (#436 did that).
- Any change to the two-image rule or its invariants.

## Decisions

### D1. A reference object with a `check_floor` flag

`BaseReference(image: str, check_floor: bool = True)`, a frozen dataclass or NamedTuple.
`run_preflight(references: Sequence[Union[str, BaseReference]], ...)` normalizes each
item: a `str` becomes `BaseReference(item)`. `Outcome.reference` stays the image string,
so `summarize`, `compose_message` and `unverified_notes` need no type change.

Rejected: a separate availability-only function whose results the caller merges. It gives
two code paths and a silent fourth outcome (decided with the operator on 2026-09-26).

### D2. Floor-free images: availability is the outcome

When `check_floor` is false, `run_preflight` does not call `probe.python_version` and does
not call `check_python_floor`.

On a dry run, `decide_availability` returns UNVERIFIED with `Cause.NOT_PULLED` for a
reachable image, because the a2rchi version cannot be read without a pull. A floor-free
image has no version to read, so reachability is the full answer. `decide_availability`
takes a keyword `check_floor: bool = True`; when it is false and `dry` and
`fetch_cause is None`, the verdict is AVAILABLE. All other branches are unchanged: no
runtime on a dry run is still UNVERIFIED (`Cause.NO_RUNTIME`, whose message has no floor
wording), and `Cause.PROBE_UNSUPPORTED` on a dry run is still UNVERIFIED.

### D3. Which third-party images, and where the reference comes from

`THIRD_PARTY_BASE_TEMPLATES: dict[str, str] = {"postgres": "Dockerfile-postgres",
"grafana": "Dockerfile-grafana"}` maps a compose service name to its template.

`third_party_base_references(compose_config, template_dir=None) -> List[str]` returns, in
map order, the final-stage `FROM` of each template whose service is enabled in the plan
(`compose_config.get_service(name).enabled`). It reads the `FROM` with the existing
`_final_stage_base` reader, so the template stays the one source of truth for the tag.

- Unknown service: `get_service` raises `ValueError` (`utils/service_builder.py:110`).
  Treat it as "not in the plan", the same as the grader lookup in `enforce_base_images`.
  Do not catch other exceptions.
- A template that is missing, or that has no readable `FROM`, for an enabled service:
  raise `BaseImagePreflightError` that names the service and template. The preflight must
  not read "cannot name the image" as "nothing to check" (the same rule as the unresolved
  a2rchi path).
- Postgres: the plan enables it whenever it is resolved as a dependency
  (`ServiceBuilder.build_compose_config` calls `resolve_dependencies`), and `evaluate`
  always lists it. Grafana: only when its service is enabled.

The values in `NON_SERVICE_TEMPLATES` stay as documentation. A unit test asserts that each
`THIRD_PARTY_BASE_TEMPLATES` template is also a key of `NON_SERVICE_TEMPLATES`, so the
two maps cannot drift apart.

### D4. Order in `enforce_base_images`

The third-party references are appended after the a2rchi references, as
`BaseReference(ref, check_floor=False)`, de-duplicated, and passed to the same
`run_preflight` call. The template-read refusal of D3 happens before `run_preflight`. Every
refusal stays before `remove_existing_deployment`, because `enforce_base_images` is called
before it on both paths.

### D5. Login text for a third-party UNAUTHORIZED outcome

The current UNAUTHORIZED message names "the fasrc packages" and a classic `read:packages`
token. That is wrong for `docker.io`. When the reference is not under `ghcr.io/fasrc/`, the
message names the registry, the `<tool> login <registry>` command, and the rate-limit or
credential cause, without the fasrc token text. The fasrc text stays unchanged for
`ghcr.io/fasrc/` references.

## Risks / Trade-offs

- A host with a registry mirror or a pre-pulled pgvector image: `image_present` is checked
  first, so a local image passes with no network access. No regression.
- Docker Hub anonymous rate limits can now refuse a `--force` that previously continued.
  This is the intended behavior: the build would have failed after the teardown.
- Postgres is in almost every plan, so the probe adds one `image inspect` (and a pull when
  absent) to most `create` runs.
