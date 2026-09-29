## ADDED Requirements

### Requirement: The catalog document endpoint SHALL enforce a 6000-character ceiling on the text it returns
The uploader's `/api/catalog/document/<hash>` endpoint SHALL return at most 6000 characters of document text for any `max_chars` value, SHALL treat `0`, a negative value, or a malformed value as a request for the 6000-character ceiling rather than as "no limit", SHALL honour a smaller valid request exactly, and SHALL return at most 4000 characters when no `max_chars` is given.
The agent tool clamps its own request (#235), but the endpoint trusts whatever a caller sends: before this change `max_chars=0` returned the whole document and `max_chars=-5` returned all of the document except its last 5 characters. The server-side rule is the same `resolve_requested_chars` rule the agent tool uses, with the ceiling set to the agent fetch tool's `DEFAULT_FETCH_RESULT_CHARS` (6000), so there is one number to reason about.

#### Scenario: A request above the ceiling is clamped
- **WHEN** a caller requests a 10000-character document with `max_chars=999999`
- **THEN** the endpoint returns at most 6000 characters of text

#### Scenario: Zero does not disable truncation
- **WHEN** a caller requests a 10000-character document with `max_chars=0`
- **THEN** the endpoint returns at most 6000 characters of text, not the whole document

#### Scenario: A negative value never trims from the end
- **WHEN** a caller requests a 10000-character document with `max_chars=-5`
- **THEN** the endpoint returns at most 6000 characters of text
- **AND** the returned text is a prefix of the document, not the document without its tail

#### Scenario: A smaller valid request is honoured
- **WHEN** a caller requests a 10000-character document with `max_chars=100`
- **THEN** the endpoint returns exactly the first 100 characters

#### Scenario: No max_chars keeps the 4000 default
- **WHEN** a caller requests a 10000-character document with no `max_chars` parameter
- **THEN** the endpoint returns exactly the first 4000 characters
