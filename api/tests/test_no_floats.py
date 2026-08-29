"""NON-NEGOTIABLE #8, enforced rather than asserted in a comment.

Any float in a monetary path is a bug. `money.py` is exempt because it exists
to reject floats, and `config.py` is exempt because confidence thresholds are
genuinely floats and are never money.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "recon"
GUARDED = ("db", "ingest", "matching", "seed", "graph", "retrieval", "llm")

# Weaviate returns relevance as a float. That conversion happens once, at the
# boundary, and the result is integer per mille from there on -- so this is the
# only module in the whole retrieval/matching path allowed a float literal.
# `test_no_float_touches_a_money_value` still applies to it.
SCORE_MODULES = {"weaviate_index.py"}


def _guarded_files() -> list[Path]:
    return sorted(p for d in GUARDED for p in (SRC / d).rglob("*.py"))


def test_the_guard_actually_covers_something():
    assert _guarded_files(), "guarded directories are empty -- the test proves nothing"


def _mentions_money(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and "minor" in sub.id.lower():
            return True
        if isinstance(sub, ast.Attribute) and "minor" in sub.attr.lower():
            return True
    return False


def _has_float(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, float):
            return True
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "float":
            return True
    return False


@pytest.mark.parametrize("path", _guarded_files(), ids=lambda p: p.name)
def test_no_float_touches_a_money_value(path: Path):
    """The invariant that matters, checked everywhere including score modules.

    A float in the same expression as a *_minor value means a monetary amount
    has been through binary floating point, which is exactly how a
    reconciliation system loses a cent per transaction.
    """
    tree = ast.parse(path.read_text())
    offences = [
        f"{path.name}:{node.lineno}: float meets a *_minor value"
        for node in ast.walk(tree)
        if isinstance(node, ast.stmt)
        and not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and _has_float(node)
        and _mentions_money(node)
    ]
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("path", _guarded_files(), ids=lambda p: p.name)
def test_no_floats_in_monetary_paths(path: Path):
    if path.name in SCORE_MODULES:
        pytest.skip("relevance scores are floats; covered by the *_minor test above")
    tree = ast.parse(path.read_text())
    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            offences.append(f"{path.name}:{node.lineno}: float literal {node.value!r}")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
        ):
            offences.append(f"{path.name}:{node.lineno}: float() call")
    assert not offences, "\n".join(offences)


@pytest.mark.parametrize("path", _guarded_files(), ids=lambda p: p.name)
def test_no_wall_clock_in_decision_logic(path: Path):
    """NON-NEGOTIABLE #4: date windows are relative to value_date, never today().

    A `today()` inside matching makes a run unreplayable tomorrow, and it fails
    silently -- the run still produces decisions, just different ones.
    """
    if path.parts[-2] in {"seed", "db"}:
        pytest.skip("generation and migration may reference the clock")
    tree = ast.parse(path.read_text())
    banned = {"today", "now", "utcnow", "time"}
    offences = [
        f"{path.name}:{node.lineno}: {node.attr}()"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in banned
    ]
    assert not offences, "\n".join(offences)
