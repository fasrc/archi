## ADDED Requirements

### Requirement: Chat-app source removal deletes unreferenced parent nodes
The system SHALL, when the chat app removes a git repository or a Jira project, delete in the same transaction every `document_parent_nodes` row of the resource hashes that the removal selects that no `document_chunks` row in any collection references.

#### Scenario: A git repository is removed
- **WHEN** the chat app removes a git repository whose documents have hierarchical parent rows
- **THEN** after the removal no parent row of those documents remains that no chunk references

#### Scenario: A Jira project is removed
- **WHEN** the chat app Jira project removal selects documents that have hierarchical parent rows (the selector matches no row until issue #666 is fixed)
- **THEN** after the removal no parent row of those documents remains that no chunk references

#### Scenario: A parent that another collection references survives
- **WHEN** a parent row of a removed document is referenced by a chunk in another collection
- **THEN** the removal keeps that parent row

#### Scenario: The parent table does not exist
- **WHEN** the database has no `document_parent_nodes` table
- **THEN** the removal completes and deletes the chunks and soft-deletes the documents as before

#### Scenario: Nothing to remove
- **WHEN** the removal finds no resource hashes
- **THEN** the parent-node helper runs no SQL
