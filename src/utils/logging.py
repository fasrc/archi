import logging
import os

from src.utils import telemetry

# ignore debug logs from these modules, too verbose :)
ignore_debug_modules = [
    "urllib3.connectionpool",
    "filelock",
    "httpcore",
    "openai._base_client",
    "unstructured.trace",
    "chardet.charsetprober",
]

logging_verboseLevel = [
    logging.CRITICAL,
    logging.ERROR,
    logging.WARNING,
    logging.INFO,
    logging.DEBUG,
]


def setup_logging():
    """Configure the root logger, and start telemetry if an operator asked for it.

    This function is the first statement of every service entrypoint in
    ``src/bin/``, which makes it the one seam that reaches all nine processes.

    The order below is a fail-open requirement, not a preference. The plain format
    goes in first, so that a warning from ``init_telemetry()`` has somewhere to
    render. The trace-aware format goes in second, and only when the logging
    instrumentor actually installed the record fields it reads. Choosing that format
    from the *intent* to enable telemetry would leave ``%(otelTraceID)s`` in a
    handler whose records never carry it, and every later record would then die in
    formatting -- the warning about the failure first of all.
    """
    verbosity = int(os.getenv("VERBOSITY", 3))

    level = logging_verboseLevel[max(0, min(4, verbosity))]
    logging.basicConfig(level=level, format=telemetry.PLAIN_LOG_FORMAT, force=True)

    status = telemetry.init_telemetry()
    if status.log_correlation:
        logging.basicConfig(level=level, format=status.log_format, force=True)

    # need to override werkzeug which Flask uses
    logging.getLogger("werkzeug").setLevel(level)

    if verbosity == 4:
        for module in ignore_debug_modules:
            logging.getLogger(module).setLevel(logging_verboseLevel[3])


def setup_cli_logging(verbosity):

    if verbosity > 3:  # high verbose mode
        format_str = "[%(name)s] %(levelname)s: %(message)s"
    else:  # low verbose mode
        format_str = "[archi] %(message)s"
    level = logging_verboseLevel[max(0, min(4, verbosity))]
    logging.basicConfig(level=level, format=format_str, force=True)


def get_logger(name, verbosity=None):
    logger = logging.getLogger(name)
    if verbosity is not None:
        logger.setLevel(logging_verboseLevel[max(0, min(4, verbosity))])
    return logger
