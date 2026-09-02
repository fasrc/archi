## Context

`src/cli/managers/base_image_preflight.py` exists to decide, before `archi create --force`
removes anything, whether the base images the service builds need can be obtained. Its
docstring states the invariant every function serves:

> Every path either **establishes** that a base image is usable, **refuses**, or **says out
> loud that it could not tell**. No path may pass silently on an assumption.

`base_reference` (`:123`) breaks it. It reads the first template whose `FROM` names the
requested image and returns, so a deployment whose templates disagree is decided on one of
the references and the others are never named. Measured on `origin/dev` at `7c9915d0` with
two templates on different digests, `required_base_images` returns a one-element list and
`templates_missing_base_reference` returns `[]` — nothing reports a problem.

The shape of the fault matters for where the fix goes. It is not a bad reference; it is a
**set** the code declines to look at. Every consumer downstream — the probe, the floor check,
the diagnostics — is correct about the reference it is handed. So the refusal belongs where
the set is derived, beside `_refuse_uncoverable_templates` (`:521`), which refuses the other
way the declared set can be undecidable.

## Goals / Non-Goals

**Goals**
- Refuse a deployment whose service templates pin a required base image at more than one
  reference, from both entry points, before any probe call and therefore before any teardown.
- Name every reference and the templates that declare it, so an operator can repin without
  reading 15 Dockerfiles.
- Leave the agreeing case byte-identical: same references, same order, same outcomes, on the
  CPU selection and the GPU/grader selection alike.

**Non-Goals**
- Fixing the annotation-keyed repository guard
  (`test_service_templates_pin_one_explicit_base_tag`). It stays as it is; this change adds
  the check the operator's environment actually runs, which the repository test cannot be.
- Deciding which of several references is the right one. The preflight has no basis for that,
  and picking one is what it does today.
- Multistage semantics inside a single template. That is #382 / PR #387.
- Any change to the templates themselves. They agree today.

## Decisions

### D1 — `base_references` returns the set; `base_reference` becomes its first element

`base_reference` keeps its signature and its documented "first match" contract because several
tests and both entry points depend on it (`tests/unit/test_base_image_preflight.py:86-87`,
`:844`). Re-expressing it over `base_references` rather than leaving the old loop in place is
what makes the guard trustworthy: a guard reading one traversal while the probe reads another
could pass on a reference the probe never sees.

The alternative — leave `base_reference` alone and give the guard its own walk — was rejected
for that reason. It is the same class of defect as the one being fixed: two readers of the
same declaration that nothing forces to agree.

Behaviour is unchanged in-tree, and the reason is measured rather than assumed: `base_reference`
walks `glob("Dockerfile-*")` while `service_templates()` walks `glob("Dockerfile*")` minus
`NON_SERVICE_TEMPLATES`, and none of the four excluded templates carries an `a2rchi-*-base`
`FROM` line. The two traversals therefore see the same set of base references today.

### D2 — Scope the check to the images this deployment requires

`required_base_image_names(gpu_ids, grader_enabled)` already decides which bases a deployment
needs. The divergence check reads that same list rather than every base found on disk.

The competing reading is real and worth stating: ignoring a split pytorch pin on a CPU-only
create hides it until the next GPU deployment. It is still the right trade. Refusing a create
because of a disagreement about an image it will never build is a refusal the operator cannot
act on without touching a template unrelated to the deployment in front of them, and this
module's whole value is that a refusal is always about the deployment being attempted. The
docstring records the choice and its cost so the next reader does not have to re-derive it.

### D3 — One reference per template, read through a single seam

Divergence is a disagreement *between* templates. A single template naming the same base twice
— a builder stage on one digest and a shipping stage on another — is a legitimate multistage
shape that PR #387 is teaching the preflight to read correctly.

So the walk asks each template for **the one reference it declares for this image** and
compares across templates. Today that seam returns the first `_FROM_BASE_RE` match in the
file, which is exactly `base_reference`'s existing contract. After #387 merges, the seam is the
single place that has to learn "the stage the deployment runs", and the divergence guard needs
no edit.

Collecting every match instead would refuse a valid multistage template the moment #387 lands,
turning a merge into a regression.

### D4 — The refusal lands beside the one that is already there

`_refuse_divergent_base_references(names, template_dir)` is called from `required_base_images`
and from `enforce_base_images`, the same two places that call `_refuse_uncoverable_templates`.

Both call sites, not one. fasrc/archi#381 shipped a refusal that lived only in
`required_base_images`, which has no production caller, so the deploy path went on silently —
the same fail-open, one function over. `enforce_base_images` is what `archi create` calls.

Placement inside `enforce_base_images` is load-bearing: after `names` is derived (`:576-578`)
because the check is scoped by D2, and before the reference loop (`:580`) so it precedes
`run_preflight` (`:602`) and therefore `remove_existing_deployment()` (`cli_main.py:294`). The
test asserts `probe.pulled == []`, which is how the ordering is held rather than described.

### D5 — The message carries the references and their templates

`base_references` alone cannot produce the message, because a reference on its own does not
tell an operator which file to edit. A private `_base_reference_sources` returns the
reference-to-templates mapping in first-seen order; `base_references` is its keys.

First-seen order, not sorted: `service_templates()` is already sorted, so first-seen is stable
across runs, and it puts the reference the preflight would have probed at the top — which is
the one the operator's other tooling has been reporting.

## Risks / Trade-offs

- **Over-refusal on a deployment that was working.** A split pin in an operator's installed
  templates now refuses where it previously deployed some services and failed others. That is
  the intended trade — the previous behaviour tore the deployment down first — but it is a
  behaviour change for anyone carrying local template edits. The message names both files and
  both references, so the repair is mechanical.
- **In-tree the guard is latent.** It refuses nothing today (measured: 1 distinct reference per
  base across 15 templates), so no test of the real directory changes. A guard that never fires
  in-tree is only as good as its unit fixtures, which is why the fixtures assert the message
  content and the pre-probe ordering, not just that an exception is raised.
- **Merge conflicts with two open PRs.** #387 and #388 both edit this module. Neither blocks
  this change and both interactions are handled by design (D1, D3), but whichever merges second
  will conflict textually and needs a human read, not an automatic resolution.
