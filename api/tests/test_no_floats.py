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


def _guarded_files() -> list[Path]:
    return sorted(p for d in GUARDED for p in (SRC / d).rglob("*.py"))


def test_the_guard_actually_covers_something():
    assert _guarded_files(), "guarded directories are empty -- the test proves nothing"


@pytest.mark.parametrize("path", _guarded_files(), ids=lambda p: p.name)
def test_no_floats_in_monetary_paths(path: Path):
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
