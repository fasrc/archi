## 1. Access limits

- [x] 1.1 In `tests/unit/test_slack_access.py`, add failing tests for `is_internal_user`, `channel_allowed`, `parse_allowed_channels`, the notice, the feedback guard, the leave paths, and `from_config`. See them fail. Implement them in `src/interfaces/slack_bot.py` (design D1-D4).
- [x] 1.2 In `tests/unit/test_slack_service_wiring.py`, extend the rendered-config tests with the three keys. Add the keys to `src/cli/templates/base-config.yaml` (design D5).
- [x] 1.3 Document the keys, the scopes, and the `member_joined_channel` event in `docs/docs/services.md` and `docs/docs/configuration.md`.

## 2. Deployment (operator)

- [ ] 2.1 Add the five scopes and the `member_joined_channel` event to the Slack app, then reinstall it.
- [ ] 2.2 Set `internal_users_only: true` and `allowed_channels: [C0C8TS7H8BV, C0BTTAFE4ET]` in the deployment config, then redeploy.
