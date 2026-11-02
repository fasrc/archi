## MODIFIED Requirements

### Requirement: Only PASS and FAIL are verdicts
The Lynx parser SHALL read each top-level object in the model output as JSON plus the two forms that Lynx writes: a single-quoted string where a value goes, and a bare word as the value of `SCORE`. Valid JSON SHALL read as `json` reads it. The parser SHALL return a verdict only when the `SCORE` of that object is exactly `PASS` or `FAIL`, after `strip()` for a quoted string. Any other output SHALL be a parse error, and a parse error SHALL NOT count as PASS or as FAIL.

#### Scenario: Valid verdicts
- **WHEN** the output is `{"REASONING": "...", "SCORE": "PASS"}` or the same with `"FAIL"`
- **THEN** the verdict is PASS or FAIL

#### Scenario: Lynx's own form
- **WHEN** the output is `{"REASONING": ['The CONTEXT explicitly states that netscratch is not backed up.', 'The ANSWER claims that netscratch is backed up nightly to tape, which directly contradicts the information provided in the CONTEXT.', 'The CONTEXT also mentions that files older than 90 days are deleted, which implies that there is no backup process in place.'], "SCORE": FAIL}`
- **THEN** the verdict is FAIL, and the reasoning is the list of the three sentences

#### Scenario: An apostrophe in a reasoning item
- **WHEN** a reasoning item is `'The ANSWER's claim contradicts the CONTEXT.'`
- **THEN** the item reads as `The ANSWER's claim contradicts the CONTEXT.` and the verdict is kept

#### Scenario: Parse errors
- **WHEN** the `SCORE` is `"pass."` or `"pass"`, or the bare word `pass`, `PAS` or `PASS.`; the object has no `SCORE`; the output has no object; the object is cut off before its closing `}`; a bare word stands anywhere but as the value of `SCORE`; or two objects have different scores
- **THEN** the row is a parse error with no verdict

### Requirement: The benchmarking docs describe the Lynx job and its output
`docs/docs/benchmarking.md` SHALL have a "Lynx faithfulness (offline)" section that gives the job and the scorer invocation, says that `--out-dir` is the folder of the result JSON and is outside the bundle folder, names the four counts, and says that the PASS rate is over scored rows only and gates nothing. It SHALL give the Lynx 70B settings of the job, including `GPUS`, and the scorer's `--tensor-parallel-size` option. It SHALL show the output form that Lynx writes and say which outputs are parse errors.

#### Scenario: Docs section
- **WHEN** a reader opens the Lynx section
- **THEN** it holds the scorer invocation, the `--out-dir` rule, the four count names, and the statement that the PASS rate gates nothing

#### Scenario: Lynx 70B
- **WHEN** a reader wants to score with Lynx 70B
- **THEN** the section names the 70B model id, the lines to swap in the job, `GPUS`, and `--tensor-parallel-size`

#### Scenario: Output form
- **WHEN** a reader wants to know why a row is a parse error
- **THEN** the section shows Lynx's form (single-quoted reasoning items and a bare `PASS` or `FAIL`) and lists the outputs that are parse errors
