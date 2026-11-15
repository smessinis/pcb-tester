"""Objects a check is allowed to see."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pcbtester.kicad.netlist import Netlist


@dataclass
class ProjectContext:
    root: Path
    name: str
    pro: Path | None
    sch: Path | None
    pcb: Path | None
    work: Path
    netlist: Netlist | None = None
    netlist_error: str | None = None


@dataclass
class RunOptions:
    """Per-run choices that are not specific to one check."""

    spice_ids: list[str] | None = None
    stimuli: dict[str, float] = field(default_factory=dict)
