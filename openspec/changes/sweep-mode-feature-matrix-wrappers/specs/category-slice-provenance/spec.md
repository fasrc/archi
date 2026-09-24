## ADDED Requirements

### Requirement: A sweep lock pins every arm's config and prompt
The system SHALL, on `lock_campaign.sh --sweep <sweep_dir> --manifest <yaml> --stack <name>`, write `$FM_OUT/sweep-<name>.lock` holding the sha256 of the manifest, of every `*.yaml` in the sweep directory keyed by its stem, of each such config's `services.benchmarking.agent_md_file`, of the bank and anchors those configs name, and the locked code tree, and SHALL refuse when the arm configs disagree on anything other than `agent_md_file`, `name` and `primary_metric`.

#### Scenario: Three-arm sweep
- **WHEN** the sweep directory holds control, r0a and r0b configs generated from one manifest
- **THEN** the lock holds three arm entries, each with its config and prompt sha256, and one bank and anchors sha256

#### Scenario: Hand-edited arm
- **WHEN** one generated config differs from the others in a retrieval setting
- **THEN** the lock is refused and the differing key is named

### Requirement: The exemplar-disjointness result is recorded on its arm
The system SHALL, when locking a sweep, run the census's exemplar-disjointness check on every arm prompt that has a `## Worked examples` section, SHALL record per such arm the prompt sha256, pass or fail, the exemplar count and the similarity threshold in the sweep lock, and SHALL refuse the lock when any arm fails; `qa_arm.sh --sweep` and `archive_run.sh --sweep` SHALL refuse an arm whose prompt sha256 no longer equals the one its disjointness record names.

#### Scenario: r0b recorded
- **WHEN** the r0 sweep is locked
- **THEN** the r0b entry carries `disjointness: {prompt_sha256, passed: true, exemplars: 3, threshold: 0.5}` and control and r0a carry none

#### Scenario: Contaminated exemplar
- **WHEN** an r0b exemplar cites a bank source URL
- **THEN** the lock is refused and the URL is named

### Requirement: Run 1 of a sweep archives only with a passing census bound to its corpus
The system SHALL require `archive_run.sh --sweep … --run 1` to be given `--census <json>` from `category_census.py`, SHALL refuse unless that census passed every gate, its corpus fingerprint equals the fingerprint the artifact records, its category-map digest equals every arm's `category_map_sha256_start`, and its bank, anchors, routing-prompt and exemplar-prompt sha256 and similarity threshold equal the sweep lock's, and SHALL record the census file's sha256, fingerprint and map digest in every ledger row of that run.

#### Scenario: Census from another corpus
- **WHEN** the census JSON records a corpus fingerprint that differs from the artifact's
- **THEN** the archive is refused and both fingerprints are named

#### Scenario: Census from an older category map
- **WHEN** the census's corpus fingerprint matches but its category-map digest differs from the arms' start digest
- **THEN** the archive is refused and both digests are named

#### Scenario: Census run on other inputs
- **WHEN** the census was run with a routing prompt whose sha256 differs from r0a's in the sweep lock
- **THEN** the archive is refused and the input is named

#### Scenario: Failed census
- **WHEN** the census JSON records a failed gate
- **THEN** the archive is refused and the gate is named

### Requirement: Sweep replicates run on one stack between corpus checks
The system SHALL, on `run_arm.sh --sweep <sweep_dir> --stack <name>`, refuse unless the sweep lock exists and every config in the directory still matches it, run `archi evaluate --config-dir <sweep_dir> --name <name> --hostmode`, stamp the stack with the sweep lock and append a `ragas-start` ledger row; and on `--sweep … --rerun` SHALL refuse unless the stack carries the active sweep lock stamp, Postgres and the data-manager are running, no benchmark is in flight and the corpus fingerprint equals the pin, then recreate only the benchmark container and check the pin again.

#### Scenario: Second replicate
- **WHEN** replicate 1 was archived (writing the pin) and `--rerun` is invoked
- **THEN** only `benchmarking-<name>` is recreated and a `ragas-start` row with `"rerun": true` is appended

#### Scenario: Corpus moved before the rerun
- **WHEN** the live fingerprint differs from the pin
- **THEN** the rerun is refused before any container is touched

#### Scenario: Campaign behavior unchanged
- **WHEN** `run_arm.sh 03 --rerun` is invoked without `--sweep`
- **THEN** every existing check and ledger row is as before

### Requirement: QA runs per sweep arm record category-map readings
The system SHALL, on `qa_arm.sh --sweep <sweep_dir> --stack <name> --arm <stem>`, use that arm's `agent_md_file` as the agent spec after checking its sha256 against the sweep lock, check the corpus pin before and after the run, take a category-map reading immediately before and immediately after `archi eval qa` with the shared digest helper, and write both digests to `<qa_dir>/category_map_readings.json` as `{"start": …, "end": …}` whatever their values.

#### Scenario: Readings written
- **WHEN** a QA run completes
- **THEN** `category_map_readings.json` holds two `sha256:` digests and the ledger `qa` row carries both

#### Scenario: A reading fails
- **WHEN** the reading snippet cannot reach Postgres
- **THEN** the digest is recorded as `<unavailable: …>` and the QA run is still written, so the consumer refuses the join rather than the wrapper hiding it

#### Scenario: Wrong prompt
- **WHEN** the arm's prompt file no longer matches the sweep lock
- **THEN** the QA run is refused before `archi eval qa` starts

### Requirement: Multi-arm sweep artifacts archive per arm
The system SHALL, on `archive_run.sh --sweep <sweep_dir> --stack <name> --run <N>`, accept an artifact with one entry per sweep arm, SHALL refuse unless every arm's corpus fingerprints are usable, equal at both endpoints and equal across arms, SHALL copy the artifact, its `_report.md` and every `_category_map_<N>.tsv` it names, and SHALL append one `ragas` ledger row per arm with the arm stem, its prompt sha256 from the sweep lock, `category_map_sha256_start`, `category_map_sha256_end` and `category_map_file`, writing the corpus pin on run 1.

#### Scenario: Three arms archived
- **WHEN** a three-arm artifact with three snapshots is archived as run 1
- **THEN** three ledger rows, four copied files plus the report, and one pin are written

#### Scenario: Missing snapshot file
- **WHEN** an arm names a `category_map_file` that is not beside the artifact
- **THEN** the archive is refused and the arm is named

#### Scenario: Corpus moved between arms
- **WHEN** two arms record different end corpus fingerprints
- **THEN** the archive is refused

#### Scenario: Campaign behavior unchanged
- **WHEN** a single-arm feature-matrix artifact is archived without `--sweep`
- **THEN** it is archived exactly as before
