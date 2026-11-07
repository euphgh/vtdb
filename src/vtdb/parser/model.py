"""Data model shared by the parser and its callers."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParseResult:
    """Summary of one parsed report.

    Attributes:
        meta: Header metadata such as ``Design`` and ``Design State``.
        counts: Row counts for paths, segments and pulse width checks.
        issues: ``(line, severity, code, message)`` tuples for import problems.
        design: Design Timing Summary values.
    """

    meta: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    issues: list[tuple[int | None, str, str, str]] = field(default_factory=list)
    design: dict[str, float | int | None] = field(default_factory=dict)
