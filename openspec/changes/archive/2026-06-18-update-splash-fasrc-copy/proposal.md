## Why

The chat app's empty-state splash still greets users with "Ask me anything about CMS Computing Operations" — copy inherited from the upstream CMS deployment. This deployment serves Harvard FASRC research-computing documentation, and the in-app assistant is already branded "FASRC", so the splash text is inaccurate and confusing to FASRC users.

## What Changes

- Update the empty-state subtitle in the chat UI from "Ask me anything about CMS Computing Operations. I'm here to assist you." to FASRC-oriented copy: "Ask me anything about FASRC research computing. I'm here to assist you."
- Keep the existing greeting title ("How can I help you today?") — it is already domain-neutral.
- No behavioral, API, or data changes; static front-end copy only.

## Capabilities

### New Capabilities
- `chat-welcome-copy`: Defines the wording requirements for the chat app's empty-state welcome screen (greeting title + subtitle), establishing that the copy reflects the FASRC research-computing domain rather than CMS Computing Operations.

### Modified Capabilities
<!-- None: no existing spec covers the chat welcome copy. -->

## Impact

- `src/interfaces/chat_app/static/chat.js` — the `messages-empty-subtitle` string in the empty-state markup.
- User-facing only; requires a redeploy (`g.sh`) for the running container to pick up the static-asset change, since the app serves a baked copy rather than the bind mount.
