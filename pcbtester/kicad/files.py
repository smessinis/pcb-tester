"""Read just enough of a KiCad file to know which release wrote it.

KiCad refuses to open a file whose format version is newer than the
installed major. Running ERC, DRC and the netlist export anyway produces
the same "Failed to load schematic" six times. The header is enough to
say so once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# First s-expression (version YYYYMMDD) of each major, from KiCad's
# sch_file_versions.h and pcb_io_kicad_sexpr.h. A file at or after the
# date requires that major. generator_version, when present, wins.
_SCH_MAJORS = (
    (20260508, 11),
    (20250222, 10),
    (20240101, 9),
    (20231120, 8),
    (20230121, 7),
    (0, 6),
)
_PCB_MAJORS = (
    (20260410, 11),
    (20250210, 10),
    (20240201, 9),
    (20230410, 8),
    (20211226, 7),
    (0, 6),
)

_VERSION = re.compile(r"\(version\s+(\d+)\)")
_GENERATOR = re.compile(r'\(generator_version\s+"([^"]+)"\)')


@dataclass(frozen=True)
class FileStamp:
    format_version: int | None
    generator_version: str | None
    kicad_major: int | None

    @property
    def label(self) -> str:
        if self.generator_version:
            return self.generator_version
        if self.kicad_major is not None:
            return str(self.kicad_major)
        return "unknown"


def major_version(text: str | None) -> int | None:
    """``10.0.8`` and ``9.0.9`` both yield their leading number."""
    if not text:
        return None
    match = re.search(r"(\d+)", text)
    if not match:
        return None
    return int(match.group(1))


def _major_for_format(kind: str, format_version: int | None) -> int | None:
    if format_version is None:
        return None
    table = _SCH_MAJORS if kind == "sch" else _PCB_MAJORS
    for threshold, major in table:
        if format_version >= threshold:
            return major
    return None


def read_stamp(path: Path, kind: str) -> FileStamp:
    """Header only. The version tokens are in the first few lines."""
    try:
        with path.open("rb") as handle:
            blob = handle.read(16384)
    except OSError:
        return FileStamp(None, None, None)
    text = blob.decode("utf-8", errors="replace")
    version_match = _VERSION.search(text)
    generator_match = _GENERATOR.search(text)
    format_version = int(version_match.group(1)) if version_match else None
    generator_version = generator_match.group(1) if generator_match else None
    major = major_version(generator_version) or _major_for_format(kind, format_version)
    return FileStamp(format_version, generator_version, major)


def project_too_new(sch: Path | None, pcb: Path | None, cli_version: str | None) -> str | None:
    """A single sentence when the design needs a newer KiCad than we have.

    ``None`` when the installed CLI can open the files, or when no CLI
    version is available to compare against.
    """
    installed = major_version(cli_version)
    if installed is None or not cli_version:
        return None
    newest: FileStamp | None = None
    for path, kind in ((sch, "sch"), (pcb, "pcb")):
        if path is None or not path.is_file():
            continue
        stamp = read_stamp(path, kind)
        if stamp.kicad_major is None:
            continue
        if stamp.kicad_major > installed and (newest is None or stamp.kicad_major > (newest.kicad_major or 0)):
            newest = stamp
    if newest is None:
        return None
    return f"Project saved with KiCad {newest.label}, tester has kicad-cli {cli_version.strip()}"


def board_has_footprints(path: Path) -> bool:
    """False for an unrouted stub that is only a file header.

    A board with footprints but no tracks is still a layout: DRC can report
    the missing connections. A 79-byte ``(kicad_pcb (version …))`` cannot.
    """
    token = b"(footprint"
    overlap = len(token) - 1
    previous = b""
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    return False
                data = previous + chunk
                if token in data:
                    return True
                previous = data[-overlap:] if overlap else b""
    except OSError:
        return False
