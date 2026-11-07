"""Rendering of query results as table, JSON, CSV, TSV or Markdown."""

from __future__ import annotations

import csv
import json
import shutil
import sys

Column = tuple[str, str, str | None]  # (key, header, format hint)


def _display(value, hint: str | None) -> str:
    """Format one cell for text output.

    Args:
        value: Raw cell value.
        hint: Format hint such as ``f3`` or ``int``.

    Returns:
        Display text.
    """
    if value is None:
        return "-"
    if hint == "f3" and isinstance(value, (int, float)):
        return f"{value:.3f}"
    if hint == "f1" and isinstance(value, (int, float)):
        return f"{value:.1f}"
    if hint == "int" and isinstance(value, (int, float)):
        return str(int(value))
    return str(value)


def _colorize(text: str, value) -> str:
    """Wrap negative numbers in an ANSI colour escape.

    Args:
        text: Already formatted cell text.
        value: Raw cell value.

    Returns:
        Possibly coloured text.
    """
    if isinstance(value, (int, float)) and value < 0:
        return f"\033[31m{text}\033[0m"
    return text


def render(rows: list[dict], columns: list[Column], fmt: str = "table",
           wide: bool = False, color: bool = False, out=None) -> None:
    """Write ``rows`` to ``out`` using the requested format."""
    out = out or sys.stdout
    if fmt == "json":
        payload = [{key: row.get(key) for key, _, _ in columns} for row in rows]
        json.dump(payload, out, ensure_ascii=False)
        out.write("\n")
        return
    if fmt in ("csv", "tsv"):
        writer = csv.writer(out, delimiter="," if fmt == "csv" else "\t",
                            lineterminator="\n")
        writer.writerow([header for _, header, _ in columns])
        for row in rows:
            writer.writerow(["" if row.get(key) is None else row.get(key)
                             for key, _, _ in columns])
        return
    if fmt == "md":
        out.write("| " + " | ".join(header for _, header, _ in columns) + " |\n")
        out.write("|" + "|".join("---" for _ in columns) + "|\n")
        for row in rows:
            cells = [_display(row.get(key), hint) for key, _, hint in columns]
            out.write("| " + " | ".join(cells) + " |\n")
        return

    widths = []
    for key, header, hint in columns:
        width = len(header)
        for row in rows:
            width = max(width, len(_display(row.get(key), hint)))
        widths.append(width)
    if not wide:
        terminal = shutil.get_terminal_size((120, 24)).columns
        separators = 2 * (len(columns) - 1)
        while sum(widths) + separators > terminal and max(widths) > 8:
            widest = widths.index(max(widths))
            widths[widest] -= 1
    header_cells = []
    for (_, header, _), width in zip(columns, widths):
        header_cells.append(header[:width].ljust(width))
    out.write("  ".join(header_cells).rstrip() + "\n")
    out.write("  ".join("-" * width for width in widths) + "\n")
    for row in rows:
        cells = []
        for (key, _, hint), width in zip(columns, widths):
            value = row.get(key)
            text = _display(value, hint)
            if len(text) > width:
                text = text[: max(0, width - 1)] + "…"
            text = text.rjust(width) if hint else text.ljust(width)
            cells.append(_colorize(text, value) if color else text)
        out.write("  ".join(cells).rstrip() + "\n")


def emit_notice(message: str) -> None:
    """Print a non-fatal notice to stderr."""
    print(message, file=sys.stderr)
