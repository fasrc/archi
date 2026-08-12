## Context

`docs/docs/api_reference.md` cites `src/interfaces/chat_app/app.py` by line number in three
distinct syntactic forms, all resolving through the same 30 reference-link definitions.
Grounded at `origin/dev` 0a157cdc:

| Form | Count | Example (doc line) |
|---|---|---|
| Link definition | 30 | `[thinkgate]: https://…/app.py#L2418` (`:352`) |
| Canonical inline | 24 | `` [`app.py:2418`][thinkgate] `` (`:328`) |
| Abbreviated inline | 17 | `` [`:2412`][thinkgate] `` (`:282`) |
| Range inline | 1 | `` [`app.py:2435-2441`][chunkyield] `` (`:311`) |

72 occurrences, 33 distinct line numbers. The issue's plan describes only the canonical
inline form, so a guard built literally to its text would leave 18 of the 42 inline
citations unchecked — a guard that reports success while a third of the surface it exists to
protect is unwatched.

## Goals / Non-Goals

**Goals**
- Every anchored line number, in every form, is asserted against the content of `app.py`.
- A failure names the specific stale anchor and shows expected-vs-actual.
- The guard runs inside `bash scripts/gate.sh` with no new dependency and no import of `src`.

**Non-Goals**
- Changing the anchor format (symbolic anchors, text fragments, permalinks) — out of scope
  per the issue.
- Auto-repairing anchors. The test detects; a human (or the agent that shifted the lines)
  fixes.
- Extending the guard to line references in any other doc, or to files other than `app.py`.

## Decision 1 — the primary check is a content assertion on every line number, not an
inline-vs-definition comparison

The issue offers "Option A — content assertion" for the definitions and, separately, asks
that inline refs "match their link definition's line number". Applied as the *primary*
mechanism, that second rule is wrong, and grounding proves it.

The documentation uses the same tag to cite two different lines, by design. In the event
table (doc `:282`) the `thinking_start` row cites `` [`:2412`][thinkgate] `` — line 2412 is
`"type": "thinking_start",`, the event literal the row is about. In the flag table (doc
`:328`) the `include_tool_steps` row cites `` [`app.py:2418`][thinkgate] `` — line 2418 is
`if include_tool_steps:`, the gate that row is about. Both are correct; the link target is
the same region either way. The same pattern holds for `[thinkgate2]` (2424 event / 2432
gate) and `[stepemit]` (1773 event / 1767 gate).

An equality rule flags those three as failures. They are not drift — they are the doc being
more precise than its link definitions can express. "Fixing" them to satisfy the guard would
repoint three event-table rows at `if` statements and make the documentation worse.

So the primary guard is uniform: **for each (line number, expected substring) pair in a
checked-in table, the line at that number in `app.py` must contain the substring** — applied
to definitions and to every inline citation alike. This catches every drift an equality rule
would (a shifted definition and a shifted inline ref each break their own assertion) and 18
more that it would not, with no false positives.

## Decision 2 — inline/definition equality is kept, scoped to the canonical spelling

The issue's acceptance criterion #2 is explicit, and it is satisfiable exactly as written:
all 24 `` [`app.py:NNNN`][tag] `` refs equal their definition's line number today. So the
guard keeps that rule, restricted to that spelling, as a second and weaker assertion.

That gives the two spellings a meaning worth writing down, and the test docstring states it:

- `` [`app.py:NNNN`][tag] `` — canonical. The number MUST equal `[tag]`'s definition.
- `` [`:NNNN`][tag] `` — abbreviated. Cites a nearby line in the region `[tag]` points at;
  the number is NOT required to equal the definition, and is pinned by Decision 1 instead.

A future author who wants to cite a different line than the definition now has a spelling
that says so, rather than a rule to fight.

## Decision 3 — the range form pins both endpoints and anchors on one of them

One citation spans a range: `` [`app.py:2435-2441`][chunkyield] `` (doc `:311`), covering
`elif event_type == "text":` through `"type": "chunk",`. `[chunkyield]` is defined at L2441,
the range's end.

Rule: both endpoints get content assertions (Decision 1), and the tag's definition line must
equal one of the two endpoints. That keeps a range honest at both ends while not inventing a
constraint about which end a definition should anchor to — the sole example anchors on the
end, but a future range anchoring on its start is equally sensible.

## Decision 4 — the expected substrings are checked in, derived once, and short

Each table entry stores a distinctive substring of the line, not the whole line: whole-line
matching would break on a pure reformat (black rewrapping an argument list) that shifts
nothing semantically, producing failures with no drift behind them. Substrings are taken
from the current correct content — e.g. `2412 → '"type": "thinking_start"'`,
`2418 → "if include_tool_steps:"`.

Two entries need care because their substring is not unique in the file: `[thinkgate]` L2418
and `[thinkgate2]` L2432 are both `if include_tool_steps:`. That is fine — the assertion is
positional (read line N, check it contains S), not a search — but it means a shift that
moves one of them onto the other's line would pass. The 33 assertions are independent, so a
real shift moves many lines and lights up the rest; noted here as a known limit rather than
papered over.

## Decision 5 — `[ovrwarn2]` is repaired to L2143

`[ovrwarn2]` (doc `:183`) currently points at L2138, the last line of a three-line comment.
The event the citation documents — `yield {"type": "warning", "message": f"Using default
model: {e}"}` for a failed request-local pipeline build — begins at L2143. The repair points
it at 2143 (`yield {`), matching how `[ovrwarn]` at L2118 anchors its own warning yield.

This repair happens **before** the guard can go green, and the guard is what proves it: with
the table written from intent (the line must contain the warning yield) and the anchor still
at 2138, the test fails and names `[ovrwarn2]`. That is the change's own demonstration that
the mechanism works on a real defect, in addition to the synthetic blank-line demonstration
the issue asks for.

## Risks / Trade-offs

- **Added friction on every `app.py` PR.** Intended and stated in the issue. Mitigated by a
  failure message that names the tag, the doc line, and the expected substring, so the fix
  is mechanical.
- **The table is hand-maintained.** It has to be: deriving expected substrings from the
  anchors at test time would make the test tautological — it would read whatever line the
  anchor points at and assert that line equals itself, passing on any drift.
- **Substring choice is a judgement call.** Too short and it matches after a shift; too long
  and a reformat breaks it. The rule used: the most distinctive short fragment of the line —
  a dict key with its literal, a full `if` condition, a `def` signature's name.
- **Reformatting `app.py` will fail this guard.** Correct behaviour: a reformat that moves
  documented lines has invalidated the anchors, and they need updating.

## Migration Plan

Additive. No runtime code changes, no schema, no deployment step, nothing to roll back
beyond reverting the commit.

## Open Questions

None. The issue's design question was resolved with the operator on 2026-08-10 (Option A);
the two-spelling rule (Decisions 1–3) follows from grounding the plan against the actual
document and does not require a new decision — it is the reading under which the issue's own
acceptance criteria are all simultaneously satisfiable.
