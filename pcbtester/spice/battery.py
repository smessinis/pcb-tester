"""Extra operating points for a battery-sense divider.

A 3S nominal check can hide a divider that clips the ADC at 6S full charge.
Cell count comes from the net name when it says so (``VBAT_6S``). Otherwise
the configured packs are used, 4S (16.8 V) and 6S (25.2 V) by default.
"""

from __future__ import annotations

import os
import re

from pcbtester.elec import format_volts, is_battery_name
from pcbtester.kicad.netlist import Netlist

_CELL_VOLTS = 4.2
_CELL_IN_NAME = re.compile(r"(?i)(?:^|[^A-Z0-9])([2-9])\s*S(?:[^A-Z]|$)")
_DEFAULT_PACKS = (4, 6)


def implied_cell_count(net_name: str) -> int | None:
    match = _CELL_IN_NAME.search(net_name)
    if not match:
        return None
    return int(match.group(1))


def battery_check_points(net_name: str) -> list[tuple[str, float]]:
    """Voltages to evaluate besides the nominal operating point."""
    implied = implied_cell_count(net_name)
    if implied is not None:
        return [(f"{implied}S", implied * _CELL_VOLTS)]
    raw = os.environ.get("PCBTESTER_BATTERY_MAX_V", "").strip()
    if raw:
        return [("max", float(raw))]
    packs = os.environ.get("PCBTESTER_BATTERY_PACKS", "4S,6S")
    points: list[tuple[str, float]] = []
    for part in packs.split(","):
        piece = part.strip()
        if not piece:
            continue
        cells = re.fullmatch(r"(?i)([2-9])S", piece)
        if cells:
            count = int(cells.group(1))
            points.append((f"{count}S", count * _CELL_VOLTS))
        else:
            points.append((piece, float(piece)))
    return points or [(f"{count}S", count * _CELL_VOLTS) for count in _DEFAULT_PACKS]


def adc_full_scale(netlist: Netlist, voltages: dict[str, float]) -> tuple[float, str]:
    """MCU ADC range from VDDA or VREF+, otherwise 3.3 V."""
    found: list[tuple[float, str]] = []
    for net in netlist.nets:
        for node in net.nodes:
            pin = (node.pin_name or "").upper().replace("+", "")
            if pin.startswith("VREF") or pin.startswith("VDDA"):
                volts = voltages.get(net.name)
                if volts is not None and volts > 0.5:
                    found.append((volts, f"{node.pin_name} on {net.name}"))
    if not found:
        for name, volts in voltages.items():
            if volts > 0.5 and re.search(r"(?i)VDDA|VREF", name):
                found.append((volts, name))
    if not found:
        return 3.3, "3.3 V default"
    volts, label = min(found, key=lambda item: item[0])
    return volts, label


def divider_headroom(
    top_net: str,
    mid_net: str,
    r_top: float,
    r_bottom: float,
    adc_max: float,
    adc_label: str,
    nominal_vin: float | None = None,
) -> tuple[str, bool]:
    """Prose for the high pack voltages, and whether the ADC clips.

    Not a battery net: empty text and no flag.
    """
    if not is_battery_name(top_net) or r_top + r_bottom <= 0:
        return "", False
    ratio = r_bottom / (r_top + r_bottom)
    lines: list[str] = []
    exceeds = False
    for label, vin in battery_check_points(top_net):
        if nominal_vin is not None and abs(vin - nominal_vin) < 0.05:
            continue
        vout = vin * ratio
        sentence = (
            f"At {label} ({format_volts(vin)}) V({mid_net}) = {format_volts(vout)}."
        )
        if vout > adc_max + 0.01:
            exceeds = True
            sentence += f" Exceeds the ADC range ({format_volts(adc_max)}, {adc_label})."
        lines.append(sentence)
    return " ".join(lines), exceeds
