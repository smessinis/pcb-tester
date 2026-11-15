"""Small electrical parsers shared by the checks.

Values come from schematic text, so the grammar is the one engineers
actually type: 100n, 100nF, 2k2, 4R7, 0.1uF, 22U.
"""

from __future__ import annotations

import re

_PREFIX = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "µ": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "K": 1e3,
    "M": 1e6,
}

_GROUND = {
    "GND",
    "AGND",
    "DGND",
    "PGND",
    "GNDA",
    "GNDD",
    "VSS",
    "VSSA",
    "VSSD",
    "0V",
}

_BATTERY = {"VBAT", "VBATT", "BAT", "BATT", "BATTERY", "VBATTERY", "LIPO", "PACK"}


def _clean_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def is_ground(name: str) -> bool:
    token = _clean_name(name)
    if token in _GROUND:
        return True
    return token.startswith("GND") and not token.startswith("GNDFLAG")


def is_battery_name(name: str) -> bool:
    token = _clean_name(name)
    if token in _BATTERY:
        return True
    return token.startswith("VBAT") or token.startswith("VBATT") or token.startswith("BATTERY")


def net_voltage(name: str) -> float | None:
    """Best-effort volts from a net name. Ground is 0. Unknown is None."""
    if is_ground(name):
        return 0.0
    text = name.strip()
    match = re.search(r"(\d+)\.(\d+)\s*V", text, re.IGNORECASE)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")
    match = re.search(r"(\d+)\s*V\s*(\d+)", text, re.IGNORECASE)
    if match:
        frac = match.group(2)
        return float(match.group(1)) + float(frac) / (10 ** len(frac))
    match = re.search(r"(\d+(?:\.\d+)?)\s*V\b", text, re.IGNORECASE)
    if match:
        return float(match.group(1))
    token = _clean_name(text)
    if token in {"VBUS", "VUSB"} or token.endswith("VBUS"):
        return 5.0
    return None


def voltage_from_part(value: str) -> float | None:
    """Fixed-regulator voltage from a value such as AP2112K-3.3 or LT1963-1.8."""
    match = re.search(r"[-_](\d+)\.(\d+)\b", value)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")
    match = re.search(r"[-_](\d+)\s*V\s*(\d+)\b", value, re.IGNORECASE)
    if match:
        frac = match.group(2)
        return float(match.group(1)) + float(frac) / (10 ** len(frac))
    return None


def parse_resistance(value: str) -> float | None:
    """Ohms. Accepts 10k, 2k2, 4R7, 100R, 0.1, 10."""
    text = value.strip().replace(" ", "").replace("Ω", "")
    text = re.sub(r"(?i)ohms?$", "", text)
    if not text or text.lower() in {"r", "resistor", "dnp"}:
        return None
    match = re.fullmatch(r"(\d+)[rR](\d+)", text)
    if match:
        return float(f"{match.group(1)}.{match.group(2)}")
    match = re.fullmatch(r"(\d+)([kKmM])(\d+)", text)
    if match:
        return float(f"{match.group(1)}.{match.group(3)}") * _PREFIX[match.group(2)]
    match = re.fullmatch(r"(\d*\.?\d+)([kKmM])", text)
    if match:
        return float(match.group(1)) * _PREFIX[match.group(2)]
    match = re.fullmatch(r"(\d*\.?\d+)[rR]?", text)
    if match:
        return float(match.group(1))
    return None


def parse_capacitance(value: str) -> float | None:
    """Farads. Accepts 100n, 100nF, 0.1uF, 2.2u, 10pF, 22U, 4n7."""
    text = value.strip().replace(" ", "")
    text = re.sub(r"(?i)farads?$", "", text)
    if text.lower().endswith("f") and not text.lower().endswith("uf"):
        # Strip a unit F but keep the prefix: 100nF -> 100n, 10uF handled below.
        if re.search(r"(?i)\d[pnuµmkm]f$", text):
            text = text[:-1]
    text = re.sub(r"(?i)u[fF]$", "u", text)
    if not text or text.lower() in {"c", "cap", "capacitor", "dnp"}:
        return None
    match = re.fullmatch(r"(\d+)([pnuµmkK])(\d+)", text)
    if match:
        return float(f"{match.group(1)}.{match.group(3)}") * _PREFIX[match.group(2)]
    match = re.fullmatch(r"(\d*\.?\d+)([pnuµmkK])", text)
    if match and match.group(2) in _PREFIX:
        return float(match.group(1)) * _PREFIX[match.group(2)]
    # Bare "22U" is how some schematics write 22 µF.
    match = re.fullmatch(r"(\d*\.?\d+)[uUµ]", text)
    if match:
        return float(match.group(1)) * 1e-6
    return None


def spice_value(number: float, unit: str = "") -> str:
    """Compact engineering notation ngspice accepts (100n, 4.7k)."""
    if number == 0:
        return "0"
    prefixes = (
        (1e12, "T"),
        (1e9, "G"),
        (1e6, "Meg"),
        (1e3, "k"),
        (1.0, ""),
        (1e-3, "m"),
        (1e-6, "u"),
        (1e-9, "n"),
        (1e-12, "p"),
    )
    sign = "-" if number < 0 else ""
    magnitude = abs(number)
    for scale, prefix in prefixes:
        if magnitude >= scale or scale == 1e-12:
            scaled = magnitude / scale
            text = f"{scaled:.4g}"
            return f"{sign}{text}{prefix}{unit}"
    return f"{number}{unit}"


def format_volts(value: float) -> str:
    text = f"{value:.4g}"
    return f"{text} V"
