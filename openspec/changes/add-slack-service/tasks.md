## 1. Dependency (PR 1)

- [x] 1.1 Add `slack_sdk==3.45.0` to `pyproject.toml` `[project].dependencies` with a comment that names this change
- [x] 1.2 Add `slack_sdk==3.45.0` to `requirements/requirements-base.txt`

## 2. Pure seams, test-first (PR 1, `src/interfaces/slack_bot.py`)

- [x] 2.1 `thread_key(team_id, channel, thread_ts)` returns `slack:{team}:{channel}:{thread_ts}`; a test fails first
- [x] 2.2 `strip_mention(text)` removes every `<@U…>` mention and trims; a test fails first
- [x] 2.3 `should_answer(event, bot_user_id)` obeys the ignore rules of the spec; a test fails first
- [x] 2.4 `to_mrkdwn(markdown)` converts bold, links, and headings outside code fences and cuts to 39,000 characters; a test fails first
- [x] 2.5 `build_messages(replies, bot_user_id, question, before_ts, limit)` returns ordered `user`/`assistant` turns then the question; a test fails first
- [x] 2.6 `EventDeduper` answers each `(channel, ts)` once and keeps at most 1024 pairs; a test fails first

## 3. HTTP client to /v1 (PR 1)

- [x] 3.1 `ask_archi(session, base_url, model, messages, chat_id, token, timeout)` posts to `/v1/chat/completions`, sends the chat-ID header, sends `Authorization` only when a token is set, and raises `ArchiApiError` with the server's `error.message` on a non-200; a test fails first
- [x] 3.2 `wait_for_model(session, base_url, token, attempts, delay, sleep)` retries `GET /v1/models` and returns the first model ID, or raises an error that names `services.chat_app.openai_compat.enabled`; a test fails first

## 4. Socket Mode handling (PR 1)

- [x] 4.1 `SlackBot.handle_request(client, req)` acknowledges first, ignores non-`events_api` requests, filters and de-duplicates, then submits the answer work to the executor; a test fails first
- [x] 4.2 `SlackBot.answer(event)` posts the placeholder, reads the thread for a reply, calls `/v1`, and updates the placeholder with the mrkdwn answer, or with one error line on failure, without raising; a test fails first
- [x] 4.3 `SlackBot.from_config()` reads `services.slack` defaults, the three secrets, and the bot user ID from `auth.test`; logs no token; a test fails first
- [x] 4.4 `SlackBot.run()` resolves the model, registers the listener, connects, and blocks; a test with mocked clients fails first

## 5. Entry point and gate (PR 1)

- [x] 5.1 Add `src/bin/service_slack.py`: `setup_logging()`, `SlackBot.from_config().run()`, no start-up sleep
- [ ] 5.2 `bash scripts/gate.sh` passes (black, isort, unit tests, diff-cover at least 80%)
- [x] 5.3 `openspec validate add-slack-service --strict` passes
- [x] 5.4 Adversarial review of the branch; verify and address each finding (4 findings, all confirmed and fixed: dedupe lock, per-thread FIFO, reply pagination, own-message-only assistant role)
- [ ] 5.5 Open PR 1 to `fasrc/archi:dev`, body starts with `Part 1 of #510`

## 6. Service wiring (PR 2)

- [ ] 6.1 Register `slack` in `src/cli/service_registry.py` (`category="integration"`, `depends_on=["postgres", "chatbot"]` (the entry point reads its config from Postgres, like `service_chat.py`; the Compose block also waits for `config-seed`), `required_secrets=["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]`, `consumes_agent_specs=False`)
- [ ] 6.2 Add `"slack": ServiceState()` to `src/cli/utils/service_builder.py`
- [ ] 6.3 Add the `services.slack` block to `src/cli/templates/base-config.yaml`
- [ ] 6.4 Add the `slack` service to `src/cli/templates/base-compose.yaml` with `OTEL_SERVICE_NAME: archi-slack` and `depends_on: chatbot`
- [ ] 6.5 Add `src/cli/templates/dockerfiles/Dockerfile-slack` (no GPU variant)
- [ ] 6.6 Extend the render tests that list services
- [ ] 6.7 Document the Slack app manifest, secrets, config, and the access boundary in `docs/docs/services.md`, `docs/docs/user_guide.md`, and `docs/docs/configuration.md`
- [ ] 6.8 Gate, adversarial review, open PR 2 with `Closes #510`

## 7. Live check (human, after PR 2)

- [ ] 7.1 A workspace admin creates the Slack app from the issue #510 manifest and stores the tokens
- [ ] 7.2 Redeploy with `openai_compat.enabled: true` and `--services chatbot,slack`; a mention gets a threaded answer, and a follow-up reuses one `conversation_metadata` row
