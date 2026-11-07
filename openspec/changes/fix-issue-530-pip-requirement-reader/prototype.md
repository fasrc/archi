# Prototype reader for #530

Measured at `5564e016` (pip 26.1.2, packaging 26.2). Appended to a copy of
`tests/unit/test_base_image_dependency_compatibility.py`, it rebinds the module helpers and
passes all 254 existing tests. Reference only: see `design.md`.

```python
# ---- prototype #530 reader (appended; rebinds the module-level helpers) ----
import shlex as _shlex

from packaging.markers import InvalidMarker, Marker
from packaging.requirements import InvalidRequirement, Requirement

_STRONG_HASHES = ("sha256", "sha384", "sha512")
_LEADING_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_URL_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _break_args_options(line):
    tokens = line.split(" ")
    args, options = [], tokens[:]
    for token in tokens:
        if token.startswith("-"):
            break
        args.append(token)
        options.pop(0)
    return " ".join(args), " ".join(options)


def _options_ok(options):
    try:
        tokens = _shlex.split(options)
    except ValueError:
        return False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("-"):
            pass
        elif token.startswith("--hash=") or token == "--hash":
            if token == "--hash":
                i += 1
                if i >= len(tokens):
                    return False
                value = tokens[i]
            else:
                value = token[7:]
            algo, sep, _ = value.partition(":")
            if not sep or algo not in _STRONG_HASHES:
                return False
        elif token in ("-C", "--config-settings"):
            i += 1
            if i >= len(tokens) or "=" not in tokens[i]:
                return False
        elif token.startswith("--config-settings="):
            if "=" not in token[len("--config-settings="):]:
                return False
        elif token.startswith("-C"):
            if "=" not in token[2:]:
                return False
        else:
            return False
        i += 1
    return True


def _requirement_lines(text):
    for joined_line in _joined_lines(text):
        line = _COMMENT.sub("", joined_line).strip()
        if not line or line.startswith("-"):
            continue
        args, options = _break_args_options(line)
        if options and _options_ok(options):
            line = args.strip()
        if line:
            yield line


def _read(line):
    """(name, requirement-or-None, reported)."""
    fallback = _LEADING_NAME.match(line)
    name = _normalize_name(fallback.group(1)) if fallback else None
    if _ENVIRONMENT_SUBSTITUTION.search(line):
        return name, None, True
    if _VCS_OR_URL_REQUIREMENT.match(line):
        return name, None, True
    if line.startswith(".") or re.match(r"^[^;@]*[\\/]", line):
        return name, None, True
    head = line.split(";", 1)[0].strip()
    stripped = re.sub(r"\[[^\]]*\]$", "", head).strip()
    if re.search(rf"{_ARCHIVE_SUFFIX}$", stripped, re.IGNORECASE) and not re.match(
        r"^[A-Za-z0-9][A-Za-z0-9._-]*[ \t]*(?:\[[^\]]*\])?[ \t]*@[ \t]*(?:(?:git|hg|bzr|svn)\+|https?://|file:)", head, re.I):
        return name, None, True
    if _break_args_options(line)[1]:
        # pip: an option half that survived _requirement_lines was rejected there
        return name, None, True
    head, sep, marker_text = line.partition(";")
    try:
        req = Requirement(head.strip())
        marker = Marker(marker_text.strip()) if marker_text.strip() else None
    except (InvalidRequirement, InvalidMarker):
        return name, None, True
    req.marker = marker
    if req.url is not None and not _URL_SCHEME.match(req.url) or (
        req.url is not None and not re.match(r"(?:(?:git|hg|bzr|svn)\+|https?://|file:)", req.url, re.I)
    ):
        return _normalize_name(req.name), req, True
    return _normalize_name(req.name), req, False


def _pin(req):
    if req is None or req.url is not None:
        return None
    specs = list(req.specifier)
    if len(specs) == 1 and specs[0].operator in ("==", "===") and "*" not in specs[0].version:
        return specs[0].version
    return None


def _parse_pins(text):
    pins = {}
    for line in _requirement_lines(text):
        name, req, reported = _read(line)
        pin = None if reported else _pin(req)
        if name and pin:
            pins[name] = pin
    return pins


def _opaque_requirements(text):
    return [line for line in _requirement_lines(text) if _read(line)[2]]


def _spec_text(line, req):
    if req is None:
        return line[_LEADING_NAME.match(line).end():].split(";", 1)[0].strip() if _LEADING_NAME.match(line) else line
    rest = line[_LEADING_NAME.match(line).end():].split(";", 1)[0]
    return re.sub(r"^[ \t]*\[[^\]]*\]", "", rest).strip()


def _parse_requirements(text):
    found = {}
    for line in _requirement_lines(text):
        name, req, reported = _read(line)
        if name:
            found[name] = _spec_text(line, req)
    return found


def _declarations(text):
    found = {}
    for line in _requirement_lines(text):
        name, req, reported = _read(line)
        if name:
            found.setdefault(name, []).append(_spec_text(line, req))
    return found


def _conditional_protected(text):
    conditional = {}
    for line in _requirement_lines(text):
        name, req, reported = _read(line)
        if req is not None and req.marker is not None and name in PROTECTED_PACKAGES:
            conditional[name] = str(req.marker)
        elif req is None and ";" in line and name in PROTECTED_PACKAGES:
            conditional[name] = line.split(";", 1)[1].strip()
    return conditional
```
