"""Run kicad-cli. The CLI is optional at import time so unit tests run without it."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

_TEMPLATE = Path("/usr/share/kicad/template")
_SYMBOL_DIR = Path("/usr/share/kicad/symbols")
_FOOTPRINT_DIR = Path("/usr/share/kicad/footprints")


class KicadCliError(RuntimeError):
    def __init__(self, message: str, returncode: int | None = None, output: str = ""):
        super().__init__(message)
        self.returncode = returncode
        self.output = output


def kicad_available() -> bool:
    return shutil.which("kicad-cli") is not None


def kicad_version() -> str | None:
    if not kicad_available():
        return None
    completed = subprocess.run(
        ["kicad-cli", "version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    text = (completed.stdout or completed.stderr or "").strip()
    return text.splitlines()[0] if text else None


def prepare_kicad_env() -> dict[str, str]:
    """Point kicad-cli at the stock symbol and footprint libraries.

    Without a global sym-lib-table, ERC warns once per library nickname
    ("does not include the symbol library …"). That is the container, not
    the design. Copy the packaged tables into the KiCad 10 config directory
    when the user has none, and set KICAD10_SYMBOL_DIR / KICAD10_FOOTPRINT_DIR
    when they are not already set.
    """
    env = os.environ.copy()
    if _SYMBOL_DIR.is_dir():
        env.setdefault("KICAD10_SYMBOL_DIR", str(_SYMBOL_DIR))
    if _FOOTPRINT_DIR.is_dir():
        env.setdefault("KICAD10_FOOTPRINT_DIR", str(_FOOTPRINT_DIR))
    config = Path(env.get("KICAD_CONFIG_HOME") or (Path.home() / ".config" / "kicad")) / "10.0"
    if _TEMPLATE.is_dir():
        config.mkdir(parents=True, exist_ok=True)
        for name in ("sym-lib-table", "fp-lib-table"):
            source = _TEMPLATE / name
            dest = config / name
            if source.is_file() and not dest.exists():
                shutil.copy(source, dest)
    return env


def run_kicad(args: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    if not kicad_available():
        raise KicadCliError("kicad-cli is not installed")
    try:
        return subprocess.run(
            ["kicad-cli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=prepare_kicad_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise KicadCliError(f"kicad-cli timed out after {timeout}s", output=str(exc)) from exc


def export_netlist(schematic: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    completed = run_kicad(
        ["sch", "export", "netlist", "--format", "kicadxml", "-o", str(dest), str(schematic)]
    )
    if completed.returncode != 0 or not dest.exists():
        output = (completed.stdout or "") + (completed.stderr or "")
        raise KicadCliError(
            f"netlist export failed ({completed.returncode})",
            returncode=completed.returncode,
            output=output.strip(),
        )


def export_erc(schematic: Path, dest: Path) -> subprocess.CompletedProcess[str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Exit code 5 means "violations exist" when --exit-code-violations is set.
    # We still want the JSON, so we do not pass that flag.
    return run_kicad(
        [
            "sch",
            "erc",
            "--format",
            "json",
            "--severity-all",
            "-o",
            str(dest),
            str(schematic),
        ]
    )


def export_drc(board: Path, dest: Path) -> subprocess.CompletedProcess[str]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    return run_kicad(
        [
            "pcb",
            "drc",
            "--format",
            "json",
            "--severity-all",
            "-o",
            str(dest),
            str(board),
        ],
        timeout=300,
    )
