## ADDED Requirements

### Requirement: Citation guidance is committed, not deployment-only

The agent SHALL receive its inline `[title](url)` citation instruction (cite the title+url shown
for each search result; never bare `[n]` indices; never a fabricated URL) from committed code as
a default appended to the system prompt, so the behavior does not depend solely on a
per-deployment (and possibly gitignored) prompt file. The default SHALL be applied to agents
wired with a catalog/vectorstore retrieval tool and SHALL NOT be applied to agents without one.

#### Scenario: A retrieval agent gets the citation default from committed code

- **WHEN** an agent that declares the vectorstore retriever tool (`search_vectorstore_hybrid` or
  `search_knowledge_base`) is resolved — even from a minimal prompt body that says nothing about
  citations
- **THEN** its resolved system prompt includes the committed citation guidance instructing inline
  `[title](url)` citation and forbidding bare `[n]` indices

#### Scenario: A non-retrieval agent does not get citation guidance

- **WHEN** an agent that declares no vectorstore retriever tool is resolved
- **THEN** the committed citation guidance is NOT appended

#### Scenario: Tracked examples model the hyperlink style

- **WHEN** the tracked example agents under `examples/agents/` are read
- **THEN** none instruct the model to cite by bare numeric result indices, and the citation
  style they model is the inline `[title](url)` Markdown link
