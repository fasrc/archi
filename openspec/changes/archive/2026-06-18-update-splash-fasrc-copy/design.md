## Context

The chat app renders an empty-state "welcome" screen when a conversation has no messages. The markup is built inline in `src/interfaces/chat_app/static/chat.js` (the `messages-empty-title` / `messages-empty-subtitle` elements). The subtitle currently reads "Ask me anything about CMS Computing Operations. I'm here to assist you.", inherited from the upstream CMS deployment. This is a static front-end string with no backend coupling.

## Goals / Non-Goals

**Goals:**
- Replace the CMS-specific subtitle with FASRC research-computing copy.
- Keep the change minimal and reviewable (single string).

**Non-Goals:**
- No redesign of the welcome screen layout or styling.
- No change to the greeting title, agent name, or any backend/prompt behavior.
- No new localization/config mechanism for UI copy.

## Decisions

- **New subtitle text:** "Ask me anything about FASRC research computing. I'm here to assist you."
  - Rationale: mirrors the existing sentence structure (domain phrase + "I'm here to assist you."), matches the in-app "FASRC" agent branding, and stays generic enough to cover the FASRC documentation corpus without over-promising specific topics.
  - Alternatives considered: "Ask me anything about Harvard FASRC research computing documentation." — rejected as wordier with no added clarity; "Ask me anything about FASRC docs." — rejected as too terse/jargony for a first-time user.
- **Keep the title "How can I help you today?"** — already domain-neutral; changing it adds churn without benefit.
- **Edit the string in source (`chat.js`), not a runtime config.** UI copy here is hard-coded; introducing a config indirection is out of scope for a one-line wording fix.

## Risks / Trade-offs

- [The running container serves a baked static copy, not the bind mount, so a plain restart won't show the new text] → Redeploy via `g.sh` after merge so the asset is rebaked.
- [A stale/minified duplicate of the string could exist elsewhere] → Verified via `git grep`: the phrase appears only in `chat.js`. The implementation step re-greps to confirm zero remaining occurrences.

## Open Questions

- None.
