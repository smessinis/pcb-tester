"""Upload limits, skipped bulky files, and chunked spooling."""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pcbtester.project import (
    ProjectError,
    UploadLimits,
    extract_zip,
    limits_from_env,
    skip_archive_member,
)
from pcbtester.server import create_app, spool_upload


def _zip(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def test_skip_rules_keep_the_design_and_drop_the_bulk():
    assert not skip_archive_member("board/board.kicad_pro")
    assert not skip_archive_member("board/board.kicad_sch")
    assert not skip_archive_member("board/board.kicad_pcb")
    assert not skip_archive_member("libs/mcu.kicad_sym")
    assert not skip_archive_member("libs/conn.pretty/pin.kicad_mod")
    assert not skip_archive_member("board/sym-lib-table")
    assert skip_archive_member("models/mcu.step")
    assert skip_archive_member("models/mcu.STEP")
    assert skip_archive_member("models/mcu.stp")
    assert skip_archive_member("models/mcu.wrl")
    assert skip_archive_member("plots/sheet.pdf")
    assert skip_archive_member("plots/board.png")
    assert skip_archive_member("gerbers/board-F_Cu.gtl")
    assert skip_archive_member("board/board-backups/board.kicad_sch")
    assert skip_archive_member(r"board\board-backups\old.kicad_pcb")
    assert skip_archive_member("hw/mcu.3dshapes/mcu.wrl")
    assert skip_archive_member("jlcpcb/board.kicad_pcb")
    assert skip_archive_member("Fab/readme.txt")


def test_extract_writes_only_the_design(tmp_path: Path):
    blob = _zip(
        {
            "fc/fc.kicad_pro": b"{}",
            "fc/fc.kicad_sch": b"(kicad_sch)",
            "fc/fc.kicad_pcb": b"(kicad_pcb)",
            "fc/libs/mcu.kicad_sym": b"(kicad_symbol_lib)",
            "fc/mcu.step": b"solid " * 1000,
            "fc/sheet.pdf": b"%PDF",
            "fc/render.png": b"\x89PNG",
            "fc/gerbers/fc-F_Cu.gtl": b"G04 gerber",
            "fc/fc-backups/fc.kicad_sch": b"(old)",
            "fc/mcu.3dshapes/mcu.wrl": b"#VRML",
        }
    )
    archive = tmp_path / "upload.zip"
    archive.write_bytes(blob)
    result = extract_zip(
        archive,
        tmp_path / "out",
        UploadLimits(max_upload_bytes=10_000_000, max_unzipped_bytes=10_000_000, max_files=100),
    )
    root = result.root
    assert (root / "fc" / "fc.kicad_sch").read_bytes() == b"(kicad_sch)"
    assert (root / "fc" / "libs" / "mcu.kicad_sym").is_file()
    assert not (root / "fc" / "mcu.step").exists()
    assert not (root / "fc" / "sheet.pdf").exists()
    assert not (root / "fc" / "render.png").exists()
    assert not (root / "fc" / "gerbers").exists()
    assert not (root / "fc" / "fc-backups").exists()
    assert not (root / "fc" / "mcu.3dshapes").exists()
    assert result.kept_files == 4
    assert result.skipped_files == 6


def test_unzipped_limit_ignores_skipped_models(tmp_path: Path):
    blob = _zip(
        {
            "fc/fc.kicad_sch": b"(kicad_sch)",
            "fc/huge.step": b"x" * 5000,
        }
    )
    archive = tmp_path / "upload.zip"
    archive.write_bytes(blob)
    result = extract_zip(
        archive,
        tmp_path / "out",
        UploadLimits(max_upload_bytes=10_000_000, max_unzipped_bytes=100, max_files=10),
    )
    assert result.kept_files == 1
    assert result.skipped_files == 1
    assert (result.root / "fc" / "fc.kicad_sch").is_file()


def test_unzipped_limit_rejects_a_large_board_file(tmp_path: Path):
    blob = _zip({"fc/fc.kicad_pcb": b"x" * 500})
    archive = tmp_path / "upload.zip"
    archive.write_bytes(blob)
    with pytest.raises(ProjectError, match="50 bytes"):
        extract_zip(
            archive,
            tmp_path / "out",
            UploadLimits(max_upload_bytes=10_000_000, max_unzipped_bytes=50, max_files=10),
        )
    assert not (tmp_path / "out" / "src").exists()


def test_file_count_limit_counts_only_kept_files(tmp_path: Path):
    members = {"fc/fc.kicad_sch": b"(kicad_sch)", "fc/a.step": b"a", "fc/b.wrl": b"b"}
    archive = tmp_path / "upload.zip"
    archive.write_bytes(_zip(members))
    result = extract_zip(
        archive,
        tmp_path / "out",
        UploadLimits(max_upload_bytes=10_000_000, max_unzipped_bytes=10_000_000, max_files=1),
    )
    assert result.kept_files == 1
    members["fc/fc.kicad_pcb"] = b"(kicad_pcb)"
    archive.write_bytes(_zip(members))
    with pytest.raises(ProjectError, match="more than 1 project files"):
        extract_zip(
            archive,
            tmp_path / "again",
            UploadLimits(max_upload_bytes=10_000_000, max_unzipped_bytes=10_000_000, max_files=1),
        )


def test_zip_slip_is_rejected(tmp_path: Path):
    archive = tmp_path / "upload.zip"
    archive.write_bytes(_zip({"../evil.kicad_sch": b"(kicad_sch)"}))
    with pytest.raises(ProjectError, match="Unsafe path"):
        extract_zip(
            archive,
            tmp_path / "out",
            UploadLimits(max_upload_bytes=10_000_000, max_unzipped_bytes=10_000_000, max_files=10),
        )


def test_limits_from_env_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PCBTESTER_MAX_UPLOAD_MB", raising=False)
    monkeypatch.delenv("PCBTESTER_MAX_UNZIPPED_MB", raising=False)
    monkeypatch.delenv("PCBTESTER_MAX_FILES", raising=False)
    limits = limits_from_env()
    assert limits.max_upload_mb == 1024
    assert limits.max_unzipped_mb == 8192
    assert limits.max_files == 100_000
    monkeypatch.setenv("PCBTESTER_MAX_UPLOAD_MB", "2048")
    monkeypatch.setenv("PCBTESTER_MAX_UNZIPPED_MB", "3")
    monkeypatch.setenv("PCBTESTER_MAX_FILES", "12")
    limits = limits_from_env()
    assert limits.max_upload_bytes == 2048 * 1024 * 1024
    assert limits.max_unzipped_mb == 3
    assert limits.max_files == 12
    monkeypatch.setenv("PCBTESTER_MAX_FILES", "nope")
    with pytest.raises(ValueError, match="PCBTESTER_MAX_FILES"):
        limits_from_env()


def test_spool_stops_before_reading_the_rest(tmp_path: Path):
    class Chunked:
        def __init__(self) -> None:
            self.reads = 0

        async def read(self, size: int = -1) -> bytes:
            self.reads += 1
            if self.reads > 5:
                return b""
            return b"a" * (600 * 1024)

    source = Chunked()
    dest = tmp_path / "upload.zip"
    with pytest.raises(ProjectError, match="larger than 1 MB"):
        asyncio.run(spool_upload(source, dest, 1024 * 1024))
    assert not dest.exists()
    # 600 KB + 600 KB crosses 1 MB on the second read. Later chunks are not pulled.
    assert source.reads == 2


def test_api_rejects_an_upload_over_the_configured_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PCBTESTER_MAX_UPLOAD_MB", "1")
    client = TestClient(create_app(tmp_path / "data"))
    payload = b"x" * (1024 * 1024 + 32)
    response = client.post("/api/projects", files={"file": ("big.zip", payload, "application/zip")})
    assert response.status_code == 400
    assert response.json()["detail"] == "Upload is larger than 1 MB"
    stored = list((tmp_path / "data").rglob("upload.zip"))
    assert stored == []


def test_api_upload_skips_a_step_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PCBTESTER_MAX_UPLOAD_MB", "8")
    client = TestClient(create_app(tmp_path / "data"))
    blob = _zip(
        {
            "demo/demo.kicad_pro": b'{"meta": {}}',
            "demo/demo.kicad_sch": b"(kicad_sch (version 20231120))",
            "demo/case.step": b"ISO-10303-21",
        }
    )
    response = client.post("/api/projects", files={"file": ("demo.zip", blob, "application/zip")})
    assert response.status_code == 200
    body = response.json()
    assert body["skipped_files"] == 1
    assert body["kept_files"] == 2
    assert body["name"] == "demo"
    extracted = list((tmp_path / "data").rglob("*.step"))
    assert extracted == []
