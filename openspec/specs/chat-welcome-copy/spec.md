# chat-welcome-copy Specification

## Purpose
Defines the wording of the chat app's empty-state welcome screen (greeting title and subtitle), ensuring the copy reflects the FASRC research-computing domain rather than the upstream CMS Computing Operations deployment.

## Requirements
### Requirement: Empty-state welcome copy reflects the FASRC domain

The chat app's empty-state welcome screen SHALL present copy scoped to the FASRC research-computing domain. The subtitle MUST NOT reference "CMS Computing Operations". The subtitle SHALL read "Ask me anything about FASRC research computing. I'm here to assist you." The greeting title SHALL remain the domain-neutral "How can I help you today?".

#### Scenario: New conversation shows FASRC welcome subtitle

- **WHEN** a user opens the chat app with no messages in the current conversation
- **THEN** the welcome subtitle reads "Ask me anything about FASRC research computing. I'm here to assist you."
- **AND** the greeting title reads "How can I help you today?"

#### Scenario: No residual CMS wording

- **WHEN** the chat app front-end source is searched for "CMS Computing Operations"
- **THEN** no occurrences remain in the served UI

