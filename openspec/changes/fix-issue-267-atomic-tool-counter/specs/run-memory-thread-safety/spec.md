## ADDED Requirements

### Requirement: The per-tool call counter is atomic under concurrent bumps

`RunMemory`'s per-tool call counter SHALL lose no updates when incremented concurrently from multiple threads, so its final value equals the number of increments performed.

The counter is incremented by `bump_tool_call_count`, which reads the current value and writes
back the value plus one. Those are two separate operations on a plain dict, and one instance of
`RunMemory` is shared across threads: `active_memory` is a ContextVar holding a reference to the
instance, and LangGraph's `ToolNode` sync path runs every tool call of one AI message through a
`ContextThreadPoolExecutor` that copies the calling context into each worker thread.

The increment and its write-back SHALL be performed while holding a lock private to the
`RunMemory` instance. The lock MUST NOT be held by the counter's callers instead: the invariant
belongs to the counter, and a caller-side lock would protect one call site while leaving every
other caller of the counter unprotected.

Reading the counter SHALL also hold that lock, so a reader cannot observe a partially applied
increment.

#### Scenario: Concurrent increments lose no updates

- **WHEN** several threads each increment the counter for the same tool name many times
- **THEN** the counter's final value equals the total number of increments performed
- **AND** it does so with the interpreter's thread switch interval forced low enough to expose
  the read-modify-write window

#### Scenario: The guarantee does not depend on interpreter implementation details

- **WHEN** the interpreter releases the GIL between the counter's read and its write
- **THEN** no increment is lost, because the read and the write are inside one critical section
- **AND** the guarantee therefore does not rest on CPython's current bytecode granularity, which
  is an implementation detail rather than a promise and is absent from a free-threaded build

### Requirement: Concurrent bumps return pairwise-distinct counts

Every concurrent call to `bump_tool_call_count` for one tool name SHALL receive a count that no other concurrent call receives, so at most one caller is admitted per count.

This is the property the per-turn tool budget actually relies on. `_consume_tool_budget` admits a
call when the returned count is less than or equal to the configured cap, so two callers handed
the same count are both admitted and the cap is exceeded by one. Distinctness is strictly
stronger than a correct final total: a run can finish with the right total while two callers were
both handed the same intermediate value.

Enforcing this requirement SHALL NOT change which call is refused, the cap lookup, or the wording
of the synthetic over-budget message returned to the model.

#### Scenario: No two concurrent callers are admitted on the same count

- **WHEN** many threads, released simultaneously, each bump the counter for the same tool name
  once
- **THEN** the returned counts are pairwise distinct
- **AND** consequently no more than `cap` callers can be admitted against a cap of `cap`

#### Scenario: The refusal behaviour is unchanged

- **WHEN** calls exceed the configured per-turn cap for a tool
- **THEN** the same synthetic over-budget message is returned, with unchanged wording
- **AND** the existing budget, ContextVar and memory-lifecycle test modules pass with no edits to
  those files

### Requirement: Composite mutations of the run's tool-call aggregates are atomic

Each `RunMemory` method that mutates a shared aggregate through more than one operation SHALL perform those operations while holding the instance's lock.

The methods in scope read a mapping and then write it back, or iterate it while assigning into
it: recording a tool call by id, recording a runtime tool input, resolving a pending tool input
and binding it to a call id, and attaching retrieved documents to a previously seen call. Each is
reachable from tool execution, so each can run on a tool-executor worker thread.

One such method iterates the tool-run mapping in Python while assigning into it. A concurrent
insert therefore raises `RuntimeError: dictionary changed size during iteration`, which is a live
exception on a tool path rather than a silent miscount, and its caller logs it at debug level and
continues — so the recorded tool input is dropped from the run's metadata with no error surface.
Holding the lock across the iteration SHALL remove that trigger.

The lock SHALL be re-entrant, because resolving a pending tool input calls the method that records
a tool call while already inside its own critical section, and a non-reentrant lock deadlocks
there. Re-entrancy also preserves the intent of that call chain, which is that popping a pending
input and binding it to a call id are one atomic unit.

Methods whose entire mutation is a single atomic operation, such as appending one item to a list,
SHALL NOT be locked. Locking them would be symmetry rather than correctness, and a single
`list.append` is atomic both under the GIL and under free-threaded CPython's per-object locking.

#### Scenario: A concurrent insert cannot break an in-progress iteration

- **WHEN** one thread records a runtime tool input while another thread inserts a new tool-call id
- **THEN** neither call raises `RuntimeError: dictionary changed size during iteration`
- **AND** the recorded tool input is not silently dropped

#### Scenario: Resolving a pending input does not deadlock

- **WHEN** a pending runtime input is resolved and bound to a tool call id, which enters a second
  locked method from inside the first
- **THEN** the call completes rather than blocking
- **AND** the pending input is popped exactly once and recorded against that call id

#### Scenario: Single-append recorders stay unlocked

- **WHEN** the change is reviewed for which methods acquired the lock
- **THEN** the document-event and note recorders are unlocked, each still being one `list.append`
- **AND** the reason is recorded, so the omission reads as a decision rather than an oversight

### Requirement: The atomicity guarantee is proven by tests that fail without it

Each test added for this capability SHALL fail on the unfixed implementation and pass on the fixed one, and SHALL force the interpreter's thread switch interval low enough that it cannot pass on the unfixed implementation by luck.

At the default switch interval the unfixed counter loses no updates across hundreds of thousands
of concurrent increments, so a test written at the default interval is green on broken code — the
worst available outcome, because it certifies the defect as fixed. The forcing therefore is part
of the assertion, not tuning.

The forced interval SHALL be restored on teardown to the value captured before the test changed
it, rather than to a hardcoded default, because the setting is process-global.

A probabilistic property SHALL be asserted over enough repetitions to be reliably red on the
unfixed implementation, and the observed detection rate at the chosen size SHALL be recorded. A
size at which the unfixed implementation passes is not a valid encoding of the property, however
faithfully it mirrors the operational shape: the three-concurrent-calls shape produces no
duplicate counts in two thousand trials and is therefore unusable as a test.

Where a property cannot be made to fail on the unfixed implementation, that fact SHALL be stated
in the pull request rather than handled by deleting the test.

#### Scenario: The new tests are red before the fix

- **WHEN** the source change is reverted and the new tests are run
- **THEN** they fail
- **AND** the failure is reproducible rather than intermittent at the chosen thread and trial
  counts

#### Scenario: A test cannot pass at the default switch interval by luck

- **WHEN** a reviewer removes the switch-interval forcing from the tests
- **THEN** the tests no longer distinguish the fixed implementation from the unfixed one
- **AND** the forcing is therefore documented in the test as load-bearing

#### Scenario: The process-global setting is restored

- **WHEN** the tests finish, whether they passed or failed
- **THEN** the thread switch interval equals the value it had before the tests ran
- **AND** no later test in the same process observes a lowered interval
