## ADDED Requirements

### Requirement: The benchmark abandons an ingestion wait on a stall, never on total runtime

The benchmark SHALL abort its wait for data-manager ingestion only after the configured budget passes with no successful, non-terminal status response, and SHALL NOT abort on total elapsed runtime.

The wait is the first statement of a benchmark run (`src/bin/service_benchmark.py:1805`),
so ending it wrongly discards every result the run would have produced and every file the
ingest already embedded.

Today the budget is measured from a `start_time` taken once before the loop, which makes a
slow ingest and a dead ingest the same event. They are not: on 2026-08-27 a run was killed
at exactly 7200s while its own last log line was a successful poll reading `state=running
step=Updating vectorstore`, and the ingest finished two minutes later. All 1433 status
checks in that run succeeded. Nothing was wrong except that the corpus was large and the
host was loaded.

Raising the budget does not satisfy this requirement. A larger constant moves the wall to a
different corpus size; the requirement is that a wait ends because the thing being waited
on stopped answering, not because it took a while.

"Non-terminal" means any parseable payload that is neither `completed` nor `error`. A
payload whose `state` is missing or unrecognised counts as an answer: it is a reachable
endpoint saying something the benchmark does not model, which is a reason to keep waiting.
Only a transport failure or an unparseable body counts as no answer.

The cost of this requirement is accepted and stated so nobody has to rediscover it: an
endpoint that reports `state=running` forever while making no progress is waited on
forever. The status payload carries no counter, index, or timestamp that a caller could
compare between polls, so a slow ingest and a wedged one are indistinguishable from
outside. Ending the wedged one means ending the slow one, which is the defect this
requirement removes.

#### Scenario: A healthy ingest is not killed for being slow

- **WHEN** the status endpoint answers every poll with `state=running` and the wait runs far past the configured budget
- **THEN** no `TimeoutError` is raised
- **AND** the wait is still running

#### Scenario: An endpoint that stops answering ends the wait

- **WHEN** no candidate status URL answers for one whole budget
- **THEN** a `TimeoutError` is raised
- **AND** it is raised one budget after the last successful poll, not one budget after the wait began

#### Scenario: An endpoint that dies mid-ingest ends the wait a budget later

- **WHEN** the endpoint answers `state=running` for several rounds and then stops answering entirely
- **THEN** a `TimeoutError` is raised once the budget passes with no successful poll
- **AND** the earlier successful polls do not extend the wait beyond that

#### Scenario: A completed ingest ends the wait immediately

- **WHEN** a poll returns `state=completed`
- **THEN** the wait returns
- **AND** no exception is raised

#### Scenario: A failed ingest still fails fast and names the step

- **WHEN** a poll returns `state=error` with a `step`
- **THEN** a `RuntimeError` is raised naming that step
- **AND** it is raised on that poll, without waiting for any budget

### Requirement: A timeout quotes a poll error only when nothing answered

The timeout error SHALL quote a poll exception only when no candidate status URL answered successfully in the final poll round.

Four candidate URLs are tried in order (`src/bin/service_benchmark.py:2107-2112`) and the
first answer wins. Falling past a dead candidate is the normal path: under `--hostmode` the
first candidate is always unreachable and the second always succeeds. An exception from a
candidate the code already fell past is therefore not evidence of anything.

Today `last_error` is cleared once per outer round
(`src/bin/service_benchmark.py:2120`) but never on the success that ends that round, so
every round leaves a stale exception behind and the timeout renders `Last error: <urlopen
error [Errno 111] Connection refused>`. The refusal is real. It is also irrelevant, and it
sends the operator looking for a network fault between the benchmark and the data-manager
that does not exist, while the actual cause — a deadline expiring against a healthy ingest
— goes unmentioned.

Clearing the error on the successful poll makes the message condition and the truth the
same thing: `last_error` survives to the abort point in exactly one case, which is the one
case where quoting it is honest.

#### Scenario: A fallen-past candidate is not blamed

- **WHEN** the first candidate URL raises a connection error, a later candidate answers, and the wait later times out
- **THEN** the timeout message does not contain that candidate's exception
- **AND** it does not contain the word `Last error`

#### Scenario: A genuinely unreachable endpoint is quoted

- **WHEN** every candidate URL raises for one whole budget
- **THEN** the timeout message quotes the last poll error

### Requirement: A timeout says what the wait was watching

The timeout error SHALL name the last observed ingestion state and step, the idle interval that expired, and the total elapsed wait.

An operator meeting this error is deciding one thing: was the ingest working, or was the
endpoint down? The current message answers neither. It reports a fixed budget and an
unrelated connection error, so the only way to tell the two apart is to read 1400 log lines
of poll output — which is what the 2026-08-27 incident cost.

The idle interval and the total elapsed wait are both reported because after this change
they differ, and the difference is the diagnosis: an idle interval far shorter than the
total says the endpoint answered for a long time and then stopped.

When no status response was ever received, the message SHALL say so explicitly rather than
render an empty state and step, so "the endpoint never came up" is not confused with "the
endpoint reported nothing".

#### Scenario: The last observed status appears in the message

- **WHEN** the endpoint reports `state=running step=Updating vectorstore` and then stops answering until the wait times out
- **THEN** the timeout message contains `running`
- **AND** it contains `Updating vectorstore`
- **AND** it reports both the idle interval and the total elapsed wait

#### Scenario: An endpoint that never answered says so

- **WHEN** no candidate URL ever answers and the wait times out
- **THEN** the timeout message states that no status response was received
- **AND** it does not report a `state` or `step` value

### Requirement: The ingestion wait's environment variables document the stall budget

`docs/docs/benchmarking.md` SHALL document `BENCH_INGEST_WAIT_TIMEOUT` and `BENCH_INGEST_POLL_INTERVAL` with their defaults, and SHALL describe the timeout as a stall budget.

Both variables are already listed with their defaults at `docs/docs/benchmarking.md:145-146`,
so the gap is not that they are undocumented. The gap is that the description states the
behaviour this change removes: "Seconds the benchmark container waits for the
data-manager's ingestion to complete before giving up" is the total-runtime reading, and an
operator sizing the value from that sentence would set it from their corpus size rather
than from how long they are willing to sit on silence.

`AGENTS.md:53-55` requires a user-facing behaviour change to update the docs in the same
change. This is one: the same variable, set to the same number, now means a different
thing.

#### Scenario: The documented meaning matches the behaviour

- **WHEN** a reader looks up `BENCH_INGEST_WAIT_TIMEOUT` in `docs/docs/benchmarking.md`
- **THEN** the entry gives the default `7200`
- **AND** it describes the value as the time allowed with no successful status response, not the total time allowed for the ingest
