import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

INPUT_LIST_PREFIXES: Dict[str, str] = {
    "git-": "git",
    "sso-": "sso",
    "elog-": "elog",
    "indico-": "indico",
}


def split_prefixed_entry(entry: str) -> Optional[Tuple[str, str]]:
    """Return (source_name, url) if entry has a known prefix, else None."""
    for prefix, source in INPUT_LIST_PREFIXES.items():
        if entry.startswith(prefix):
            return (source, entry[len(prefix) :])
    return None


def is_elog_url(url: str) -> bool:
    """Return True if the URL path contains /elog/ or /elogs/."""
    path = urlparse(url).path.lower()
    return "/elog/" in path or "/elogs/" in path


def read_input_list_entries(path) -> List[str]:
    """Parse a list file, skipping blank lines and # comments; strip ,depth suffixes."""
    entries: List[str] = []
    with Path(path).open("r") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            entries.append(stripped.split(",")[0].strip())
    return entries


def _input_list_is_missing(path) -> bool:
    """True only when no file system entry exists at *path*.

    os.path.exists() returns False for every OSError, so it reports a list under
    an untraversable directory (PermissionError) as missing. Only "no such entry"
    (FileNotFoundError) and "a path component is a regular file"
    (NotADirectoryError) count as missing here; any other error is left to the
    warn-and-skip of get_enabled_sources() and staging (#636).
    """
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def _configured_input_list_entries(configs):
    """Yield ``(path, config file)`` for each usable ``input_lists`` entry.

    The shape rules match get_enabled_sources(): a config whose ``data_manager``,
    ``sources``, or ``links`` is not a mapping, or whose ``input_lists`` is not a
    list, yields nothing, and an entry that is not a str or an os.PathLike is
    skipped (it keeps the WARNING that get_enabled_sources() logs for it). The
    config file is the ``_config_path`` the operator gave on the command line.
    """
    for cfg in configs:
        config_path = cfg.get("_config_path", "<unknown config>")
        dm = cfg.get("data_manager")
        if not isinstance(dm, dict):
            continue
        sources = dm.get("sources")
        if not isinstance(sources, dict):
            continue
        links = sources.get("links")
        if not isinstance(links, dict):
            continue
        input_lists = links.get("input_lists")
        if not isinstance(input_lists, list):
            continue
        for entry in input_lists:
            if not isinstance(entry, (str, os.PathLike)):
                continue
            yield os.fspath(entry), config_path


def refuse_missing_input_lists(configs) -> None:
    """Raise ValueError naming every configured input list that does not exist.

    A relative path resolves from the current directory, as get_enabled_sources()
    and staging resolve it (fasrc/archi#617). A path that exists but cannot be
    read is not refused here: those callers warn about it and skip it (#636).
    That includes a list under a directory the user cannot traverse: os.stat
    raises PermissionError there, which is not "does not exist".
    """
    missing_pairs = []
    seen = set()
    for key in _configured_input_list_entries(configs):
        if key not in seen and _input_list_is_missing(key[0]):
            seen.add(key)
            missing_pairs.append(key)

    if not missing_pairs:
        return None

    cwd = os.getcwd()
    lines = "\n".join(
        f"  - {path} (config: {cfg_path})" for path, cfg_path in missing_pairs
    )
    raise ValueError(
        f"input_lists: these configured list files do not exist. A relative path"
        f" resolves from the current directory ({cwd}), not from the config"
        f" file's directory.\n{lines}\n"
        f"Correct each path, or use an absolute path, then run the command again."
    )


def refuse_input_lists_inside_deployment(configs, deployment_dir) -> None:
    """Raise ValueError naming every configured input list inside *deployment_dir*.

    A ``--force`` run of ``archi create`` or ``archi evaluate`` removes the whole
    deployment directory (remove_existing_deployment() ->
    DeploymentManager.delete_deployment()) before it stages the lists into
    ``<deployment dir>/weblists/``. A list that lives inside that directory, for
    example a ``weblists/`` copy that an earlier run staged, passes
    refuse_missing_input_lists() and is then deleted by the teardown; staging
    warns and skips it, and the new deployment starts without it
    (fasrc/archi#819).

    Both sides are resolved with Path.resolve(): symbolic links are followed,
    ``..`` is collapsed, and a relative path is taken from the current directory,
    as refuse_missing_input_lists() takes it. So a link or a relative path that
    lands inside the directory is refused too. ``~`` is not expanded, as nowhere
    else in the input-list path handling expands it. A sibling directory with the
    same prefix (``archi-smoke-2`` next to ``archi-smoke``) is outside.
    """
    resolved_dir = Path(deployment_dir).resolve()
    inside = []
    seen = set()
    for path_str, config_path in _configured_input_list_entries(configs):
        key = (path_str, config_path)
        if key in seen:
            continue
        seen.add(key)
        resolved = Path(path_str).resolve()
        if resolved.is_relative_to(resolved_dir):
            inside.append((path_str, str(resolved), config_path))

    if not inside:
        return None

    lines = []
    for path_str, resolved_str, config_path in inside:
        where = (
            path_str if resolved_str == path_str else f"{path_str} -> {resolved_str}"
        )
        lines.append(f"  - {where} (config: {config_path})")
    raise ValueError(
        f"input_lists: these configured list files are inside the deployment"
        f" directory {resolved_dir}. A --force run deletes that directory before"
        f" archi stages the lists, so they would be lost.\n" + "\n".join(lines) + "\n"
        f"Move each file outside that directory, point the config at the new"
        f" location, then run the command again."
    )


@dataclass
class SourceDefinition:
    """Definition for a data ingestion source."""

    name: str
    description: str
    required_secrets: List[str] = field(default_factory=list)
    required_config_fields: List[str] = field(default_factory=list)
    depends_on: List[str] = field(default_factory=list)


class SourceRegistry:
    """Registry that describes the supported data sources."""

    def __init__(self) -> None:
        self._sources: Dict[str, SourceDefinition] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(
            SourceDefinition(
                name="links",
                description="Basic HTTP/HTTPS link scraping from input lists",
                required_config_fields=[
                    "data_manager.sources.links.input_lists",
                ],
            )
        )
        self.register(
            SourceDefinition(
                name="sso",
                description="SSO-backed web crawling",
                required_secrets=["SSO_USERNAME", "SSO_PASSWORD"],
                required_config_fields=[
                    "data_manager.sources.links.selenium_scraper.selenium_class",
                ],
                depends_on=["links"],
            )
        )
        self.register(
            SourceDefinition(
                name="git",
                description="Git repository scraping for MkDocs-based documentation",
                required_secrets=["GIT_USERNAME", "GIT_TOKEN"],
                depends_on=["links"],
            )
        )
        self.register(
            SourceDefinition(
                name="jira",
                description="Jira issue tracking integration",
                required_secrets=["JIRA_PAT"],
                required_config_fields=[
                    "data_manager.sources.jira.url",
                    "data_manager.sources.jira.projects",
                ],
            )
        )
        self.register(
            SourceDefinition(
                name="redmine",
                description="Redmine ticket integration",
                required_secrets=[
                    "REDMINE_USER",
                    "REDMINE_PW",
                ],
                required_config_fields=[
                    "data_manager.sources.redmine.url",
                    "data_manager.sources.redmine.project",
                ],
            )
        )
        self.register(
            SourceDefinition(
                name="elog",
                description="ELOG logbook scraping",
                depends_on=["links"],
            )
        )
        self.register(
            SourceDefinition(
                name="indico",
                description="Indico event and meeting scraping",
                depends_on=["links"],
            )
        )

    def register(self, source_def: SourceDefinition) -> None:
        self._sources[source_def.name] = source_def

    def resolve_dependencies(self, sources: List[str]) -> List[str]:
        """Return sources including their dependency closure."""
        resolved_order: List[str] = []
        visited = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            source_def = self._sources.get(name)
            if not source_def:
                return
            for dep in source_def.depends_on:
                visit(dep)
            if name not in resolved_order:
                resolved_order.append(name)

        for src in sources:
            visit(src)

        return resolved_order

    def get(self, name: str) -> SourceDefinition:
        if name not in self._sources:
            raise KeyError(f"Unknown source: {name}")
        return self._sources[name]

    def names(self) -> List[str]:
        return sorted(self._sources.keys())

    def required_secrets(
        self, enabled_sources: List[str], configs: List[Dict] = None
    ) -> List[str]:
        secrets: List[str] = []
        resolved = self.resolve_dependencies(enabled_sources)
        for source in resolved:
            if source in self._sources:
                secrets.extend(self._sources[source].required_secrets)
        secrets.extend(self._conditional_secrets(resolved, configs or []))
        return sorted(set(secrets))

    @staticmethod
    def _conditional_secrets(
        resolved_sources: List[str], configs: List[Dict]
    ) -> List[str]:
        """Secrets that depend on config, not just whether a source is enabled."""
        extra: List[str] = []
        # Indico defaults use_sso=True; protected events need SSO credentials at
        # scrape time. Require them up front unless every config opts out, so the
        # create step fails fast instead of silently skipping authed events later.
        if "indico" in resolved_sources:
            use_sso = True
            for config in configs:
                if not isinstance(config, dict):
                    continue
                indico_cfg = (
                    config.get("data_manager", {}).get("sources", {}).get("indico", {})
                )
                if isinstance(indico_cfg, dict) and "use_sso" in indico_cfg:
                    use_sso = bool(indico_cfg["use_sso"])
            if use_sso:
                extra.extend(["SSO_USERNAME", "SSO_PASSWORD"])
        return extra

    def required_config_fields(self, enabled_sources: List[str]) -> List[str]:
        fields: List[str] = []
        for source in self.resolve_dependencies(enabled_sources):
            if source in self._sources:
                fields.extend(self._sources[source].required_config_fields)
        return sorted(set(fields))


source_registry = SourceRegistry()
