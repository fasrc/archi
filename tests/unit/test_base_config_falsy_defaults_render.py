"""Boolean flags in base-config.yaml must honour an explicitly configured
``false`` value rather than replacing it with the template default.

Covers all 21 bug-class sites from the 28-conversion table in tasks.md.
Three inputs per key: configured false → False, absent → True, explicit None → True.
"""

import pytest
import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape


def _render(**kwargs):
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, **kwargs)
    return yaml.safe_load(rendered)


def _expand(dotted_path, value):
    """Build the nested template-variable dict from a dotted input path.

    ``"a.b.c"`` with value ``v`` → ``{"a": {"b": {"c": v}}}``.
    The first component is the template variable name; the rest nest inside it.
    """
    parts = dotted_path.split(".")
    result = value
    for part in reversed(parts[1:]):
        result = {part: result}
    return {parts[0]: result}


def _get(cfg, dotted_path):
    """Read a dotted path from the rendered config dict."""
    node = cfg
    for part in dotted_path.split("."):
        node = node[part]
    return node


# ---------------------------------------------------------------------------
# All 21 bug-class rows — (input_path, rendered_path, default)
# Listed in template line order.
# ---------------------------------------------------------------------------

_BUG_ROWS = [
    (
        "services.data_manager.enabled",
        "services.data_manager.enabled",
        True,
    ),
    (
        "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.encode_kwargs.normalize_embeddings",
        "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.encode_kwargs.normalize_embeddings",
        True,
    ),
    (
        "data_manager.reset_collection",
        "data_manager.reset_collection",
        True,
    ),
    (
        "data_manager.sources.local_files.enabled",
        "data_manager.sources.local_files.enabled",
        True,
    ),
    (
        "data_manager.sources.local_files.visible",
        "data_manager.sources.local_files.visible",
        True,
    ),
    (
        "data_manager.sources.links.enabled",
        "data_manager.sources.links.enabled",
        True,
    ),
    (
        "data_manager.sources.links.visible",
        "data_manager.sources.links.visible",
        True,
    ),
    (
        "data_manager.sources.links.html_scraper.reset_data",
        "data_manager.sources.links.html_scraper.reset_data",
        True,
    ),
    (
        "data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.kwargs.headless",
        "data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.kwargs.headless",
        True,
    ),
    (
        "data_manager.sources.git.enabled",
        "data_manager.sources.git.enabled",
        True,
    ),
    (
        "data_manager.sources.git.visible",
        "data_manager.sources.git.visible",
        True,
    ),
    (
        "data_manager.sources.sso.enabled",
        "data_manager.sources.sso.enabled",
        True,
    ),
    (
        "data_manager.sources.sso.visible",
        "data_manager.sources.sso.visible",
        True,
    ),
    (
        "data_manager.sources.indico.visible",
        "data_manager.sources.indico.visible",
        True,
    ),
    (
        "data_manager.sources.indico.sso_kwargs.headless",
        "data_manager.sources.indico.sso_kwargs.headless",
        True,
    ),
    (
        "data_manager.sources.jira.enabled",
        "data_manager.sources.jira.enabled",
        True,
    ),
    (
        "data_manager.sources.jira.visible",
        "data_manager.sources.jira.visible",
        True,
    ),
    (
        "data_manager.sources.redmine.enabled",
        "data_manager.sources.redmine.enabled",
        True,
    ),
    (
        "data_manager.sources.redmine.anonymize_data",
        "data_manager.sources.redmine.anonymize_data",
        True,
    ),
    (
        "data_manager.sources.elog.verify_ssl",
        "data_manager.sources.elog.verify_ssl",
        True,
    ),
    (
        "data_manager.processing.html_to_markdown.enabled",
        "data_manager.processing.html_to_markdown.enabled",
        True,
    ),
]


@pytest.mark.parametrize("input_path,rendered_path,default", _BUG_ROWS)
def test_false_renders_false(input_path, rendered_path, default):
    cfg = _render(**_expand(input_path, False))
    assert _get(cfg, rendered_path) is False


@pytest.mark.parametrize("input_path,rendered_path,default", _BUG_ROWS)
def test_absent_renders_default(input_path, rendered_path, default):
    cfg = _render()
    assert _get(cfg, rendered_path) is default


@pytest.mark.parametrize("input_path,rendered_path,default", _BUG_ROWS)
def test_none_renders_default(input_path, rendered_path, default):
    cfg = _render(**_expand(input_path, None))
    assert _get(cfg, rendered_path) is default
