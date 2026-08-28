# Design — a stall budget for the benchmark's ingestion wait

## Context

`wait_for_ingestion_completion` is 74 lines on `Benchmarker`
(`src/bin/service_benchmark.py:2092-2165`). It reads two env vars, builds four candidate
status URLs from `self.config["services"]["data_manager"]`, and then runs one loop:

```
while True:
    for status_url in status_urls:      # first answer wins; failures fall through
        ...urlopen -> json -> state/step
        completed -> return
        error     -> raise RuntimeError
        otherwise -> break
    if elapsed >= timeout_seconds: raise TimeoutError
    sleep(poll_interval_seconds)
```

The URL order is deliberate and stays as it is: in bridge mode the in-network hostname
resolves, and under `--hostmode` the container shares the host network so the data-manager
answers on `localhost` at its *internal* port. Falling past a dead candidate is the normal
path, not an error path — which is precisely why an exception left behind by one is
misleading rather than informative.

The method has no unit test today. Twelve test files already do
`import src.bin.service_benchmark`, so the module imports cleanly under `pytest`, and
`tests/unit/test_benchmark_ragas_dialect.py:78` establishes the house idiom for exercising
a `Benchmarker` method without its heavy `__init__`: `object.__new__(Benchmarker)`, then
set the attributes the method actually reads.

## Goals / Non-Goals

**Goals:**

- Stop aborting a wait on an ingest that is answering and non-terminal.
- Keep aborting promptly on an endpoint nothing can reach, and immediately on
  `state=error`.
- Make the timeout message describe the wait it ended, and never quote an exception that
  was not the reason it ended.
- Keep the diff inside one method, so the change is reviewable against the incident.

**Non-Goals:**

- Changing the default `7200`. The issue rules it out and it is the wrong lever: a bigger
  constant moves the wall without removing it.
- Detecting a wedged-but-answering data-manager. See "Accepted risk" below.
- Any change to the candidate URL list, the poll interval, or `Benchmarker.run`.
- Re-ingesting or touching the deployment. Every test uses a fake clock and a fake opener.

## Decisions

### The clock resets on a successful poll, not on a changed step

The abort decision is driven by `last_progress_time`, set to `time.monotonic()` whenever a
poll returns a parseable, non-terminal payload. The timeout fires when
`now - last_progress_time >= timeout_seconds`.

The obvious alternative — reset only when `step` *changes* — was rejected because it does
not fix the reported incident and the acceptance criteria forbid it. The incident's final
poll read `step=Updating vectorstore`, and nothing in the logs says when that step began;
a step-change clock could have killed the same run at the same moment. Acceptance
criterion 1 settles it: an endpoint returning `state=running` indefinitely, polled past the
budget, must not raise *while each poll keeps succeeding*. Any progress test finer than
"the endpoint answered" contradicts that sentence.

`start_time` is kept, because the message still reports total elapsed time. It no longer
takes part in the abort decision.

### An empty or unknown `state` counts as progress

The existing code treats anything that is neither `completed` nor `error` as non-terminal
and breaks out of the URL loop. That is unchanged: a payload with a missing `state` is a
reachable endpoint saying something the benchmark does not recognise, which is a reason to
keep waiting and not a reason to discard an ingest. Only a parse or transport failure
counts as a non-answer.

### `last_error` is cleared where the success is, not where the loop restarts

Clearing at the top of the outer iteration (`src/bin/service_benchmark.py:2120`) is what
exists and is not enough — it clears an error from the *previous* round while preserving
one from an earlier URL in the *current* round. The clear moves to the success path, one
statement before the `break`. After that, `last_error` is non-`None` at the abort point in
exactly one case: no candidate URL answered in the final round. That is the only case in
which quoting it is honest, so the message condition and the invariant become the same
thing.

### One message shape, assembled from what is known

Rather than two `raise` sites with different wordings, the abort builds one message:

- the idle interval that expired and the total elapsed wait, always;
- the last observed `state` and `step`, or an explicit statement that no status response
  was ever received;
- the last poll error, appended only when `last_error` is set.

An endpoint that answered for an hour and then died therefore reports both halves — the
last thing it said and the error that ended it — which is the case the current code
describes worst. Formatting seconds with `:.0f` keeps the message stable for assertions
without pinning it to a fake clock's exact float.

### Seams for the tests

The method keeps its signature, `wait_for_ingestion_completion(self)`. No test-only
parameters are added; the run path stays exactly as `Benchmarker.run` calls it.

Tests reach in through the module namespace, which is narrower than patching the standard
library:

- `monkeypatch.setattr(sb, "time", fake)` where `fake` supplies `monotonic()` and a
  `sleep()` that advances the fake clock by the poll interval. This rebinds the name
  `time` inside `src.bin.service_benchmark` only.
- `monkeypatch.setattr(sb, "url_request", fake)` where `fake.urlopen(url, timeout=...)`
  returns a context manager whose `read()` yields JSON bytes, or raises a real
  `urllib.error.URLError` for a candidate that is meant to be unreachable.
- `monkeypatch.setenv` for `BENCH_INGEST_WAIT_TIMEOUT` and `BENCH_INGEST_POLL_INTERVAL`,
  which the method reads on entry, so a test budget is a few simulated seconds.

Driving the clock from the fake `sleep` is what makes "polled past the configured budget"
cost no real time: the loop advances the clock itself, and a test that wants 100 rounds
gets them instantly. A test that returns `state=running` forever needs a stop condition of
its own — the fake opener counts calls and raises a sentinel exception after a fixed
number, and the test asserts that sentinel escaped rather than a `TimeoutError`. That is
the honest form of "does not raise" for a loop with no natural end.

## Accepted risk

A data-manager that answers `state=running` forever while doing nothing will now be waited
on forever, where today it is killed at two hours. This is the deliberate cost of the fix,
because the two situations are indistinguishable from the status payload the endpoint
returns: neither the step nor the state carries a counter, a file index, or a timestamp
that a caller could compare between polls. Choosing to abort one means aborting the other,
which is the defect being removed.

The mitigation is a follow-up, not a smaller version of this change: have the data-manager
report a monotonic progress counter in its status payload, then reset the stall clock on
that counter rather than on the fact of an answer. Until then the observable difference is
loud — the poll line logs `state` and `step` on every attempt — and the operator retains
the ability to abort the container.
