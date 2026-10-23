#!/bin/python
import os

from flask import Flask

from src.interfaces.grader_app.app import FlaskAppWrapper
from src.utils.config_access import get_services_config
from src.utils.env import read_secret
from src.utils.logging import setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory
from src.utils.telemetry import instrument_flask_app

# set basicConfig for logging and get debug value for flask app
setup_logging()

os.environ["ANTHROPIC_API_KEY"] = read_secret("ANTHROPIC_API_KEY")
os.environ["OPENAI_API_KEY"] = read_secret("OPENAI_API_KEY")
os.environ["HUGGING_FACE_HUB_TOKEN"] = read_secret("HUGGING_FACE_HUB_TOKEN")

factory = PostgresServiceFactory.from_env(password_override=read_secret("PG_PASSWORD"))
PostgresServiceFactory.set_instance(factory)

grader_config = get_services_config().get("grader_app", {})

flask_app = Flask(
    __name__,
    template_folder=grader_config["template_folder"],
)
instrument_flask_app(flask_app)
app = FlaskAppWrapper(flask_app)

app.run(
    debug=grader_config["flask_debug_mode"],
    use_reloader=False,
    port=grader_config["port"],
    host=grader_config["host"],
)
