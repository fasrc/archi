## ADDED Requirements

### Requirement: A sweep lock pins every arm's config and prompt
The system SHALL, on `lock_campaign.sh --sweep <sweep_dir> --manifest <yaml> --stack <name> --qa-dataset <json> --qa-profile <yaml>`, regenerate the arm configs from the manifest into a temporary directory with `generate_prompt_sweep.py` and refuse unless the set of stems and every file's bytes equal the sweep directory's, SHALL write `$FM_OUT/sweep-<name>.lock` holding the sha256 of the manifest, of every arm config keyed by its stem, of each config's `services.benchmarking.agent_md_file`, of the bank and anchors those configs name, of the QA dataset and evaluator profile, and the locked code tree, and SHALL refuse when the arm configs disagree on anything other than `agent_md_file`, `name` and `primary_metric`.

#### Scenario: Three-arm sweep
- **WHEN** the sweep directory holds control, r0a and r0b configs generated from one manifest
- **THEN** the lock holds three arm entries, each with its config and prompt sha256, and one bank and anchors sha256

#### Scenario: Hand-edited arm
- **WHEN** one generated config differs from the others in a retrieval setting
- **THEN** the lock is refused and the differing key is named

#### Scenario: Manifest edited after generation
- **WHEN** the manifest now points r0a at a different prompt than the generated r0a config names
- **THEN** the regenerated config differs from the one in the sweep directory and the lock is refused naming the stem

### Requirement: Every sweep step verifies the whole lock
The system SHALL provide one check, `fm_require_sweep_lock <stack>`, that every sweep step (`run_arm.sh --sweep` and `--rerun`, `qa_arm.sh --sweep`, `archive_run.sh --sweep`) runs before it acts, and that refuses unless the checkout's code tree equals the locked code tree with no tracked modification, every locked file (manifest, arm configs, arm prompts, bank, anchors, QA dataset, evaluator profile, prepared `preparation.jsonl`) still hashes to its locked sha256, and — for every step after the first run — the stack carries the lock's stamp.

#### Scenario: Source changed after locking
- **WHEN** a tracked file under `src/` changes after the sweep was locked
- **THEN** every sweep step is refused naming the locked and current code trees

#### Scenario: Bank edited after locking
- **WHEN** the bank file's content changes after the sweep was locked
- **THEN** the run is refused before `archi evaluate` touches the stack

#### Scenario: QA against another stack
- **WHEN** `qa_arm.sh --sweep` names a stack that lacks this lock's stamp
- **THEN** the QA run is refused before any QA phase starts

### Requirement: Gold atoms are prepared once per sweep, before the lock
The system SHALL, on `qa_prepare.sh --sweep <stack> --qa-dataset <json> --qa-profile <yaml>`, run `archi eval qa prepare` once into `$FM_OUT/qa/<stack>-prepared` before the sweep is locked, SHALL refuse when a sweep lock for that stack already exists, and `lock_campaign.sh --sweep` SHALL require that prepared directory and record the sha256 of its `preparation.jsonl`, so the lock is written once and never rewritten; every sweep QA run SHALL copy that prepared directory and run `archi eval qa run` and `score` on the copy after checking the copy's `preparation.jsonl` sha256 against the lock.

#### Scenario: Prepare after locking
- **WHEN** `qa_prepare.sh --sweep` runs for a stack that already has a sweep lock
- **THEN** it is refused, so the lock and every stack stamp taken from it stay valid

#### Scenario: Same atoms for every arm
- **WHEN** QA runs for control, r0a and r0b in both replicates complete
- **THEN** all six run directories carry a `preparation.jsonl` whose sha256 equals the lock's

#### Scenario: No prepared workspace
- **WHEN** `qa_arm.sh --sweep` runs before `qa_prepare.sh --sweep`
- **THEN** it is refused before any QA phase starts

### Requirement: The exemplar-disjointness result is recorded on its arm
The system SHALL, when locking a sweep, run the census's exemplar-disjointness check on every arm prompt that has a `## Worked examples` section, SHALL record per such arm the prompt sha256, pass or fail, the exemplar count and the similarity threshold in the sweep lock, and SHALL refuse the lock when any arm fails; `qa_arm.sh --sweep` and `archive_run.sh --sweep` SHALL refuse an arm whose prompt sha256 no longer equals the one its disjointness record names.

#### Scenario: r0b recorded
- **WHEN** the r0 sweep is locked
- **THEN** the r0b entry carries `disjointness: {prompt_sha256, passed: true, exemplars: 3, threshold: 0.5}` and control and r0a carry none

#### Scenario: Contaminated exemplar
- **WHEN** an r0b exemplar cites a bank source URL
- **THEN** the lock is refused and the URL is named

### Requirement: Run 1 of a sweep archives only with a passing census bound to its corpus
The system SHALL require `archive_run.sh --sweep … --run 1` to be given `--census <json>` from `category_census.py`, SHALL refuse unless that census passed every gate, its corpus fingerprint equals the fingerprint the artifact records, its category-map digest equals each arm's first usable map reading (`category_map_sha256_start`, else `category_map_sha256_end`), and its bank, anchors, routing-prompt and exemplar-prompt sha256 and similarity threshold equal the sweep lock's, and SHALL record the census file's sha256, fingerprint and map digest in every ledger row of that run. An arm with no usable map reading is archived with `census_map_bound: false` recorded, not refused, because #538 rule 2 makes a map failure cost only the slice; every other arm records `census_map_bound: true`.

#### Scenario: Start reading failed on one arm
- **WHEN** one arm's start reading is `<unavailable: …>` and its end digest equals the census map digest
- **THEN** the run is archived and that arm records `census_map_bound: true` against its end digest

#### Scenario: No usable reading on one arm
- **WHEN** one arm's start and end readings both failed
- **THEN** the run is archived, the pin is written, and that arm records `census_map_bound: false`

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
The system SHALL, on `run_arm.sh --sweep <sweep_dir> --stack <name>`, refuse unless the sweep lock exists and every config in the directory and every prompt file those configs name still match their locked sha256, run `archi evaluate --config-dir <sweep_dir> --name <name> --hostmode`, stamp the stack with the sweep lock and append a `ragas-start` ledger row; and on `--sweep … --rerun` SHALL refuse unless the stack carries the active sweep lock stamp, every config and prompt still matches the lock, Postgres and the data-manager are running, no benchmark is in flight, the corpus fingerprint equals the corpus pin and the live category-map digest equals the map pin, then recreate only the benchmark container and check both pins again.

#### Scenario: Prompt edited after locking
- **WHEN** r0b's prompt file changes after the sweep was locked
- **THEN** the run and the rerun are refused naming the prompt, before any container is touched

#### Scenario: Category map moved between replicates
- **WHEN** a document's category changes after run 1 was archived and before `--rerun`
- **THEN** the rerun is refused naming the pinned and live map digests

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
The system SHALL, on `qa_arm.sh --sweep <sweep_dir> --stack <name> --arm <stem>`, use that arm's `agent_md_file` as the agent spec after checking its sha256 against the sweep lock, check the QA dataset and evaluator profile sha256 against the sweep lock, check the corpus pin before and after the run, take a category-map reading immediately before and immediately after `archi eval qa` with the shared digest helper, and write both digests to `<qa_dir>/category_map_readings.json` as `{"start": …, "end": …}` whatever their values.

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
The system SHALL, on `archive_run.sh --sweep <sweep_dir> --stack <name> --run <N>`, accept an artifact whose arms' recorded `services.benchmarking.name` values match the locked stems one to one, refusing a duplicate, missing or extra name, SHALL refuse an artifact older than the stack's latest `ragas-start` row or already named by a ledger row, SHALL check every arm before copying any file or appending any ledger row, SHALL refuse unless every arm's corpus fingerprints are usable, equal at both endpoints and equal across arms, and every arm with a usable `category_map_sha256_end` names exactly one `category_map_file` that exists beside the artifact with `"sha256:" + sha256(file)` equal to that digest (an arm may name no file only when its end reading failed), SHALL copy the artifact, its `_report.md` and every `_category_map_<N>.tsv` it names, and SHALL append one `ragas` ledger row per arm with the arm stem, its prompt sha256 from the sweep lock, `category_map_sha256_start`, `category_map_sha256_end`, `category_map_unchanged_at_endpoints` and `category_map_file`, SHALL refuse unless every arm's recorded `agent_md_sha256` equals its locked prompt sha256, and SHALL write on run 1 the corpus pin and the category-map pin, which is the census's map digest (always present, because run 1 requires a passing census). An arm whose map changed or whose map reading failed is archived with that state recorded, not refused, because #538 rule 2 makes a map failure cost only the slice.

#### Scenario: Three arms archived
- **WHEN** a three-arm artifact with three snapshots is archived as run 1
- **THEN** three ledger rows, four copied files plus the report, and one pin are written

#### Scenario: Missing snapshot file
- **WHEN** an arm names a `category_map_file` that is not beside the artifact
- **THEN** the archive is refused and the arm is named

#### Scenario: Usable end digest but no snapshot named
- **WHEN** an arm records a usable `category_map_sha256_end` and a null `category_map_file`
- **THEN** the archive is refused naming the arm, because the snapshot cannot be recovered once the stack is gone

#### Scenario: Snapshot does not match its digest
- **WHEN** the third arm's snapshot file hashes to a value other than its `category_map_sha256_end`
- **THEN** the archive is refused naming that arm, and no file is copied and no ledger row is appended for any arm

#### Scenario: Map changed during an arm
- **WHEN** an arm records `category_map_unchanged_at_endpoints: false`
- **THEN** the arm is archived and its ledger row records `false`, so the missing slice is visible in the ledger

#### Scenario: Prompt changed between preflight and run
- **WHEN** an arm's recorded `agent_md_sha256` differs from its locked prompt sha256
- **THEN** the archive is refused naming the arm and both digests

#### Scenario: Map pin with no usable arm reading
- **WHEN** run 1's arms have no usable map reading and the census passed
- **THEN** the map pin is the census's map digest and the rerun checks the live map against it

#### Scenario: Arm names do not match the lock
- **WHEN** two artifact arms record the same name, or a locked stem has no arm
- **THEN** the archive is refused naming the duplicate or missing stem, before any file is copied

#### Scenario: Rerun produced no new artifact
- **WHEN** the newest artifact predates the latest `ragas-start` row, or a ledger row already names it
- **THEN** the archive is refused, so run 1 cannot be archived again as run 2

#### Scenario: Corpus moved between arms
- **WHEN** two arms record different end corpus fingerprints
- **THEN** the archive is refused

#### Scenario: Campaign behavior unchanged
- **WHEN** a single-arm feature-matrix artifact is archived without `--sweep`
- **THEN** it is archived exactly as before
