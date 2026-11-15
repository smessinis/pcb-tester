"""Load MCU pin databases and match them to schematic parts.

Each file in ``data/`` describes one orderable package. Matching is by
the part patterns in that file (``STM32H743VITx`` matches both the KiCad
symbol name and ``STM32H743VIT6``). Dropping in another JSON file is
enough to add an MCU; nothing else is hard-coded to the H743.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"


@dataclass(frozen=True)
class PinFunction:
    name: str
    af: int | None


@dataclass
class McuPin:
    number: str
    name: str
    role: str
    ft: bool
    functions: list[PinFunction]

    def function_names(self) -> set[str]:
        return {item.name for item in self.functions}

    def function(self, name: str) -> PinFunction | None:
        target = _compact(name)
        for item in self.functions:
            if _compact(item.name) == target:
                return item
        return None


@dataclass
class Mcu:
    id: str
    family: str
    package: str
    patterns: list[str]
    source: str
    pins: dict[str, McuPin]
    by_compact: dict[str, str] = field(default_factory=dict)

    def supports(self, function: str) -> bool:
        return _compact(function) in self.by_compact


def _compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def _load_file(path: Path) -> Mcu:
    raw = json.loads(path.read_text())
    pins: dict[str, McuPin] = {}
    catalog: dict[str, str] = {}
    for number, pin in raw["pins"].items():
        functions = [
            PinFunction(name=item["name"], af=item.get("af"))
            for item in pin.get("functions") or []
        ]
        for function in functions:
            catalog.setdefault(_compact(function.name), function.name)
        pins[str(number)] = McuPin(
            number=str(number),
            name=pin["name"],
            role=pin.get("role") or "io",
            ft=bool(pin.get("ft")),
            functions=functions,
        )
    return Mcu(
        id=raw["id"],
        family=raw.get("family") or "",
        package=raw.get("package") or "",
        patterns=list(raw.get("patterns") or [raw["id"]]),
        source=raw.get("source") or "",
        pins=pins,
        by_compact=catalog,
    )


@lru_cache(maxsize=1)
def load_mcus(directory: str | None = None) -> tuple[Mcu, ...]:
    folder = Path(directory) if directory else DATA_DIR
    mcus = []
    for path in sorted(folder.glob("*.json")):
        if path.name == "index.json":
            continue
        mcus.append(_load_file(path))
    return tuple(mcus)


def _pattern_score(pattern: str, text: str) -> int | None:
    """How specifically ``pattern`` matches a part string. None if it does not."""
    pat = _compact(pattern)
    blob = _compact(text)
    if not pat or not blob:
        return None
    if pat.endswith("X"):
        base = pat[:-1]
        if blob == pat:
            return len(pat) + 1
        if blob.startswith(base) and len(blob) == len(pat) and blob[-1].isdigit():
            return len(base)
        if blob == base:
            return len(base) - 1
        return None
    if blob == pat or blob.startswith(pat):
        # Require the next character, if any, not to start a different package.
        return len(pat)
    return None


def match_mcu(value: str, part: str = "", lib: str = "", mcus: tuple[Mcu, ...] | None = None) -> Mcu | None:
    catalog = mcus if mcus is not None else load_mcus()
    blob = " ".join(piece for piece in (value, part, lib) if piece)
    best: tuple[int, Mcu] | None = None
    for mcu in catalog:
        for pattern in mcu.patterns:
            score = _pattern_score(pattern, blob)
            if score is None:
                continue
            if best is None or score > best[0]:
                best = (score, mcu)
    return best[1] if best else None


def looks_like_stm32(value: str, part: str = "") -> bool:
    return bool(re.search(r"(?i)STM32[A-Z]\d", f"{value} {part}"))
