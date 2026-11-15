#!/usr/bin/env python3
"""Build the compact MCU pin database shipped with PCB Tester.

The input is the public chip JSON from embassy-rs/stm32-data-generated
(MIT OR Apache-2.0). We keep only the package pinout and peripheral
functions so checks do not need the network, and so another MCU can be
added by dropping another JSON file in pcbtester/mcu/data/.

Usage:
    python scripts/build_mcu_db.py [chip.json ...]
If no paths are given, the STM32H743 LQFP100 and LQFP144 files are
downloaded from the embassy-rs repository.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "pcbtester" / "mcu" / "data"

EMBASSY_RAW = (
    "https://raw.githubusercontent.com/embassy-rs/stm32-data-generated/"
    "main/data/chips/{name}.json"
)

# AF0 debug pins are often omitted from the Cube-derived AF tables because
# they are the reset function. They are the same on STM32H7 LQFP parts.
DEBUG_AF0 = {
    "PA13": [("SWDIO", 0), ("JTMS", 0)],
    "PA14": [("SWCLK", 0), ("JTCK", 0)],
    "PA15": [("JTDI", 0)],
    "PB3": [("JTDO", 0), ("SWO", 0)],
    "PB4": [("NJTRST", 0)],
}

DEFAULT_CHIPS = ("STM32H743VI", "STM32H743ZI")


def download(name: str) -> dict:
    url = EMBASSY_RAW.format(name=name)
    print(f"fetch {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _numeric_package(pkg: dict) -> bool:
    return bool(pkg.get("pins")) and all(str(p["position"]).isdigit() for p in pkg["pins"])


def _role(name: str) -> str:
    upper = name.upper().replace(" ", "")
    if upper.startswith("VCAP"):
        return "vcap"
    if upper in {"NRST", "RESET"}:
        return "nrst"
    if upper.startswith("BOOT"):
        return "boot"
    if upper.startswith("VBAT"):
        return "vbat"
    if upper.startswith("VDDA"):
        return "vdda"
    if upper.startswith("VREF"):
        return "vref"
    if upper.startswith("VDD"):
        return "vdd"
    if upper.startswith("VSS") or upper in {"GND"}:
        return "gnd"
    return "io"


def _patterns(package_name: str) -> list[str]:
    """STM32H743VITx matches the KiCad name and STM32H743VIT6."""
    patterns = [package_name]
    if package_name.endswith("x"):
        patterns.append(package_name[:-1])
    # Bare family+pin count key, shorter, so a more specific package wins.
    # STM32H743VITx -> STM32H743VI
    trimmed = package_name
    if trimmed.endswith("x"):
        trimmed = trimmed[:-1]
    if trimmed and trimmed[-1] in "TRC":
        patterns.append(trimmed[:-1])
    # Preserve order, drop duplicates.
    seen: list[str] = []
    for item in patterns:
        if item not in seen:
            seen.append(item)
    return seen


def convert(chip: dict) -> list[dict]:
    packages = [pkg for pkg in chip["packages"] if _numeric_package(pkg)]
    if not packages:
        raise SystemExit(f"{chip.get('name')}: no numeric (LQFP-style) package")
    peripherals = []
    for core in chip.get("cores") or []:
        peripherals.extend(core.get("peripherals") or [])

    built = []
    for pkg in packages:
        position_name = {str(pin["position"]): pin["signals"][0] for pin in pkg["pins"]}
        functions: dict[str, list[dict]] = defaultdict(list)
        seen: dict[str, set[str]] = defaultdict(set)
        for periph in peripherals:
            pname = periph.get("name") or ""
            if pname.startswith("GPIO") or pname in {"ADC12_COMMON", "ADC3_COMMON"}:
                continue
            for entry in periph.get("pins") or []:
                gpio = entry.get("pin")
                signal = entry.get("signal")
                if not gpio or not signal:
                    continue
                fname = f"{pname}_{signal}"
                if fname in seen[gpio]:
                    continue
                seen[gpio].add(fname)
                functions[gpio].append({"name": fname, "af": entry.get("af")})
        for gpio, extras in DEBUG_AF0.items():
            if gpio not in position_name.values():
                continue
            for fname, af in extras:
                if fname in seen[gpio]:
                    continue
                seen[gpio].add(fname)
                functions[gpio].append({"name": fname, "af": af})

        pins = {}
        for position, name in sorted(position_name.items(), key=lambda kv: int(kv[0])):
            role = _role(name)
            pins[position] = {
                "name": name,
                "role": role,
                # STM32H743 I/Os in this datasheet are FT (5 V tolerant).
                # Power and reset pins are not. TT parts can set this false.
                "ft": role == "io",
                "functions": sorted(functions.get(name, []), key=lambda item: item["name"]),
            }
        package_name = pkg["name"]
        built.append(
            {
                "id": package_name,
                "family": chip.get("family"),
                "line": chip.get("line"),
                "device": chip.get("name"),
                "package": pkg.get("package"),
                "patterns": _patterns(package_name),
                "source": (
                    "Derived from embassy-rs/stm32-data-generated "
                    "(MIT OR Apache-2.0), chip file "
                    f"{chip.get('name')}.json, package {package_name}. "
                    "AF0 SWD/JTAG names are supplemented where the Cube "
                    "tables omit the reset function. "
                    "ft=true marks a GPIO; STM32H743 I/Os are 5 V tolerant "
                    "(FT) per the datasheet I/O-structure column. Power pins "
                    "are not."
                ),
                "pins": pins,
            }
        )
    return built


def main(argv: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = [Path(arg) for arg in argv]
    chips: list[dict] = []
    if paths:
        for path in paths:
            chips.append(json.loads(path.read_text()))
    else:
        for name in DEFAULT_CHIPS:
            chips.append(download(name))
    written = []
    for chip in chips:
        for mcu in convert(chip):
            dest = OUT_DIR / f"{mcu['id']}.json"
            dest.write_text(json.dumps(mcu, indent=2) + "\n")
            written.append(dest.name)
            print(f"wrote {dest} ({len(mcu['pins'])} pins)")
    index = {
        "parts": written,
        "note": "Add a part by dropping another JSON file of this shape into this directory.",
    }
    (OUT_DIR / "index.json").write_text(json.dumps(index, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
