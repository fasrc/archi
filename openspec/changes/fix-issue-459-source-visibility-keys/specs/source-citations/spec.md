## ADDED Requirements

### Requirement: Source visibility applies to every configured source
The chat wrapper SHALL map each retrieved document to its `data_manager.sources` config key before it reads `visible`, so that `visible: false` hides citations for every source, not only the sources whose stored `source_type` equals the key.

The mapping SHALL be: `source_type` `web` with marker `scraper` `indico` → `indico`; `web`
with `scraper` `elog` → `elog`; any other `web` → `links`; `ticket` with
`ticket_provider` `jira` → `jira`; `ticket` with `ticket_provider` `redmine` → `redmine`;
`git`, `sso`, and `local_files` → the same name. A document with no `source_type`, any other
`source_type`, or a `ticket` with no known provider SHALL be visible. The stored
`source_type` values SHALL NOT change.

A document with no mapped key SHALL NOT produce an ERROR log record. A mapped key that is
absent from `sources_config` SHALL leave the document visible and log a WARNING.

#### Scenario: Hidden Jira source hides only Jira tickets

- **WHEN** `sources_config` has `jira.visible: false` and `redmine.visible: true`
- **AND** `get_top_sources` receives a document with `source_type` `ticket` and
  `ticket_provider` `jira`, and one with `ticket_provider` `redmine`
- **THEN** the result contains the redmine document and does not contain the jira document

#### Scenario: Hidden Indico source leaves plain links visible

- **WHEN** `sources_config` has `indico.visible: false`
- **THEN** a `web` document with `scraper` `indico` is not visible
- **AND** a `web` document with no `scraper` marker is visible

#### Scenario: Hidden links source hides plain web documents

- **WHEN** `sources_config` has `links.visible: false`
- **THEN** a `web` document with no `scraper` marker is not visible

#### Scenario: Uploaded file is visible without an error log

- **WHEN** a document has no `source_type`
- **THEN** the document is visible
- **AND** no ERROR log record is emitted
