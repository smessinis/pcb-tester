"""Run a deck in ngspice batch mode and pull printed voltages back out."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from pcbtester.elec import spice_value


class NgspiceError(RuntimeError):
    pass


def ngspice_available() -> bool:
    return shutil.which("ngspice") is not None


_PRINT = re.compile(r"^([A-Za-z_][\w().]*)\s*=\s*([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)", re.IGNORECASE)


def run_deck(deck: str, *, timeout: int = 15) -> dict[str, float]:
    if not ngspice_available():
        raise NgspiceError("ngspice is not installed")
    with tempfile.TemporaryDirectory(prefix="pcbtester-spice-") as directory:
        path = Path(directory) / "circuit.cir"
        path.write_text(deck)
        try:
            completed = subprocess.run(
                ["ngspice", "-b", str(path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise NgspiceError(f"ngspice timed out after {timeout}s") from exc
    log = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if "Error" in log and "fatal" in log.lower():
        raise NgspiceError(log[-1500:])
    values = parse_prints(log)
    if completed.returncode not in {0, None} and not values:
        raise NgspiceError(log[-1500:] or f"ngspice exited {completed.returncode}")
    values["_log"] = log  # type: ignore[assignment]
    return values


def parse_prints(log: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in log.splitlines():
        match = _PRINT.match(line.strip())
        if not match:
            continue
        name = match.group(1).lower()
        if name.startswith("v(") or name.startswith("i(") or name.isidentifier():
            found[name] = float(match.group(2))
    return found


def divider_deck(r_top: float, r_bottom: float, vin: float) -> str:
    return "\n".join(
        [
            "* PCB Tester resistive divider",
            f"Vstim vin 0 DC {spice_value(vin)}",
            f"Rtop vin mid {spice_value(r_top)}",
            f"Rbot mid 0 {spice_value(r_bottom)}",
            ".control",
            "op",
            "print v(mid)",
            "print v(vin)",
            "quit",
            ".endc",
            ".end",
            "",
        ]
    )


def rc_deck(resistance: float, capacitance: float, vin: float) -> str:
    tau = resistance * capacitance
    stop = max(tau * 8, 1e-6)
    step = stop / 200
    return "\n".join(
        [
            "* PCB Tester RC low-pass, input step",
            f"Vstim vin 0 PULSE(0 {spice_value(vin)} 1u 1u 1u {spice_value(stop)} {spice_value(stop * 2)})",
            f"R1 vin vout {spice_value(resistance)}",
            f"C1 vout 0 {spice_value(capacitance)}",
            f".tran {spice_value(step)} {spice_value(stop)}",
            ".control",
            "run",
            f"meas tran vsettle FIND v(vout) AT={spice_value(stop * 0.8)}",
            "quit",
            ".endc",
            ".end",
            "",
        ]
    )


def regulator_deck(vin: float, vset: float, cout: float, dropout: float = 0.3) -> str:
    # A behavioral clamp. This is not a vendor transistor model.
    return "\n".join(
        [
            "* PCB Tester behavioral regulator (not a vendor model)",
            f"Vstim vin 0 PULSE(0 {spice_value(vin)} 10u 20u 20u 2m 4m)",
            f"Breg out 0 V = u(v(vin)-{spice_value(dropout)}) * min({spice_value(vset)}, v(vin)-{spice_value(dropout)})",
            f"Cout out 0 {spice_value(cout)}",
            "Rload out 0 100",
            ".tran 5u 1.5m",
            ".control",
            "run",
            "meas tran vout FIND v(out) AT=1.2m",
            "quit",
            ".endc",
            ".end",
            "",
        ]
    )
