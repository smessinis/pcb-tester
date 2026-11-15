"""Find a KiCad project inside a directory or an uploaded zip."""

from __future__ import annotations

import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pcbtester.context import ProjectContext

# Files the checkers never open. A flight-controller repo is often mostly
# these: STEP models, plot PDFs, and a gerber folder.
_SKIP_EXTENSIONS = {
    ".step",
    ".stp",
    ".wrl",
    ".wrz",
    ".igs",
    ".iges",
    ".stl",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
    ".svg",
    ".ico",
    ".pdf",
    ".gbr",
    ".ger",
    ".pho",
    ".gtl",
    ".gbl",
    ".gts",
    ".gbs",
    ".gto",
    ".gbo",
    ".gtp",
    ".gbp",
    ".gko",
    ".gm1",
    ".gd1",
    ".gg1",
    ".g2",
    ".g3",
    ".g4",
    ".g5",
    ".drl",
    ".xln",
    ".exc",
    ".gbrjob",
}

# Directory names that hold fab output rather than the design. Compared
# case-insensitively. `production` is intentionally not here: some people
# keep the KiCad project in a folder with that name.
_SKIP_DIR_NAMES = {
    "gerber",
    "gerbers",
    "gerber_output",
    "fab",
    "fabrication",
    "fab_output",
    "jlcpcb",
    "pcbway",
    "nextpcb",
    "__macosx",
}

_DEFAULT_UPLOAD_MB = 1024
_DEFAULT_UNZIPPED_MB = 8192
_DEFAULT_MAX_FILES = 100_000
_CHUNK = 1024 * 1024


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class UploadLimits:
    max_upload_bytes: int
    max_unzipped_bytes: int
    max_files: int

    @property
    def max_upload_mb(self) -> int:
        return self.max_upload_bytes // (1024 * 1024)

    @property
    def max_unzipped_mb(self) -> int:
        return self.max_unzipped_bytes // (1024 * 1024)


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1, got {value}")
    return value


def limits_from_env() -> UploadLimits:
    """Read PCBTESTER_MAX_UPLOAD_MB, PCBTESTER_MAX_UNZIPPED_MB, PCBTESTER_MAX_FILES."""
    upload_mb = _positive_int("PCBTESTER_MAX_UPLOAD_MB", _DEFAULT_UPLOAD_MB)
    unzipped_mb = _positive_int("PCBTESTER_MAX_UNZIPPED_MB", _DEFAULT_UNZIPPED_MB)
    max_files = _positive_int("PCBTESTER_MAX_FILES", _DEFAULT_MAX_FILES)
    return UploadLimits(
        max_upload_bytes=upload_mb * 1024 * 1024,
        max_unzipped_bytes=unzipped_mb * 1024 * 1024,
        max_files=max_files,
    )


def _size_label(num_bytes: int) -> str:
    mib = 1024 * 1024
    if num_bytes % mib == 0:
        return f"{num_bytes // mib} MB"
    return f"{num_bytes} bytes"


def archive_parts(name: str) -> tuple[str, ...]:
    return tuple(part for part in name.replace("\\", "/").split("/") if part not in ("", "."))


def skip_archive_member(name: str) -> bool:
    """True for members the checks do not read, so they are not extracted."""
    parts = archive_parts(name)
    if not parts:
        return True
    for part in parts[:-1]:
        lowered = part.lower()
        if (
            lowered in _SKIP_DIR_NAMES
            or lowered in {".history", ".ds_store"}
            or lowered.endswith("-backups")
            or lowered.endswith(".3dshapes")
        ):
            return True
    if parts[-1].lower() == ".ds_store":
        return True
    suffix = Path(parts[-1]).suffix.lower()
    return suffix in _SKIP_EXTENSIONS


@dataclass(frozen=True)
class ExtractResult:
    root: Path
    kept_files: int
    skipped_files: int


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


def _unsafe_member(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\"):
        return True
    parts = archive_parts(name)
    if not parts or ".." in parts:
        return True
    # Windows drive prefix, e.g. C:/board/board.kicad_pro
    if len(parts[0]) >= 2 and parts[0][1] == ":":
        return True
    return False


def extract_zip(archive_path: Path, dest: Path, limits: UploadLimits | None = None) -> ExtractResult:
    """Extract the design files from an upload.

    3D models, images, PDFs, Gerbers and KiCad backup folders are skipped.
    Only the files that are written count toward the unzipped-size and
    file-count limits. Bytes are copied in chunks, and the running total is
    checked against the declared size so a lying zip header cannot expand
    without bound.
    """
    limits = limits or limits_from_env()
    src = dest / "src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True, exist_ok=True)
    kept = 0
    skipped = 0
    written = 0
    try:
        with zipfile.ZipFile(archive_path) as archive:
            chosen: list[zipfile.ZipInfo] = []
            for info in archive.infolist():
                if info.is_dir():
                    continue
                if _unsafe_member(info.filename):
                    raise ProjectError(f"Unsafe path in zip: {info.filename}")
                if skip_archive_member(info.filename):
                    skipped += 1
                    continue
                kept += 1
                if kept > limits.max_files:
                    raise ProjectError(
                        f"This zip has more than {limits.max_files} project files "
                        "(3D models, images, PDFs, Gerbers and backups are not counted)"
                    )
                if info.file_size > limits.max_unzipped_bytes or written + info.file_size > limits.max_unzipped_bytes:
                    raise ProjectError(f"Zip expands past the {_size_label(limits.max_unzipped_bytes)} limit")
                written += info.file_size
                chosen.append(info)
            written = 0
            for info in chosen:
                relative = Path(*archive_parts(info.filename))
                target = src / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as inbound, target.open("wb") as outbound:
                    while True:
                        chunk = inbound.read(_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > limits.max_unzipped_bytes:
                            raise ProjectError(f"Zip expands past the {_size_label(limits.max_unzipped_bytes)} limit")
                        outbound.write(chunk)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(src, ignore_errors=True)
        raise ProjectError("Upload is not a zip file") from exc
    except ProjectError:
        shutil.rmtree(src, ignore_errors=True)
        raise
    return ExtractResult(root=src, kept_files=kept, skipped_files=skipped)
