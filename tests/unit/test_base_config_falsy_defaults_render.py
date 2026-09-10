"""Boolean flags in base-config.yaml must honour an explicitly configured
``false`` value rather than replacing it with the template default.

Covers three keys proven by issue #448:
  - data_manager.processing.html_to_markdown.enabled  (line :426)
  - data_manager.sources.git.enabled                  (line :345)
  - services.data_manager.enabled                     (line :164)
"""

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
# data_manager.processing.html_to_markdown.enabled  (:426)
# ---------------------------------------------------------------------------

_HTML_MD = "data_manager.processing.html_to_markdown.enabled"


def test_html_to_markdown_enabled_false_renders_false():
    cfg = _render(**_expand(_HTML_MD, False))
    assert _get(cfg, _HTML_MD) is False


def test_html_to_markdown_enabled_absent_renders_true():
    cfg = _render()
    assert _get(cfg, _HTML_MD) is True


def test_html_to_markdown_enabled_none_renders_true():
    cfg = _render(**_expand(_HTML_MD, None))
    assert _get(cfg, _HTML_MD) is True


# ---------------------------------------------------------------------------
# data_manager.sources.git.enabled  (:345)
# ---------------------------------------------------------------------------

_GIT = "data_manager.sources.git.enabled"


def test_git_enabled_false_renders_false():
    cfg = _render(**_expand(_GIT, False))
    assert _get(cfg, _GIT) is False


def test_git_enabled_absent_renders_true():
    cfg = _render()
    assert _get(cfg, _GIT) is True


def test_git_enabled_none_renders_true():
    cfg = _render(**_expand(_GIT, None))
    assert _get(cfg, _GIT) is True


# ---------------------------------------------------------------------------
# services.data_manager.enabled  (:164)
# ---------------------------------------------------------------------------

_SVC_DM = "services.data_manager.enabled"


def test_services_data_manager_enabled_false_renders_false():
    cfg = _render(**_expand(_SVC_DM, False))
    assert _get(cfg, _SVC_DM) is False


def test_services_data_manager_enabled_absent_renders_true():
    cfg = _render()
    assert _get(cfg, _SVC_DM) is True


def test_services_data_manager_enabled_none_renders_true():
    cfg = _render(**_expand(_SVC_DM, None))
    assert _get(cfg, _SVC_DM) is True
