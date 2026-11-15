"""Parse KiCad ERC and DRC JSON reports.

KiCad 8 and 9 write a top-level ``violations`` list. Older exports nested
them under sheets. Both are accepted.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def iter_violations(report: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(report.get("violations"), list):
        found.extend(item for item in report["violations"] if isinstance(item, dict))
    for sheet in report.get("sheets") or []:
        if isinstance(sheet, dict) and isinstance(sheet.get("violations"), list):
            found.extend(item for item in sheet["violations"] if isinstance(item, dict))
    # DRC also reports unconnected items beside the violation list.
    for key in ("unconnected_items", "unconnected"):
        extra = report.get(key)
        if isinstance(extra, list):
            for item in extra:
                if isinstance(item, dict):
                    item = dict(item)
                    item.setdefault("type", "unconnected")
                    item.setdefault("severity", item.get("severity") or "error")
                    found.append(item)
    return found


def violation_refs(item: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Pull component refs and a short description out of a violation."""
    refs: list[str] = []
    descriptions: list[str] = []
    items = item.get("items") or []
    if not isinstance(items, list):
        items = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        description = str(entry.get("description") or "").strip()
        if description:
            descriptions.append(description)
        # "Pin 12 [PA0] of component U1" / "Track of U1"
        for token in description.replace(",", " ").split():
            if len(token) >= 2 and token[0] in "URCJLQD" and any(ch.isdigit() for ch in token):
                cleaned = token.strip("[](),.")
                if cleaned not in refs:
                    refs.append(cleaned)
    return refs, descriptions
