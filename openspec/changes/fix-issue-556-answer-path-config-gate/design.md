# Design — refuse arms whose answer-path configuration differs (G10)

Every anchor below was read at `0ddc96e1`, the tip of `origin/dev` this branch was cut
from, and every claim about current behaviour was measured there. Line numbers shift as you
edit; re-`grep` before you rely on one.

## D1. What the gate compares, and why absent is not null

The gate reads two **refused paths** from each arm's recorded configuration:

| Path | Rendered by `src/cli/templates/base-config.yaml` | Why it is answer-path |
|---|---|---|
| `services.chat_app.context_editing` | `:149-156` — emitted **only when declared**; the whole block passes through verbatim | the in-loop context bound; without it a long question overflows the window and is lost |
| `services.chat_app.recursion_limit` | `:147` — always rendered, default 50 | caps the agent's tool-call loop; a lower cap ends an answer early |

It compares **what the artifact recorded**, not what a value might mean at runtime. Values
are compared by their canonical JSON rendering (`json.dumps(value, sort_keys=True)`), so
`{"trigger": 32768, "keep": 1}` and `{"keep": 1, "trigger": 32768}` are equal and `50` and
`"50"` are not. A path that is missing from the recorded configuration is the sentinel
**absent**, and absent is distinct from a recorded `null`. Two reasons:

1. The issue's plan asks for exactly this ("a sentinel for absent that is distinct from
   `None`"), and the case that happened — `context_editing: null` in one arm, the bound in
   the other — must refuse whether the missing bound was spelled `null` or left out.
2. Runtime reads the two spellings through different paths. `context_budget.py:90` says
   an absent configuration yields the protective defaults, while the template comment at
   `base-config.yaml:150-154` says dropping the block leaves a self-hosted deployment with no
   declared window. Whether they coincide is a runtime question the comparison tool cannot
   answer from a finished file. Fail closed: the two arms recorded different things, the
   report prints both, and the operator who knows they are equivalent has the override.

The refusal names **every** differing path, not the first one found, so one run of the tool
shows the operator the full set.

## D2. Reading a dotted path from the recorded configuration

`Arm.raw` (`compare_runs.py:201`) is the arm entry as recorded, so
`arm.raw.get("configuration")` is the rendered configuration the harness wrote. Three states:

| Recorded | Meaning | Gate reads |
|---|---|---|
| a mapping (including `{}`) | recorded | each path walked with `path.split(".")`; a missing step, or a non-mapping intermediate, is **absent** |
| key missing, or not a mapping (`null`, a string) | not recorded | `not recorded` for that arm; see D6 |

`{}` is **recorded and empty**. That is the fixture default in
`tests/unit/test_compare_runs.py:152` (`"configuration": {}` for every arm), so all 99
existing tests see every refused path as absent in every arm — equal — and stay green with
no edit. Only tests that pass the new `configuration` argument exercise the gate.

The reader is a small module-level function, not a method on `Arm`, because it must also
run against a plain mapping in tests and it has no per-arm state. Name suggestion:
`recorded_setting(configuration, path)` returning the sentinel for absent; a companion
`_show_setting(value)` renders `absent`, `null`, or the canonical JSON, so the refusal and
the gate row print the same spelling.

## D3. The refusal

`raise CompareError(message, EXIT_GATE)` (`:131-134`, `EXIT_GATE = 2`), caught by `main`
(`:2780-2784`), which prints it to stderr and returns the code. This is the same channel G3
uses at `:585-591`, and the tests read it the same way (`capsys.readouterr().err`).

The message states, in order: the gate id `G10`; each differing path with every arm's
rendered value (`services.chat_app.context_editing: base={"keep": 1, "trigger": 32768},
treat=absent`); any arm whose configuration was not recorded; why it matters in one
sentence (the bound and the limit decide which questions the agent can finish, so the delta
would measure the configuration); and the way past it (`re-run with one answer-path
configuration, or pass --config-differs-by-design <path> if the difference is the
treatment`). Use the arm's printed label (`arm.label`), which is how the operator addresses
arms everywhere else in the report.

## D4. The override flag

`--config-differs-by-design`, `action="append"`, `default=[]`, `metavar="DOTTED.PATH"`,
declared directly after `--corpus-differs-by-design` (`:2437-2441`). It mirrors that flag's
contract (`corpus_gate`, `:551-606`):

- It waives **one named path**. Passing it twice waives two. A refused path that differs
  and is **not** named still refuses (acceptance criterion 3).
- When a named path differs, the gate row's status is
  `OVERRIDDEN (--config-differs-by-design <path>[, <path>])`, listing the paths that were
  both named **and** differed, and the detail prints each arm's value. A named path that
  does not differ changes nothing: the row stays `pass`. This matches `corpus_gate`, whose
  row reads `OVERRIDDEN` only when a difference was actually waived.
- It never hides a value. Both values appear in the row whether or not the path is waived.
- A value that is not one of the two refused paths is a **usage error** (`EXIT_USAGE`, 1)
  whose message lists the accepted paths. It can waive nothing, so accepting it silently
  would let a typo (`context-editing`, `services.chat_app.contextEditing`) look like an
  override while the gate still refuses — or worse, look like a pass when nothing differed.
  The tool already treats a self-contradicting flag combination as usage (the
  `--noise-floor` / measured-sigma collision at `:2740-2757`). `services.benchmarking.agent_md_file`
  is not accepted either: it is reported, never refused, so there is nothing to waive.

## D5. The reported path

`services.benchmarking.agent_md_file` is read with the same reader. When its value differs
across arms the gate **detail** gains
`services.benchmarking.agent_md_file differs (reported, not refused): base=..., treat=...`.
It never changes the status and never raises. When it is equal (or absent everywhere) the
detail says nothing about it — a prompt sweep is the normal case where it differs, and a
line that is always present would be noise on every non-sweep comparison.

## D6. An arm with no recorded configuration

Treated like G3 treats an unrecorded fingerprint (`:556-558`, `:573-578`): the artifact
cannot show what the arm ran with, so the comparison refuses, and the message says
`configuration was not recorded for <label>`. Two arms that both lack a configuration also
refuse — equal ignorance is not equality. The refusal is waived only when **every** refused
path is named in `--config-differs-by-design`, because an unrecorded configuration hides
every path at once. With both named, the row is `OVERRIDDEN (...)` and the detail prints
`not recorded` for that arm.

## D7. Noise replicates stay unchanged

`check_noise_replicates` (`:816-906`) already holds replicates to one
`config_version.digest` among themselves — a differing answer-path setting changes that
digest, so such a replicate is already refused with `EXIT_GATE`. The baseline is deliberately
outside that check (comment at `:897-900`): a floor measured on the baseline's own
configuration is legitimately a different digest from a treatment arm's, and it is compared
with the baseline through the arms gate, not the replicate check. The issue says to leave
this unchanged unless a test shows a gap; none does. G10 runs on `arms` only, in the gate
list in `run` (`:2694-2702`), after `divergence_gate`.

## D8. Spec delta direction

`ADDED Requirements` under `retrieval-benchmarking`. `openspec/specs/retrieval-benchmarking/spec.md`
holds four requirements (reproducible A/B, grounded banks, measurement protocol,
recommendation) and none for the comparison gates; the gates' requirements live only in
unarchived changes (`fix-issue-431-propagate-bank-difficulty`,
`fix-issue-441-failed-arm-not-relabelling`) that used this capability. `MODIFIED` would
fail validation against a requirement that does not exist in the archived spec.

## D9. Where the code goes, and the gate's name

- Constants near the gate function: `ANSWER_PATH_REFUSED = ("services.chat_app.context_editing", "services.chat_app.recursion_limit")`
  and `ANSWER_PATH_REPORTED = ("services.benchmarking.agent_md_file",)`. Tests import them
  so a future list change cannot silently desynchronise the tests from the tool.
- `answer_path_gate(arms, allow_differs)` beside `corpus_gate` and `divergence_gate`
  (`:551-640`). Returns the same row shape (`id`, `name`, `status`, `detail`) that
  `render_markdown` tabulates (`:1875-1882`) and `build_report` copies into
  `report["gates"]` (`:2383`), so the JSON report carries it with no further change.
- Gate id **`G10`**, name **`one answer path`**. `G2`–`G9` are in use; `G10` appears nowhere
  in the tool, the tests or the docs at `0ddc96e1` (grepped).
- One docstring bullet after the Procedure E bullet (`:30-35`), in the module's existing
  voice. The `Exit codes` line is unchanged.
- `docs/docs/interpreting_benchmark_results.md`, Procedure C (`:530-585`): the implements
  line (`:532`, `G3–G8` → `G3–G10`), one flag-table row after `--corpus-differs-by-design`
  (`:549`), one bullet in **What it refuses** after the G3 bullet (`:572-577`), and one
  clause in the Gap 1 paragraph (`:849-853`). No new heading, no new anchor.

## D10. Order of work, and the red

Measured at `0ddc96e1` with the probe recorded in `tasks.md` § Commands: two single-arm
artifacts differing only in `context_editing` (set vs absent) → `main` returns **0**; one
arm with no `configuration` key at all → **0**. Both must return **2**. Module baseline:
**99 passed in 0.67s**.

The work is staged so that each task's red is real when the task starts:

1. Fixture argument + the refusal and the `pass` row (no flag yet).
2. The flag and the `OVERRIDDEN` row, the unknown-path usage error.
3. The reported path, and the not-recorded refusal (two unrecorded arms pass after task 1,
   and nothing says `not recorded` yet — that is task 3's red).
4. Docstring and docs, then the acceptance oracle.
5. Push and PR.

Do not implement a later task's behaviour early; the later task's red must still fail when
it starts.
