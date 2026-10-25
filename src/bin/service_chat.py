#!/bin/python
import multiprocessing as mp
import os

from flask import Flask

from src.interfaces.chat_app.app import FlaskAppWrapper
from src.utils.config_access import get_full_config
from src.utils.env import read_secret
from src.utils.logging import setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.telemetry import instrument_flask_app


def build_gunicorn_options(chat_config):
    """Return the five gunicorn option keys for the chat service."""
    raw = chat_config.get("wsgi_threads")
    if raw is None:
        threads = 16
    else:
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
            raise ValueError(
                "services.chat_app.wsgi_threads must be an int >= 1, got {!r}".format(
                    raw
                )
            )
        threads = raw
    return {
        "bind": f"{chat_config['host']}:{chat_config['port']}",
        "workers": 1,
        "worker_class": "gthread",
        "threads": threads,
        "preload_app": False,
    }


def make_gunicorn_application(app_factory, options):
    """Build a gunicorn BaseApplication that serves app_factory()."""
    from gunicorn.app.base import BaseApplication

    class ChatApplication(BaseApplication):
        def __init__(self):
            self.app_factory = app_factory
            self.options = dict(options)
            super().__init__()

        def load_config(self):
            for key, value in self.options.items():
                self.cfg.set(key, value)

        def load(self):
            return self.app_factory()

    return ChatApplication()


def create_app():
    """Build and return the Flask app for the chat service."""
    os.environ["ANTHROPIC_API_KEY"] = read_secret("ANTHROPIC_API_KEY")
    os.environ["OPENAI_API_KEY"] = read_secret("OPENAI_API_KEY")
    os.environ["HUGGING_FACE_HUB_TOKEN"] = read_secret("HUGGING_FACE_HUB_TOKEN")

    factory = PostgresServiceFactory.from_env(
        password_override=read_secret("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)

    chat_config = get_full_config()["services"]["chat_app"]
    generate_script(chat_config)

    flask_app = Flask(
        __name__,
        template_folder=chat_config["template_folder"],
        static_folder=chat_config["static_folder"],
    )
    instrument_flask_app(flask_app)
    FlaskAppWrapper(flask_app)

    flask_app.debug = False
    return flask_app


def main():

    setup_logging()

    factory = PostgresServiceFactory.from_env(
        password_override=read_secret("PG_PASSWORD")
    )
    PostgresServiceFactory.set_instance(factory)
    try:
        chat_config = get_full_config()["services"]["chat_app"]
    finally:
        factory.close()
        PostgresServiceFactory.set_instance(None)
    print(
        f"Starting Chat Service with (host, port): ({chat_config['host']}, {chat_config['port']})"
    )
    print(
        f"Accessible externally at (host, port): ({chat_config['hostname']}, {chat_config['external_port']})"
    )
    options = build_gunicorn_options(chat_config)
    server = make_gunicorn_application(create_app, options)
    server.run()


def generate_script(chat_config):
    """
    This is not elegant but it creates the javascript file from the template using the config.yaml parameters
    """
    script_template = os.path.join(chat_config["static_folder"], "script.js-template")
    with open(script_template, "r") as f:
        template = f.read()

    filled_template = template.replace(
        "XX-NUM-RESPONSES-XX", str(chat_config["num_responses_until_feedback"])
    )
    filled_template = filled_template.replace(
        "XX-TRAINED_ON-XX", str(chat_config.get("trained_on", ""))
    )

    script_file = os.path.join(chat_config["static_folder"], "script.js")
    with open(script_file, "w") as f:
        f.write(filled_template)

    return


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)
    main()
