"""Regression: the base-config template must render the `sitemap` sub-block so a
`sitemap-` source's trust/bounds policy survives into the runtime config.

Crucially, a SCALAR `allowed_hosts: cdn.example.com` (a common hand-written YAML
form) must render as a single-element list, not one entry per character — a Jinja
`{% for host in "cdn.example.com" %}` iterates the string. If the template
char-explodes the scalar, the runtime coercion in `ScraperManager._expand_sitemaps`
never sees the original scalar and the intended host is silently dropped.
"""

import yaml
from jinja2 import ChainableUndefined, Environment, PackageLoader, select_autoescape


def _render(data_manager):
    # Mirror the env in src/cli/cli_main.py (PackageLoader + ChainableUndefined).
    env = Environment(
        loader=PackageLoader("src.cli"),
        autoescape=select_autoescape(),
        undefined=ChainableUndefined,
    )
    template = env.get_template("base-config.yaml")
    rendered = template.render(verbosity=0, data_manager=data_manager)
    return yaml.safe_load(rendered)


def _sitemap(cfg):
    return cfg["data_manager"]["sources"]["links"]["sitemap"]


def test_scalar_allowed_hosts_renders_as_single_host():
    cfg = _render(
        {"sources": {"links": {"sitemap": {"allowed_hosts": "cdn.example.com"}}}}
    )
    assert _sitemap(cfg)["allowed_hosts"] == ["cdn.example.com"]


def test_list_allowed_hosts_rendered():
    cfg = _render(
        {
            "sources": {
                "links": {
                    "sitemap": {"allowed_hosts": ["a.example.com", "b.example.com"]}
                }
            }
        }
    )
    assert _sitemap(cfg)["allowed_hosts"] == ["a.example.com", "b.example.com"]


def test_bounds_rendered_when_set():
    cfg = _render(
        {"sources": {"links": {"sitemap": {"min_pages": 200, "max_pages": 500}}}}
    )
    sm = _sitemap(cfg)
    assert sm["min_pages"] == 200
    assert sm["max_pages"] == 500


def test_defaults_when_unset():
    cfg = _render({})
    sm = _sitemap(cfg)
    assert sm["min_pages"] == 1
    assert sm["max_pages"] == 20000
    # allowed_hosts omitted/empty -> null or empty (falsy); runtime treats as [].
    assert not sm["allowed_hosts"]
