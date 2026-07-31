"""Shared primitives for reading Epic Clarity HTML.

Both the SQLite indexer and the column parser need these. They live here, in a
module that imports nothing from either, so the two can depend on the same
helpers without depending on each other.
"""

from __future__ import annotations

from pathlib import Path

from bs4.element import Tag


def css_classes(tag: Tag) -> list[str]:
    value = tag.get("class")
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def owned_rows(table: Tag) -> list[Tag]:
    """Return only rows whose nearest containing table is table."""
    return [row for row in table.find_all("tr") if row.find_parent("table") is table]


def owned_cells(row: Tag) -> list[Tag]:
    """Return only cells whose nearest containing row is row."""
    return [cell for cell in row.find_all(["th", "td"]) if cell.find_parent("tr") is row]


def owned_cell_text(cell: Tag) -> str:
    """Read a cell without absorbing text from malformed descendant cells.

    Some legacy Epic pages omit closing ``td`` and ``tr`` tags. BeautifulSoup
    consequently nests every later row inside an earlier cell. Restricting
    strings to those whose nearest cell is this cell prevents cumulative,
    quadratic text expansion. The nested-table fallback preserves legitimate
    KeyValue cells whose value is represented by a one-cell child table.
    """
    owned_strings = [
        str(value)
        for value in cell.find_all(string=True)
        if value.find_parent(["td", "th"]) is cell
    ]
    direct_text = " ".join(" ".join(owned_strings).split()).strip()
    if direct_text:
        return direct_text
    nested_table = cell.find("table")
    return (
        " ".join(nested_table.get_text(" ", strip=True).split()).strip()
        if nested_table is not None
        else ""
    )


def normalized_source_path(html_path: Path, corpus_root: Path) -> str:
    """Return a stable, platform-independent corpus-relative source path."""
    try:
        relative_path = html_path.relative_to(corpus_root)
    except ValueError:
        relative_path = Path(html_path.name)
    return relative_path.as_posix()


def discover_html_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.rglob("*.html"))
    raise FileNotFoundError(input_path)
