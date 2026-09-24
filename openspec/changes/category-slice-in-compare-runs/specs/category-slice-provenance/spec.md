## ADDED Requirements

### Requirement: Exact two-sided McNemar test for paired binary outcomes
The system SHALL compute the two-sided exact McNemar p-value from discordant counts b and c as `min(1, 2 * sum(C(n, i) for i in 0..min(b, c)) / 2**n)` with `n = b + c`, and SHALL return 1.0 when `n = 0`.

#### Scenario: No discordant pairs
- **WHEN** b = 0 and c = 0
- **THEN** p = 1.0

#### Scenario: Matches the archi-bench-out reference
- **WHEN** b and c take each pair in a fixed grid from 0 to 20
- **THEN** p equals `mcnemar_exact(b, c)` from `fasrc/archi-bench-out` `feature_matrix/figures/extract_figure_data.py:67-74` exactly

#### Scenario: Symmetric
- **WHEN** b and c are swapped
- **THEN** p is unchanged

### Requirement: Per-arm source and completion tests against the baseline
The system SHALL report, for every non-baseline arm, a *source* test pairing per-question relative hits (any declared source matched) over questions with declared sources that are clean in both arms and whose canonical declared source lists are equal in both arms, and a *completion* test pairing status `ok` versus not `ok` over the common question set, each with b = questions where the baseline succeeds and the arm fails, c = questions where the arm succeeds and the baseline fails, n = b + c, p, and a direction of `arm better` when c > b, `arm worse` when b > c, else `no difference`.

#### Scenario: Direction of a significant result
- **WHEN** b = 2 and c = 12
- **THEN** the test reports `arm better` with the exact p for (2, 12)

#### Scenario: Gold sources changed between artifacts
- **WHEN** a question's canonical declared sources differ between the baseline and the arm
- **THEN** that question is left out of the source test and listed by name, and it stays in the completion test

#### Scenario: Relative hit on a two-source row
- **WHEN** a row declares two sources and only one matched
- **THEN** that row is a hit for the source test

#### Scenario: Degraded row counts for completion only
- **WHEN** a question is `ok` in the baseline and `degraded` in the arm
- **THEN** it is a discordant pair in the completion test and is not in the source test

#### Scenario: Descriptive figures beside the tests
- **WHEN** the report is rendered
- **THEN** it also shows strict source accuracy and the count of questions with no `tool_call` message per arm, labelled descriptive

### Requirement: Arm selectors resolve to one arm by label or recorded name
The system SHALL resolve every arm selector in `--baseline`, `--primary`, `--routes-on-category` and `--qa-run` first against the labels `compare_runs.py` prints (`<artifact-stem>` or `<artifact-stem>@<N>`) and otherwise against the arm's recorded `configuration.services.benchmarking.name`, SHALL fail with a usage error naming the candidates when a selector matches no arm or more than one, and SHALL print both the label and the recorded name for every arm in the report header.

#### Scenario: Sweep arm selected by name
- **WHEN** a three-arm sweep artifact's second arm records `services.benchmarking.name: fasrc-docs-r0a-category` and the operator passes `--routes-on-category fasrc-docs-r0a-category`
- **THEN** the rule applies to `<artifact-stem>@2`

#### Scenario: Unknown selector
- **WHEN** `--primary r0a=source` names no label and no recorded name
- **THEN** the run exits 1 and lists the available labels and names

#### Scenario: Two replicates share names
- **WHEN** two artifacts are compared and both carry an arm named `fasrc-docs-r0a-category`
- **THEN** a bare name is ambiguous and refused, and the `<stem>@<N>` label selects one

### Requirement: Primary and Holm-adjusted secondary tests
The system SHALL accept `--primary LABEL=source|completion` for each treatment arm, SHALL mark that test as the arm's primary with its raw p, and SHALL report the arm's other test as secondary with a Holm-adjusted p computed across all secondaries in the report.

#### Scenario: Two arms, two secondaries
- **WHEN** r0a's primary is source and r0b's primary is completion
- **THEN** r0a's completion p and r0b's source p are Holm-adjusted as a family of two, and the primaries are unadjusted

#### Scenario: No primary given
- **WHEN** an arm has no `--primary` entry
- **THEN** both of its tests are shown unadjusted and labelled "no pre-registered primary"

### Requirement: The slice reads only a snapshot bound to the run
The system SHALL build an arm's per-category slice only when four checks pass — the arm names a `category_map_file` that exists beside the artifact, the arm's corpus fingerprint is recorded and `corpus_unchanged_at_endpoints` is `true`, `category_map_unchanged_at_endpoints` is `true`, and `"sha256:" + sha256(file bytes)` equals `category_map_sha256_end` — SHALL build a per-category comparison between two arms only when both pass and their corpus fingerprints are equal, whatever `--corpus-differs-by-design` says, and SHALL otherwise report "no slice" with the first failing check named.

#### Scenario: Artifact without category keys
- **WHEN** an artifact predates `record-category-map-digest`
- **THEN** the arm reports "no slice: no snapshot" and every other report section is unchanged

#### Scenario: Map changed during the arm
- **WHEN** `category_map_unchanged_at_endpoints` is `false`
- **THEN** the arm reports "no slice: map changed between endpoints"

#### Scenario: A reading failed
- **WHEN** `category_map_unchanged_at_endpoints` is `null`
- **THEN** the arm reports "no slice: a map reading failed"

#### Scenario: Snapshot file replaced
- **WHEN** the file's hash differs from `category_map_sha256_end`
- **THEN** the arm reports "no slice: snapshot does not match the end digest"

#### Scenario: Pair on different corpora
- **WHEN** both arms pass the four checks but record different corpus fingerprints, and `--corpus-differs-by-design` is passed
- **THEN** the pair reports "no slice: arms scored different corpora", while the override still applies to the non-slice sections as today

### Requirement: Category ownership, coverage and the side lines
The system SHALL assign each bank row to the category of its first declared source after canonicalization with the shared URL rule, SHALL put a row whose first source has no category on an "uncategorized" line, SHALL put a row whose sources resolve to more than one category on a "cross-category" line while it keeps its owner, SHALL report a source URL absent from the snapshot or mapped to two categories as unresolved, and SHALL compute each category's coverage as the distinct gold articles over every declared source of every row.

#### Scenario: Two sources in different categories
- **WHEN** a row's first source is in category A and its second in category B
- **THEN** the row counts once, under A, for gold rows, completion and item pass rate; it is excluded from A's and B's source accuracy; it appears on the cross-category line; and its second article counts toward B's coverage

#### Scenario: Uncategorized first source
- **WHEN** a row's first source has an empty category and its second source has category B
- **THEN** the row is on the uncategorized line, is not moved to B, and its second article still counts toward B's coverage

#### Scenario: Overall figures unchanged
- **WHEN** the slice is built
- **THEN** the report's overall source accuracy and completion figures equal those computed without the slice

#### Scenario: Unresolved source
- **WHEN** a gold URL is not in the snapshot
- **THEN** it is listed as unresolved and the row is not dropped from the overall figures

### Requirement: Power is judged per metric
The system SHALL mark a category underpowered for a metric, with no verdict for that metric, when fewer than 3 distinct gold articles of that category are cited by the rows that metric attributes to the category.

#### Scenario: Powered for completion, underpowered for source
- **WHEN** a category owns rows over 3 articles and one of those articles is cited only by a cross-category row
- **THEN** the category is powered for completion and underpowered for source accuracy

### Requirement: A map mismatch voids comparisons with a map-reading arm
The system SHALL accept `--routes-on-category LABEL` (repeatable), SHALL treat an arm pair's maps as mismatched when any of the four readings (each arm's start and end) is missing or unavailable, when either arm's start and end differ, or when the two end digests differ, SHALL void a mismatched comparison that includes a named arm, and for any other mismatched pair SHALL drop only the per-category slice unless either side called `search_metadata_index` in its benchmark `messages` or in a joined QA run's `answers.jsonl` `tool_calls`, in which case it SHALL void the comparison.

#### Scenario: r0a against control with different maps
- **WHEN** r0a is named and its end digest differs from the control's
- **THEN** the r0a comparison is void and reports no numbers

#### Scenario: r0a reading failed
- **WHEN** r0a is named and its end digest is `<unavailable: …>`
- **THEN** the r0a comparison is void

#### Scenario: r0a start reading failed, end matches
- **WHEN** r0a is named, its start digest is `<unavailable: …>`, and its end digest equals the control's end digest
- **THEN** the r0a comparison is void

#### Scenario: r0b against control, no metadata search
- **WHEN** r0b is not named, the maps differ, and no trace on either side names `search_metadata_index`
- **THEN** the comparison stands and only the per-category slice for that pair is dropped

#### Scenario: Metadata search in a QA trace
- **WHEN** r0b is not named, the maps differ, and a joined QA row's `tool_calls` contains `name: search_metadata_index`
- **THEN** the comparison is void

#### Scenario: Legacy artifacts
- **WHEN** neither arm records `category_map_sha256_end`
- **THEN** no map rule applies and the comparison behaves as before

### Requirement: A QA run joins only with matching map readings
The system SHALL, for an arm that records `category_map_sha256_end`, join a `--qa-run` only if the run directory's `category_map_readings.json` holds `start` and `end` digests that are both usable and both equal to that arm's end digest, SHALL, for an arm that records `agent_md_sha256`, join it only if the QA run's recorded `agent_spec_sha256` identifies the same prompt, and SHALL otherwise refuse that QA run with the reason named.

#### Scenario: QA run of another prompt
- **WHEN** the arm records `agent_md_sha256` and the QA run's recorded `agent_spec_sha256` identifies a different prompt
- **THEN** the QA run is refused with "QA run used a different agent spec", naming both digests

#### Scenario: Map edited during the QA run
- **WHEN** the QA run's start equals the arm's end digest and its end differs
- **THEN** the QA run is refused with "map changed during the QA run"

#### Scenario: QA readings file missing
- **WHEN** the arm records `category_map_sha256_end` and the QA directory has no readings file
- **THEN** the QA run is refused with "no map readings for this QA run"

#### Scenario: Legacy arm
- **WHEN** the arm records no `category_map_sha256_end`
- **THEN** the QA run joins as it does today
