## 1. Update the welcome copy

- [x] 1.1 In `src/interfaces/chat_app/static/chat.js`, replace the `messages-empty-subtitle` text "Ask me anything about CMS Computing Operations. I'm here to assist you." with "Ask me anything about FASRC research computing. I'm here to assist you."
- [x] 1.2 Leave the `messages-empty-title` ("How can I help you today?") unchanged.

## 2. Verify

- [x] 2.1 Run `git grep -n "CMS Computing Operations" -- 'src/*'` and confirm zero matches.
- [x] 2.2 Confirm the new subtitle string is present exactly once in `chat.js`.
- [ ] 2.3 (Deploy-time) After merge, redeploy via `g.sh` so the running container serves the rebaked asset, then load the chat app and confirm the new subtitle renders on an empty conversation.
