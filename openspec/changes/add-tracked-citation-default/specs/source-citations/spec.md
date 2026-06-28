## ADDED Requirements

### Requirement: Citation guidance is committed, not deployment-only

The agent SHALL receive its inline `[title](url)` citation instruction (cite the title+url shown
for each search result; never bare `[n]` indices; never a fabricated URL) from committed code as
a default appended to the system prompt, so the behavior does not depend solely on a
per-deployment (and possibly gitignored) prompt file. The default SHALL be applied to agents
wired with a catalog/vectorstore retrieval tool and SHALL NOT be applied to agents without one.

#### Scenario: A retrieval agent gets the citation default from committed code

- **WHEN** an agent whose selected tools include a catalog/vectorstore retrieval tool
  (e.g. `search_local_files`, `search_metadata_index`, `search_vectorstore_hybrid`,
  `search_knowledge_base`) builds its system prompt — even from a minimal prompt body that says
  nothing about citations
- **THEN** the system prompt includes the committed citation guidance instructing inline
  `[title](url)` citation and forbidding bare `[n]` indices

#### Scenario: A non-retrieval agent does not get citation guidance

- **WHEN** an agent whose selected tools include no catalog/vectorstore retrieval tool builds
  its system prompt
- **THEN** the committed citation guidance is NOT appended

#### Scenario: Tracked examples model the hyperlink style

- **WHEN** the tracked example agents under `examples/agents/` are read
- **THEN** none instruct the model to cite by bare numeric result indices, and the citation
  style they model is the inline `[title](url)` Markdown link
