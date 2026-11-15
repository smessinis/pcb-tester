"""Find a KiCad project inside a directory or an uploaded zip."""

from __future__ import annotations

import zipfile
from pathlib import Path

from pcbtester.context import ProjectContext

_MAX_FILES = 8000
_MAX_UNCOMPRESSED = 250 * 1024 * 1024


class ProjectError(ValueError):
    pass


def discover(path: Path) -> ProjectContext:
    path = path.resolve()
    if path.is_file():
        if path.suffix == ".kicad_pro":
            return _from_pro(path)
        if path.suffix == ".kicad_sch":
            pcb = path.with_suffix(".kicad_pcb")
            return ProjectContext(
                root=path.parent,
                name=path.stem,
                pro=_sibling(path, ".kicad_pro"),
                sch=path,
                pcb=pcb if pcb.exists() else None,
                work=path.parent / ".pcbtester",
            )
        raise ProjectError(f"{path.name} is not a KiCad project, schematic or directory")
    if not path.is_dir():
        raise ProjectError(f"{path} does not exist")
    pros = [
        item
        for item in path.rglob("*.kicad_pro")
        if _wanted(item)
    ]
    if pros:
        ranked = sorted(pros, key=_rank_pro, reverse=True)
        return _from_pro(ranked[0])
    schematics = [item for item in path.rglob("*.kicad_sch") if _wanted(item)]
    if not schematics:
        raise ProjectError("No .kicad_pro or .kicad_sch file found")
    schematics.sort(key=lambda item: item.stat().st_size, reverse=True)
    sch = schematics[0]
    pcb = sch.with_suffix(".kicad_pcb")
    return ProjectContext(
        root=path,
        name=sch.stem,
        pro=_sibling(sch, ".kicad_pro"),
        sch=sch,
        pcb=pcb if pcb.exists() else None,
        work=path / ".pcbtester",
    )


def _wanted(path: Path) -> bool:
    parts = path.parts
    if any(part.startswith(".") or part == "__MACOSX" for part in parts):
        return False
    return True


def _sibling(path: Path, suffix: str) -> Path | None:
    candidate = path.with_suffix(suffix)
    return candidate if candidate.exists() else None


def _rank_pro(path: Path) -> tuple[int, int]:
    sch = path.with_suffix(".kicad_sch")
    pcb = path.with_suffix(".kicad_pcb")
    score = int(sch.exists()) + int(pcb.exists())
    size = sch.stat().st_size if sch.exists() else 0
    return score, size


def _from_pro(pro: Path) -> ProjectContext:
    sch = pro.with_suffix(".kicad_sch")
    pcb = pro.with_suffix(".kicad_pcb")
    # Hierarchical projects sometimes keep the top sheet name in the pro file.
    # The sibling with the same stem is the usual top sheet.
    return ProjectContext(
        root=pro.parent,
        name=pro.stem,
        pro=pro,
        sch=sch if sch.exists() else None,
        pcb=pcb if pcb.exists() else None,
        work=pro.parent / ".pcbtester",
    )


def extract_zip(blob: bytes, dest: Path) -> Path:
    """Extract an upload. Returns the directory that contains the project."""
    dest.mkdir(parents=True, exist_ok=True)
    archive_path = dest / "upload.zip"
    archive_path.write_bytes(blob)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            if len(infos) > _MAX_FILES:
                raise ProjectError(f"Zip has {len(infos)} files; the limit is {_MAX_FILES}")
            total = sum(info.file_size for info in infos)
            if total > _MAX_UNCOMPRESSED:
                raise ProjectError("Zip expands past the 250 MB limit")
            for info in infos:
                relative = Path(info.filename)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ProjectError(f"Unsafe path in zip: {info.filename}")
            archive.extractall(dest / "src")
    except zipfile.BadZipFile as exc:
        raise ProjectError("Upload is not a zip file") from exc
    return dest / "src"
