#!/bin/python
from src.interfaces.slack_bot import SlackBot
from src.utils.config_access import get_full_config
from src.utils.env import read_secret
from src.utils.logging import setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory


def main():
    setup_logging()

    # Set up shared Postgres services (expects config already in DB)
    factory = PostgresServiceFactory.from_env(
        password_override=read_secret("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)

    SlackBot.from_config(get_full_config()).run()


if __name__ == "__main__":
    main()
