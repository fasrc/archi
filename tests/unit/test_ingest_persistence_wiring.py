"""Both ingest construction sites must build persistence via the shared factory.

The spec mandates UI uploads are not bypassed: ``DataManager`` (scheduled/startup)
and the uploader UI must construct their persistence through ``build_persistence`` so
the processing pipeline applies uniformly. Importing those modules in CI pulls heavy
deps (nltk, llama_index, langchain text splitters) that aren't installed, so the wiring
contract is asserted at the source level.

The assertions are AST-based (not a brittle whitespace-stripped grep): we parse each
module, locate the ``self.persistence`` assignment inside the relevant ``__init__``,
resolve through one level of local-variable indirection, and assert the value is a
call to ``build_persistence`` — while also asserting no bare ``PersistenceService(...)``
call constructs persistence in that ``__init__``. This catches regressions like
``svc = PersistenceService(...); self.persistence = svc`` that a string match misses.
"""

import ast
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def _find_class(tree: ast.Module, class_name: str) -> ast.ClassDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"class {class_name} not found")


def _find_init(class_node: ast.ClassDef) -> ast.FunctionDef:
    for item in class_node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            return item
    raise AssertionError(f"__init__ not found on {class_node.name}")


def _is_self_persistence_target(targets: List[ast.expr]) -> bool:
    for target in targets:
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "persistence"
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            return True
    return False


def _call_func_name(value: ast.expr) -> Optional[str]:
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
    return None


def _local_assignments(init: ast.FunctionDef) -> dict:
    """Map local variable name -> its assigned value node (last assignment wins)."""
    assignments = {}
    for node in ast.walk(init):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
    return assignments


def _resolve_persistence_call(init: ast.FunctionDef) -> Optional[str]:
    """Return the function name that constructs self.persistence, resolving one
    level of local-variable indirection."""
    locals_map = _local_assignments(init)
    for node in ast.walk(init):
        if isinstance(node, ast.Assign) and _is_self_persistence_target(node.targets):
            value = node.value
            # Direct call: self.persistence = build_persistence(...)
            name = _call_func_name(value)
            if name is not None:
                return name
            # Indirection: self.persistence = svc  (svc = build_persistence(...))
            if isinstance(value, ast.Name) and value.id in locals_map:
                return _call_func_name(locals_map[value.id])
    return None


def _constructs_bare_persistence_service(init: ast.FunctionDef) -> bool:
    """True if any PersistenceService(...) call appears inside the __init__."""
    for node in ast.walk(init):
        if _call_func_name(node) == "PersistenceService":
            return True
    return False


def _assert_wired_via_factory(module_path: str, class_name: str) -> None:
    source = _read(module_path)
    tree = ast.parse(source)
    assert (
        "from src.data_manager.collectors.processing import build_persistence" in source
    ), f"{module_path} must import build_persistence"

    init = _find_init(_find_class(tree, class_name))

    constructor = _resolve_persistence_call(init)
    assert constructor == "build_persistence", (
        f"{class_name}.__init__ must assign self.persistence from build_persistence(...), "
        f"got {constructor!r}"
    )
    assert not _constructs_bare_persistence_service(init), (
        f"{class_name}.__init__ must not construct a bare PersistenceService(...) — "
        "UI uploads would bypass the processing pipeline"
    )


def test_data_manager_constructs_persistence_via_factory():
    _assert_wired_via_factory("src/data_manager/data_manager.py", "DataManager")


def test_uploader_app_constructs_persistence_via_factory():
    _assert_wired_via_factory("src/interfaces/uploader_app/app.py", "FlaskAppWrapper")


def test_chat_app_document_utils_stays_unwrapped():
    """The delete-only chat-app path intentionally uses the bare service."""
    source = _read("src/interfaces/chat_app/document_utils.py")
    tree = ast.parse(source)

    found_bare = any(
        _call_func_name(node) == "PersistenceService" for node in ast.walk(tree)
    )
    found_factory = any(
        _call_func_name(node) == "build_persistence" for node in ast.walk(tree)
    )
    assert found_bare, "document_utils should still use the bare PersistenceService"
    assert not found_factory, "document_utils (delete-only) must not be wrapped"
