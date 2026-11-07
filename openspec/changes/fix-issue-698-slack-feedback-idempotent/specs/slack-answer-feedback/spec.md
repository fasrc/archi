## ADDED Requirements

### Requirement: A retried Helpful click SHALL store at most one like row
The bot MUST set the `feedback_ts` of a `like` row from the click's `actions[0].action_ts`, so that every retry of one click, after an ambiguous commit, a Slack envelope retry, or a bot restart, uses the same primary key `(message_id, feedback_ts)`.

#### Scenario: Ambiguous commit then a Slack retry
- **WHEN** a Helpful click on message ID 42 with `action_ts` `"1700000000.123456"` is stored, the commit result is lost so the insert reports a failure, and Slack retries the same click
- **THEN** the `feedback` table holds exactly one row for message ID 42 with `feedback_ts` `2023-11-14T22:13:20.123456Z`

#### Scenario: A retry after a bot restart
- **WHEN** the same click is stored, the bot restarts, and Slack delivers the click again
- **THEN** the `feedback` table holds exactly one row for that click

### Requirement: A retried form submission SHALL store at most one dislike row
The bot MUST carry the Not helpful click's `action_ts` into the modal's `private_metadata` as `<message_id>:<action_ts>`, and MUST set the `feedback_ts` of the `dislike` row from it, so that every retry of one submission uses the same primary key.

#### Scenario: The modal carries the click time
- **WHEN** a user clicks Not helpful on message ID 42 with `action_ts` `"1700000000.123456"`
- **THEN** the opened modal's `private_metadata` is `"42:1700000000.123456"`

#### Scenario: Ambiguous commit then a Slack retry of the submission
- **WHEN** a submission with `private_metadata` `"42:1700000000.123456"` is stored, the commit result is lost so the insert reports a failure, and Slack retries the same submission
- **THEN** the `feedback` table holds exactly one `dislike` row for message ID 42

### Requirement: A duplicate-key insert SHALL count as already stored
When an insert of a Slack feedback row fails with a unique violation (SQLSTATE `23505`), the store MUST return as a success, MUST NOT retry, and the bot MUST NOT log the failure as an error. Any other integrity error MUST still raise.

#### Scenario: The row is already there
- **WHEN** the store inserts a record whose `(message_id, feedback_ts)` is already stored
- **THEN** `insert()` returns without an exception and the bot posts the ephemeral thanks without an error log

#### Scenario: A foreign-key violation
- **WHEN** the insert fails with a foreign-key violation
- **THEN** `insert()` raises it

### Requirement: Feedback without a valid interaction time SHALL fall back to the current time
When a click has no valid `action_ts`, or a submission's `private_metadata` has no valid time part (for example `"42"` from a modal opened before this change), the bot MUST still store the row, with `feedback_ts` set to the current UTC time.

#### Scenario: A legacy modal
- **WHEN** a submission arrives with `private_metadata` `"42"`
- **THEN** exactly one `dislike` row for message ID 42 is stored with a current UTC `feedback_ts`

#### Scenario: An invalid action_ts
- **WHEN** a Helpful click on message ID 42 has `action_ts` `"abc"`
- **THEN** exactly one `like` row for message ID 42 is stored with a current UTC `feedback_ts`
