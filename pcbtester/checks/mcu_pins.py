"""Map schematic nets onto an MCU pin database.

The reference part is the STM32H743 (LQFP100 and LQFP144 are shipped).
A net named like a peripheral function (USART1_TX, I2C1_SCL, SPI1_SCK)
must land on a pin that actually offers that function. Power pins are
checked against the voltage recovered from the net name or the regulator
that feeds them. Adding another MCU is a new JSON file, not a new check.
"""

from __future__ import annotations

import re

from pcbtester.checks.base import Check
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.elec import format_volts, is_ground
from pcbtester.kicad.netlist import Component, Netlist, Node
from pcbtester.mcu.db import Mcu, load_mcus, looks_like_stm32, match_mcu
from pcbtester.models import CheckResult, Finding, rollup
from pcbtester.power import build_power_tree, find_series_links, pin_nets

# Compact net name -> canonical function name, applied only when that
# function exists on the matched MCU.
_ALIASES = {
    "SWDIO": "SWDIO",
    "SWDCLK": "SWCLK",
    "SWCLK": "SWCLK",
    "JTMS": "JTMS",
    "JTCK": "JTCK",
    "JTDO": "JTDO",
    "NJTRST": "NJTRST",
    "USBDP": "USB_OTG_FS_DP",
    "USBDM": "USB_OTG_FS_DM",
    "DPLUS": "USB_OTG_FS_DP",
    "DMINUS": "USB_OTG_FS_DM",
    "OTGFSDP": "USB_OTG_FS_DP",
    "OTGFSDM": "USB_OTG_FS_DM",
}

_VDD_MAX = 3.6


class McuPinCheck(Check):
    id = "mcu_pins"
    title = "MCU pinmux and power"
    description = (
        "Matches STM32 parts on the schematic to a pin database (STM32H743 "
        "LQFP100 and LQFP144 are included) and checks peripheral nets, "
        "duplicate assignments, 5 V on a non-tolerant pin, VDD above 3.6 V, "
        "and VCAP. A net that is not a known function name is treated as GPIO "
        "and is not a failure. Firmware alternate-function registers are not "
        "read; the net name is the only statement of intent."
    )

    def run(self, ctx: ProjectContext, options: RunOptions) -> CheckResult:
        del options
        if ctx.netlist is None:
            message = ctx.netlist_error or "No netlist is available."
            return CheckResult(self.id, self.title, "error", message)
        findings, artifacts = analyze_mcus(ctx.netlist)
        if artifacts.get("matched") == 0 and not findings:
            return CheckResult(
                self.id,
                self.title,
                "skipped",
                "No MCU with a pin database was found on the schematic.",
                artifacts=artifacts,
            )
        status = rollup(findings)
        fails = sum(1 for item in findings if item.severity == "fail")
        warns = sum(1 for item in findings if item.severity == "warn")
        matched = artifacts.get("matched", 0)
        if status == "pass":
            summary = f"Checked {matched} MCU(s); constrained nets match the pin database."
        else:
            summary = f"Checked {matched} MCU(s): {fails} error(s), {warns} warning(s)."
        return CheckResult(self.id, self.title, status, summary, findings=findings, artifacts=artifacts)


def analyze_mcus(netlist: Netlist, mcus: tuple[Mcu, ...] | None = None) -> tuple[list[Finding], dict]:
    catalog = mcus if mcus is not None else load_mcus()
    tree = build_power_tree(netlist)
    voltages: dict[str, float] = tree["voltages"]
    links = find_series_links(netlist, pin_nets(netlist))
    findings: list[Finding] = []
    pinmux: list[dict] = []
    matched = 0
    seen_unknown = False
    for component in netlist.components.values():
        mcu = match_mcu(component.value, component.part, component.lib, catalog)
        if mcu is None:
            if looks_like_stm32(component.value, component.part) and not seen_unknown:
                seen_unknown = True
                findings.append(
                    Finding(
                        "warn",
                        "mcu.no_database",
                        f"{component.ref} ({component.value or component.part}) looks like an STM32, "
                        "but no pin database matches it. Add a JSON file under pcbtester/mcu/data/.",
                        refs=[component.ref],
                    )
                )
            continue
        matched += 1
        part_findings, rows = _analyze_one(netlist, component, mcu, voltages, links)
        findings.extend(part_findings)
        pinmux.extend(rows)
    artifacts = {"matched": matched, "pinmux": pinmux, "parts": [mcu.id for mcu in catalog]}
    return findings, artifacts


def _compact(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def resolve_function(net_name: str, mcu: Mcu) -> str | None:
    """Return the canonical function a net name is asking for, if any."""
    token = _compact(net_name)
    if not token:
        return None
    if token in mcu.by_compact:
        return mcu.by_compact[token]
    if token.startswith("UART"):
        swapped = "USART" + token[4:]
        if swapped in mcu.by_compact:
            return mcu.by_compact[swapped]
    if token.endswith("SCLK"):
        alt = token[:-4] + "SCK"
        if alt in mcu.by_compact:
            return mcu.by_compact[alt]
    if token.endswith("CS"):
        alt = token[:-2] + "NSS"
        if alt in mcu.by_compact:
            return mcu.by_compact[alt]
    alias = _ALIASES.get(token)
    if alias and _compact(alias) in mcu.by_compact:
        return mcu.by_compact[_compact(alias)]
    return None


def _nodes_by_pin(netlist: Netlist, ref: str) -> dict[str, tuple[Node, str]]:
    found: dict[str, tuple[Node, str]] = {}
    for net in netlist.nets:
        for node in net.nodes:
            if node.ref == ref:
                found[node.pin] = (node, net.name)
    return found


def _analyze_one(netlist, component: Component, mcu: Mcu, voltages: dict[str, float], links) -> tuple[list[Finding], list[dict]]:
    findings: list[Finding] = []
    rows: list[dict] = []
    connected = _nodes_by_pin(netlist, component.ref)
    symbol_pins = set(component.pins)
    claimed: dict[str, list[str]] = {}
    validated = 0
    absent_from_symbol: list[str] = []

    vdd_nets = set()
    vdda_nets = set()
    for number, pin in mcu.pins.items():
        if pin.role == "vdd":
            pair = connected.get(number)
            if pair:
                vdd_nets.add(pair[1])
        elif pin.role == "vdda":
            pair = connected.get(number)
            if pair:
                vdda_nets.add(pair[1])

    for number, pin in mcu.pins.items():
        pair = connected.get(number)
        if pair is None:
            if pin.role in {"vdd", "vdda", "vcap", "gnd", "vbat", "boot", "nrst"}:
                if symbol_pins and number not in symbol_pins:
                    absent_from_symbol.append(f"{number}:{pin.name}")
                else:
                    findings.append(
                        Finding(
                            "fail",
                            "mcu.pin_missing",
                            f"{component.ref} pin {number} ({pin.name}) is not in the netlist.",
                            refs=[component.ref],
                            pins=[f"{number}:{pin.name}"],
                        )
                    )
            continue
        node, net_name = pair
        volts = voltages.get(net_name)
        function = resolve_function(net_name, mcu)
        # A net named exactly for the pin (PA9) is not a peripheral request.
        if function and _compact(function) == _compact(pin.name):
            function = None
        offered = pin.function(function) if function else None
        ok = function is None or offered is not None or pin.role != "io"
        if function and pin.role == "io":
            claimed.setdefault(function, []).append(f"{pin.name}:{net_name}")
            if offered is None:
                ok = False
                findings.append(
                    Finding(
                        "fail",
                        "mcu.function_unavailable",
                        f"{component.ref} {pin.name} (pin {number}) is on {net_name}, which asks for {function}. "
                        f"That function is not available on {pin.name}.",
                        refs=[component.ref],
                        nets=[net_name],
                        pins=[f"{number}:{pin.name}"],
                    )
                )
            else:
                validated += 1
        if pin.role in {"vdd", "vdda", "vref"} and volts is not None and volts > _VDD_MAX + 0.05:
            findings.append(
                Finding(
                    "fail",
                    "mcu.vdd_overvoltage",
                    f"{component.ref} {pin.name} (pin {number}) is on {net_name} at {format_volts(volts)}. "
                    f"STM32 VDD/VDDA absolute maximum is {_VDD_MAX:g} V.",
                    refs=[component.ref],
                    nets=[net_name],
                    pins=[f"{number}:{pin.name}"],
                )
            )
        elif pin.role == "io" and volts is not None and volts >= 4.5:
            if not pin.ft:
                findings.append(
                    Finding(
                        "fail",
                        "mcu.ft_violation",
                        f"{component.ref} {pin.name} is not 5 V tolerant and sits on {net_name} at {format_volts(volts)}.",
                        refs=[component.ref],
                        nets=[net_name],
                        pins=[f"{number}:{pin.name}"],
                    )
                )
            else:
                findings.append(
                    Finding(
                        "info",
                        "mcu.ft_ok",
                        f"{component.ref} {pin.name} is 5 V tolerant and sits on {net_name} at {format_volts(volts)}. "
                        "That is allowed only while VDD is present.",
                        refs=[component.ref],
                        nets=[net_name],
                        pins=[f"{number}:{pin.name}"],
                    )
                )
        if pin.role == "vcap":
            findings.extend(_vcap_pin(component, pin, number, net_name, netlist))
        if pin.role == "gnd" and volts is not None and volts > 0.2:
            findings.append(
                Finding(
                    "fail",
                    "mcu.ground_not_ground",
                    f"{component.ref} {pin.name} is on {net_name} ({format_volts(volts)}), not ground.",
                    refs=[component.ref],
                    nets=[net_name],
                    pins=[f"{number}:{pin.name}"],
                )
            )
        if function and pin.role == "io":
            rows.append(
                {
                    "ref": component.ref,
                    "mcu": mcu.id,
                    "pin": number,
                    "pin_name": pin.name,
                    "net": net_name,
                    "function": function,
                    "af": None if offered is None else offered.af,
                    "ok": ok,
                }
            )

    for function, places in sorted(claimed.items()):
        if len(places) > 1:
            findings.append(
                Finding(
                    "fail",
                    "mcu.duplicate_function",
                    f"{function} is assigned on {len(places)} pins of {component.ref}: {', '.join(places)}.",
                    refs=[component.ref],
                    pins=places,
                )
            )

    if absent_from_symbol:
        findings.append(
            Finding(
                "info",
                "mcu.symbol_incomplete",
                f"{component.ref} symbol does not define {len(absent_from_symbol)} power or strap pin(s) from {mcu.id}.",
                refs=[component.ref],
                details=", ".join(absent_from_symbol[:16]),
            )
        )

    if validated:
        findings.append(
            Finding(
                "pass",
                "mcu.functions_ok",
                f"{component.ref} ({mcu.id}, {mcu.package}): {validated} peripheral net(s) match the pin database.",
                refs=[component.ref],
            )
        )

    findings.extend(_vdda_filter(component, vdd_nets, vdda_nets, links))
    _debug_reuse(component, mcu, connected, findings)
    return findings, rows


def _vcap_pin(component, pin, number, net_name, netlist: Netlist) -> list[Finding]:
    net = netlist.net(net_name)
    if net is None:
        return []
    # The connectivity check owns the capacitor-value wording. Here we only
    # flag a VCAP pin that shares a net with VDD or ground, because that is
    # a pin-configuration error rather than a missing part.
    shares_vdd = any(re.match(r"(?i)^VDD", node.pin_name or "") for node in net.nodes)
    if is_ground(net_name) or shares_vdd:
        return [
            Finding(
                "fail",
                "mcu.vcap_shorted",
                f"{component.ref} {pin.name} (pin {number}) is on {net_name}. VCAP must be a private capacitor to ground, not a supply or ground net.",
                refs=[component.ref],
                nets=[net_name],
                pins=[f"{number}:{pin.name}"],
            )
        ]
    return []


def _vdda_filter(component, vdd_nets: set[str], vdda_nets: set[str], links) -> list[Finding]:
    if not vdda_nets:
        return []
    findings = []
    for vdda in vdda_nets:
        if vdda in vdd_nets:
            findings.append(
                Finding(
                    "info",
                    "mcu.vdda_unfiltered",
                    f"{component.ref} VDDA is on {vdda}, the same net as VDD. A ferrite or small resistor between them is the usual STM32 arrangement.",
                    refs=[component.ref],
                    nets=[vdda],
                )
            )
            continue
        filtered = [
            link
            for link in links
            if {link.a, link.b} & {vdda} and {link.a, link.b} & vdd_nets
        ]
        if filtered:
            link = filtered[0]
            findings.append(
                Finding(
                    "pass",
                    "mcu.vdda_filtered",
                    f"{component.ref} VDDA ({vdda}) is isolated from the digital rail by {link.ref}.",
                    refs=[component.ref, link.ref],
                    nets=[vdda, link.a, link.b],
                )
            )
    return findings


def _debug_reuse(component, mcu: Mcu, connected, findings: list[Finding]) -> None:
    for number, pin in mcu.pins.items():
        if pin.name not in {"PA13", "PA14"}:
            continue
        pair = connected.get(number)
        if pair is None:
            continue
        _node, net_name = pair
        function = resolve_function(net_name, mcu)
        if function is None:
            continue
        if function in {"SWDIO", "SWCLK", "JTMS", "JTCK"}:
            continue
        findings.append(
            Finding(
                "warn",
                "mcu.debug_reused",
                f"{component.ref} {pin.name} is the SWD pin and the net {net_name} asks for {function}. "
                "You will lose the debugger while that function is selected.",
                refs=[component.ref],
                nets=[net_name],
                pins=[f"{number}:{pin.name}"],
            )
        )
