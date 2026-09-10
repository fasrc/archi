"""Boolean flags in base-config.yaml must honour an explicitly configured
``false`` value rather than replacing it with the template default, and must
render an explicit ``null`` as the row's default rather than the string 'None'.

Covers all 21 bug-class sites and all 7 null-class sites from the 28-conversion
table in tasks.md.  Three inputs per key: configured false → False, absent →
default, explicit None → default.
"""

import pytest
import yaml
from jinja2 import (
    ChainableUndefined,
    Environment,
    PackageLoader,
    nodes,
    select_autoescape,
)


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
# All 28 rows — (input_path, rendered_path, default, absent_kwargs)
# Listed in template line order.  absent_kwargs is merged into _render() for
# the absent-input case; it is {} for most rows, but non-empty for :43 where
# the enclosing {%- if %} block only renders when anchors is truthy.
# ---------------------------------------------------------------------------

_BUG_ROWS = [
    (
        "services.data_manager.enabled",
        "services.data_manager.enabled",
        True,
        {},
    ),
    (
        "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.encode_kwargs.normalize_embeddings",
        "data_manager.embedding_class_map.HuggingFaceEmbeddings.kwargs.encode_kwargs.normalize_embeddings",
        True,
        {},
    ),
    (
        "data_manager.reset_collection",
        "data_manager.reset_collection",
        True,
        {},
    ),
    (
        "data_manager.sources.local_files.enabled",
        "data_manager.sources.local_files.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.local_files.visible",
        "data_manager.sources.local_files.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.links.enabled",
        "data_manager.sources.links.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.links.visible",
        "data_manager.sources.links.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.links.html_scraper.reset_data",
        "data_manager.sources.links.html_scraper.reset_data",
        True,
        {},
    ),
    (
        "data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.kwargs.headless",
        "data_manager.sources.links.selenium_scraper.selenium_class_map.CERNSSOScraper.kwargs.headless",
        True,
        {},
    ),
    (
        "data_manager.sources.git.enabled",
        "data_manager.sources.git.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.git.visible",
        "data_manager.sources.git.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.sso.enabled",
        "data_manager.sources.sso.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.sso.visible",
        "data_manager.sources.sso.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.visible",
        "data_manager.sources.indico.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.sso_kwargs.headless",
        "data_manager.sources.indico.sso_kwargs.headless",
        True,
        {},
    ),
    (
        "data_manager.sources.jira.enabled",
        "data_manager.sources.jira.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.jira.visible",
        "data_manager.sources.jira.visible",
        True,
        {},
    ),
    (
        "data_manager.sources.redmine.enabled",
        "data_manager.sources.redmine.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.redmine.anonymize_data",
        "data_manager.sources.redmine.anonymize_data",
        True,
        {},
    ),
    (
        "data_manager.sources.elog.verify_ssl",
        "data_manager.sources.elog.verify_ssl",
        True,
        {},
    ),
    (
        "data_manager.processing.html_to_markdown.enabled",
        "data_manager.processing.html_to_markdown.enabled",
        True,
        {},
    ),
]

# Null-class rows: already honor an explicit false but render an explicit null
# as the string 'None'.  Same three-input structure as _BUG_ROWS.
_NULL_ROWS = [
    # :43 — inside {%- if services.benchmarking.anchors %}.  absent_kwargs
    # must make the enclosing if-block fire without supplying enabled itself.
    (
        "services.benchmarking.anchors.enabled",
        "services.benchmarking.anchors.enabled",
        True,
        {
            "services": {
                "benchmarking": {
                    "anchors": {"path": "examples/benchmarking/anchor_questions.json"}
                }
            }
        },
    ),
    (
        "services.chat_app.flask_debug_mode",
        "services.chat_app.flask_debug_mode",
        True,
        {},
    ),
    # :163 defaults to false — both absent and null should yield False.
    (
        "services.data_manager.auth.enabled",
        "services.data_manager.auth.enabled",
        False,
        {},
    ),
    (
        "services.grader_app.flask_debug_mode",
        "services.grader_app.flask_debug_mode",
        True,
        {},
    ),
    (
        "data_manager.retrievers.hierarchical_rerank.enabled",
        "data_manager.retrievers.hierarchical_rerank.enabled",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.use_sso",
        "data_manager.sources.indico.use_sso",
        True,
        {},
    ),
    (
        "data_manager.sources.indico.slide_conversion.enabled",
        "data_manager.sources.indico.slide_conversion.enabled",
        True,
        {},
    ),
]

_ALL_ROWS = _BUG_ROWS + _NULL_ROWS


@pytest.mark.parametrize("input_path,rendered_path,default,absent_kwargs", _ALL_ROWS)
def test_false_renders_false(input_path, rendered_path, default, absent_kwargs):
    cfg = _render(**_expand(input_path, False))
    assert _get(cfg, rendered_path) is False


@pytest.mark.parametrize("input_path,rendered_path,default,absent_kwargs", _ALL_ROWS)
def test_absent_renders_default(input_path, rendered_path, default, absent_kwargs):
    cfg = _render(**absent_kwargs)
    assert _get(cfg, rendered_path) is default


@pytest.mark.parametrize("input_path,rendered_path,default,absent_kwargs", _ALL_ROWS)
def test_none_renders_default(input_path, rendered_path, default, absent_kwargs):
    cfg = _render(**_expand(input_path, None))
    assert _get(cfg, rendered_path) is default


def test_numeric_zero_not_replaced_by_default():
    # base_source_depth: explicit 0 must render as integer 0, not the default 1, not False
    cfg = _render(**_expand("data_manager.sources.links.base_source_depth", 0))
    val = _get(cfg, "data_manager.sources.links.base_source_depth")
    assert val == 0
    assert val is not False
    # absent -> default 1
    cfg_absent = _render()
    assert _get(cfg_absent, "data_manager.sources.links.base_source_depth") == 1

    # sitemap.max_pages: explicit 0 must render as integer 0, not the default 20000, not False
    cfg2 = _render(**_expand("data_manager.sources.links.sitemap.max_pages", 0))
    val2 = _get(cfg2, "data_manager.sources.links.sitemap.max_pages")
    assert val2 == 0
    assert val2 is not False
    # absent -> default 20000
    cfg_absent2 = _render()
    assert _get(cfg_absent2, "data_manager.sources.links.sitemap.max_pages") == 20000


# ---------------------------------------------------------------------------
# AST guard — keeps the fixed pattern from regressing
# ---------------------------------------------------------------------------


def _get_template_source():
    env = Environment(loader=PackageLoader("src.cli"))
    source, _, _ = env.loader.get_source(env, "base-config.yaml")
    return source


def _walk_default_filters(source):
    """Walk the Jinja2 AST for ``| default(...)`` calls.

    Returns (bad_boolean_lines, truthy_non_bool_lines).
    bad_boolean_lines: lines where a boolean literal is used as default, except
        the permitted ``default(false, true)`` form.
    truthy_non_bool_lines: lines where a truthy non-boolean Const is used as
        default and true is the second argument.
    """
    ast = Environment().parse(source)
    bad_boolean = []
    truthy_non_bool = []

    for node in ast.find_all(nodes.Filter):
        if node.name != "default":
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        second_arg = node.args[1] if len(node.args) >= 2 else None

        if isinstance(first_arg, nodes.Const) and isinstance(first_arg.value, bool):
            is_allowed = (
                first_arg.value is False
                and second_arg is not None
                and isinstance(second_arg, nodes.Const)
                and second_arg.value is True
            )
            if not is_allowed:
                bad_boolean.append(node.lineno)
        elif (
            isinstance(first_arg, nodes.Const)
            and bool(first_arg.value)
            and not isinstance(first_arg.value, bool)
            and second_arg is not None
            and isinstance(second_arg, nodes.Const)
            and second_arg.value is True
        ):
            truthy_non_bool.append(node.lineno)

    return bad_boolean, truthy_non_bool


def test_guard_no_boolean_literal_defaults():
    """No default() call may use a boolean literal except the form default(false, true)."""
    source = _get_template_source()
    bad_lines, _ = _walk_default_filters(source)
    assert bad_lines == [], (
        f"Found default() calls with a boolean literal at lines {bad_lines}. "
        "Replace each with the ternary: "
        "{%- set v = <path> %}{{ v if v is defined and v is not none else <default> }}"
    )


_TRUTHY_NON_BOOL_BASELINE = 79


def test_guard_default_filter_baseline_count():
    """The count of default(<truthy non-bool literal>, true) calls is frozen at 79.

    To add a new boolean or numeric site, use the ternary form instead of default().
    To remove a converted site, lower the baseline in the same commit.
    """
    source = _get_template_source()
    _, truthy_non_bool = _walk_default_filters(source)
    count = len(truthy_non_bool)
    assert count == _TRUTHY_NON_BOOL_BASELINE, (
        f"Expected {_TRUTHY_NON_BOOL_BASELINE} default(<truthy non-bool literal>, true) "
        f"calls but found {count} (lines {truthy_non_bool}). "
        "New boolean/numeric site: use the ternary "
        "({%- set v = <path> %}{{ v if v is defined and v is not none else <default> }}) "
        "rather than default(). Removed a converted site: lower the baseline in this same commit."
    )
