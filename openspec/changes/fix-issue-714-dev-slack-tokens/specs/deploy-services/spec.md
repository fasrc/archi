## ADDED Requirements

### Requirement: The deploy README names the Slack tokens that the dev deployment needs
`deploy/scripts/README.md` SHALL name `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` in its "Secrets" bullet as required in the secrets env for the `dev` deployment only, and it SHALL NOT hold a value for either key.

The `dev` deployment runs the `slack` service, which declares both keys as required secrets, and `archi create` stops when a required secret is missing from the env file. No other deployment runs `slack`.

#### Scenario: Both Slack keys are named
- **WHEN** `grep -n "SLACK_BOT_TOKEN" deploy/scripts/README.md` and `grep -n "SLACK_APP_TOKEN" deploy/scripts/README.md` run
- **THEN** each command prints at least one line, and each printed line is in the "Secrets" bullet of the "## Notes" list

#### Scenario: The keys are required for dev only
- **WHEN** an operator reads the "Secrets" bullet
- **THEN** the bullet says that the `dev` deployment needs both keys because it runs the Slack service, and that no other deployment needs them

#### Scenario: No value is written
- **WHEN** `grep -nE "SLACK_(BOT|APP)_TOKEN *=" deploy/scripts/README.md` runs
- **THEN** it prints nothing

### Requirement: The deploy README says where the services list of each deployment lives
`deploy/scripts/README.md` SHALL say that the `SERVICES` case block in `lib.sh` sets the services list of each deployment, and SHALL give the list that each row runs, by variable name and never by `lib.sh` line number.

The rows on `origin/dev` `82850b3b` are `dev` → `chatbot,slack` and every other name → `chatbot`; `tests/unit/test_deploy_services_per_deployment.py` pins them.

#### Scenario: The services list is named
- **WHEN** `grep -n "SERVICES" deploy/scripts/README.md` runs
- **THEN** it prints at least one line, and that line is in a "Services" bullet of the "## Notes" list that also names `lib.sh`

#### Scenario: The rows match lib.sh
- **WHEN** an operator reads the "Services" bullet
- **THEN** it says that `dev` runs `chatbot,slack` and that every other deployment name runs `chatbot`, and that the list is not read from the environment or `host.env`

#### Scenario: No lib.sh line number is copied
- **WHEN** `grep -nE "lib\.sh:[0-9]" deploy/scripts/README.md` runs
- **THEN** it prints nothing
