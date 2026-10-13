## ADDED Requirements

### Requirement: The non-streaming /v1 response SHALL return the stored message ID
A non-streaming `POST /v1/chat/completions` response MUST include a top-level field `archi_message_id` that is the integer `message_id` of the stream's `final` event, or `null` when the `final` event has no integer `message_id`. The streaming response does not change.

#### Scenario: A call with a chat ID header
- **WHEN** a client sends a non-streaming request with `X-OpenWebUI-Chat-Id` and the `final` event has `message_id` 42
- **THEN** the JSON response has `"archi_message_id": 42`

#### Scenario: No stored message ID
- **WHEN** a client sends a non-streaming request and the `final` event has no integer `message_id`
- **THEN** the JSON response has `"archi_message_id": null`

### Requirement: The Slack bot SHALL offer feedback buttons after a stored answer
After the bot delivers an answer whose `archi_message_id` is a positive integer and a feedback store is configured, it MUST post one thread message with blocks that contain the buttons `archi_feedback_helpful` and `archi_feedback_not_helpful`, each with `value` equal to the message ID. The answer message itself MUST NOT change.

#### Scenario: A stored answer
- **WHEN** `ask_archi()` returns message ID 42 and the delivery succeeds
- **THEN** the bot posts a thread message whose blocks contain both action IDs and both button values are `"42"`

#### Scenario: No stored message
- **WHEN** `ask_archi()` returns message ID `None`
- **THEN** the bot sends no `blocks`

#### Scenario: The error path
- **WHEN** the call to `/v1` fails and the bot posts the error text
- **THEN** the bot sends no `blocks`

#### Scenario: The prompt is not history
- **WHEN** a later question in the thread builds its history
- **THEN** the feedback prompt message is not sent as an assistant turn

### Requirement: A Helpful click SHALL store one like row
When a `block_actions` payload has action `archi_feedback_helpful` with a valid message ID, the bot MUST insert exactly one `feedback` row with `feedback='like'`, that `message_id`, and `NULL` flags and comment, and then send the clicking user an ephemeral thanks.

#### Scenario: One click
- **WHEN** a user clicks Helpful on the answer with message ID 42
- **THEN** exactly one row `(42, <ts>, 'like', NULL, NULL, NULL, NULL)` is inserted

#### Scenario: Two users rate one answer
- **WHEN** two users each click Helpful on the answer with message ID 42
- **THEN** two rows are inserted and no row is deleted

### Requirement: A Not helpful click SHALL open the feedback form
When a `block_actions` payload has action `archi_feedback_not_helpful` with a valid message ID, the bot MUST call `views.open` at once with the payload's `trigger_id` and a modal whose `private_metadata` is the message ID.

#### Scenario: Open the form
- **WHEN** a user clicks Not helpful on message ID 42 with trigger ID `T1`
- **THEN** `views_open` is called with `trigger_id="T1"` and a view whose `private_metadata` is `"42"`

### Requirement: A form submission SHALL store one dislike row
When a `view_submission` payload from the feedback form arrives, the bot MUST insert exactly one `feedback` row with `feedback='dislike'`, the message ID from `private_metadata`, the three flags as selected, and the comment capped at 2000 characters (an empty comment is `NULL`).

#### Scenario: Flags and comment
- **WHEN** a user submits the form for message ID 42 with incorrect and inappropriate checked and the comment "wrong partition"
- **THEN** exactly one row `(42, <ts>, 'dislike', 'wrong partition', true, false, true)` is inserted

### Requirement: Invalid feedback input SHALL be rejected without a crash
A button `value` or `private_metadata` that is not a positive integer MUST give no insert and no exception.

#### Scenario: A non-integer value
- **WHEN** a click arrives with `value` `"abc"` or a submission with `private_metadata` `"-1"`
- **THEN** no row is inserted and `handle_request()` does not raise

### Requirement: Feedback failures SHALL NOT change the answer
A failure of the feedback store, of the prompt post, or of a Slack feedback call MUST be logged and MUST NOT change or replace the delivered answer text.

#### Scenario: The prompt post fails
- **WHEN** posting the feedback prompt raises
- **THEN** the placeholder holds the answer text, not the error text

#### Scenario: The store fails on a click
- **WHEN** the store raises on a Helpful click
- **THEN** `handle_request()` logs the error and does not raise

#### Scenario: A failing store does not touch the answer
- **WHEN** the injected feedback store raises on every call and a question is answered with message ID 42
- **THEN** the last `chat_update` text for the placeholder is the answer text

#### Scenario: An empty comment
- **WHEN** a user submits the form with flags checked and the comment input sent as `"value": null`
- **THEN** exactly one `dislike` row is inserted with the flags and a `NULL` comment
