"""Parser protocol.

CSV is the only implementation for Phases 1-5; a CAMT.053 XML parser lands in
Phase 6 (NOTES.md 0.7). It exists as a protocol from day one so that parser
drops in without touching normalise or the loader.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol, runtime_checkable

RawRow = dict[str, str]


class ParseError(ValueError):
    """A source file could not be read as the declared format."""


@runtime_checkable
class StatementParser(Protocol):
    kind: str

    def parse(self, path: Path) -> Iterator[RawRow]: ...
