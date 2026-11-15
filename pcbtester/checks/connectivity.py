"""Netlist checks for the mistakes ERC leaves alone.

ERC knows pin types. It does not know that BOOT0 should be strapped, that
each VDD pin wants a local capacitor, or that an I2C net with no resistor
to a rail will not idle high. Those rules live here, and they are
heuristics: a netlist has no coordinates, so "the capacitor is on the
other side of the board" cannot be seen.
"""

from __future__ import annotations

import re

from pcbtester.checks.base import Check
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.elec import is_ground, net_voltage, parse_capacitance, parse_resistance
from pcbtester.kicad.netlist import Net, Netlist, Node
from pcbtester.models import CheckResult, Finding, rollup
from pcbtester.power import (
    build_power_tree,
    is_capacitor,
    is_resistor,
    pin_nets,
    shunt_resistors,
)

_I2C = re.compile(r"(?i)(?:^|_)(SDA|SCL)(?:$|_)")
_CRITICAL_PIN = re.compile(
    r"(?i)^(VDD|VDD[A-Z0-9]*|VDDA|VREF\+|VREF|VBAT|VCAP\d*|NRST|NRESET|RESET|BOOT0)$"
)
_POWER_PIN = re.compile(r"(?i)^(VDD|VDD[A-Z0-9]*|VDDA|VCC|AVDD|DVDD)$")
_VCAP = re.compile(r"(?i)^VCAP\d*$")
_BOOT = re.compile(r"(?i)^BOOT0$")
_RESET = re.compile(r"(?i)^(NRST|NRESET|RESET)$")


class ConnectivityCheck(Check):
    id = "connectivity"
    title = "Connectivity and power"
    description = (
        "Parses the KiCad netlist and checks unconnected power and strap pins, "
        "I2C pull-ups, reset and BOOT0, a capacitor to ground on each VDD rail, "
        "VCAP wiring, and rails that have loads but no source. Builds a power-rail "
        "tree from regulators, ferrites and net names. Does not do geometric "
        "decoupling (a 100 nF on the same net counts, wherever it sits) and does "
        "not replace the MCU pinmux check."
    )

    def run(self, ctx: ProjectContext, options: RunOptions) -> CheckResult:
        del options
        if ctx.netlist is None:
            message = ctx.netlist_error or "No netlist is available."
            return CheckResult(self.id, self.title, "error", message)
        findings, tree = analyze_connectivity(ctx.netlist)
        status = rollup(findings)
        fails = sum(1 for item in findings if item.severity == "fail")
        warns = sum(1 for item in findings if item.severity == "warn")
        if status == "pass":
            summary = "Power straps, pull-ups and rail sources look consistent with the netlist."
        else:
            summary = f"{fails} connectivity error(s), {warns} warning(s)."
        return CheckResult(self.id, self.title, status, summary, findings=findings, artifacts={"power": tree})


def analyze_connectivity(netlist: Netlist) -> tuple[list[Finding], dict]:
    findings: list[Finding] = []
    nets_of = pin_nets(netlist)
    tree = build_power_tree(netlist)
    voltages: dict[str, float] = tree["voltages"]

    findings.extend(_unconnected(netlist))
    findings.extend(_i2c_pullups(netlist, nets_of, voltages))
    findings.extend(_boot_and_reset(netlist, nets_of, voltages))
    findings.extend(_vcap(netlist, nets_of))
    findings.extend(_decoupling(netlist, nets_of))
    findings.extend(_unsourced_rails(tree))
    findings.extend(_regulator_notes(tree))
    return findings, tree


def _pin_label(node: Node) -> str:
    name = node.pin_name or node.pin
    return f"{node.ref}.{name}"


def _unconnected(netlist: Netlist) -> list[Finding]:
    findings: list[Finding] = []
    floating_gpio: list[str] = []
    for net in netlist.nets:
        if len(net.nodes) != 1:
            continue
        node = net.nodes[0]
        if node.pin_type in {"no_connect", "free"}:
            continue
        label = _pin_label(node)
        name = node.pin_name or ""
        if _BOOT.match(name) or _RESET.match(name) or _VCAP.match(name):
            # Dedicated strap checks report these. Skipping avoids a second finding.
            continue
        if _CRITICAL_PIN.match(name) or node.pin_type == "power_in":
            findings.append(
                Finding(
                    "fail",
                    "conn.floating_power",
                    f"{label} is the only pin on {net.name}.",
                    refs=[node.ref],
                    nets=[net.name],
                    pins=[name or node.pin],
                )
            )
            continue
        if node.pin_type == "input":
            findings.append(
                Finding(
                    "warn",
                    "conn.floating_input",
                    f"Input {label} is unconnected ({net.name}).",
                    refs=[node.ref],
                    nets=[net.name],
                    pins=[name or node.pin],
                )
            )
            continue
        if node.pin_type in {"bidirectional", "tri_state", "unspecified", "output"} or net.name.startswith(
            "unconnected"
        ):
            floating_gpio.append(label)
    if floating_gpio:
        preview = ", ".join(floating_gpio[:12])
        extra = "" if len(floating_gpio) <= 12 else f" (+{len(floating_gpio) - 12} more)"
        findings.append(
            Finding(
                "info",
                "conn.unused_pins",
                f"{len(floating_gpio)} pin(s) are on single-node nets. Unused GPIOs are normal; power and strap pins are reported separately.",
                details=preview + extra,
                refs=_unique_refs(floating_gpio),
            )
        )
    return findings


def _unique_refs(labels: list[str]) -> list[str]:
    refs = []
    for label in labels:
        ref = label.split(".", 1)[0]
        if ref not in refs:
            refs.append(ref)
    return refs[:8]


def _i2c_pullups(netlist: Netlist, nets_of: dict, voltages: dict[str, float]) -> list[Finding]:
    findings: list[Finding] = []
    i2c_nets = []
    seen: set[str] = set()
    for net in netlist.nets:
        if net.name in seen or not _is_i2c_net(net):
            continue
        seen.add(net.name)
        i2c_nets.append(net)
    direct: dict[str, list[tuple]] = {}
    for net in i2c_nets:
        pullups = []
        for component, other in shunt_resistors(netlist, nets_of, net.name):
            other_v = voltages.get(other)
            if is_ground(other):
                continue
            if other_v is None and not _looks_like_positive_rail(other, netlist):
                continue
            if other_v is not None and other_v < 1.0:
                continue
            ohms = parse_resistance(component.value)
            # A 33 Ω series resistor is not a pull-up. Those are handled below.
            if ohms is not None and ohms < 500:
                continue
            pullups.append((component, other))
        if pullups:
            direct[net.name] = pullups
    # Pull-up on the MCU side of a series resistor still biases the device side.
    series: list[tuple[str, str, object]] = []
    for component in netlist.components.values():
        if not is_resistor(component):
            continue
        ohms = parse_resistance(component.value)
        if ohms is None or not 0 < ohms <= 100:
            continue
        ends = []
        for net_name in nets_of.get(component.ref, {}).values():
            if net_name not in ends:
                ends.append(net_name)
        if len(ends) == 2:
            series.append((ends[0], ends[1], component))
    covered = set(direct)
    via: dict[str, tuple] = {}
    changed = True
    while changed:
        changed = False
        for left, right, component in series:
            if left in covered and right not in covered:
                covered.add(right)
                via[right] = (component, left)
                changed = True
            elif right in covered and left not in covered:
                covered.add(left)
                via[left] = (component, right)
                changed = True
    for net in i2c_nets:
        if net.name in direct:
            for component, other in direct[net.name]:
                ohms = parse_resistance(component.value)
                if ohms is not None and (ohms < 500 or ohms > 20000):
                    findings.append(
                        Finding(
                            "warn",
                            "conn.i2c_pullup_value",
                            f"{component.ref} on {net.name} is {component.value}, outside the usual 1 kΩ–10 kΩ I2C range.",
                            refs=[component.ref],
                            nets=[net.name, other],
                        )
                    )
                else:
                    findings.append(
                        Finding(
                            "pass",
                            "conn.i2c_pullup",
                            f"{net.name} has pull-up {component.ref} ({component.value}) to {other}.",
                            refs=[component.ref],
                            nets=[net.name, other],
                        )
                    )
            continue
        if net.name in via:
            component, other_net = via[net.name]
            findings.append(
                Finding(
                    "pass",
                    "conn.i2c_pullup",
                    f"{net.name} is biased through series resistor {component.ref} ({component.value}) from {other_net}, which has a pull-up.",
                    refs=[component.ref],
                    nets=[net.name, other_net],
                )
            )
            continue
        findings.append(
            Finding(
                "warn",
                "conn.i2c_pullup_missing",
                f"{net.name} looks like an I2C line and has no resistor to a positive rail.",
                nets=[net.name],
                refs=[node.ref for node in net.nodes][:8],
            )
        )
    return findings


def _is_i2c_net(net: Net) -> bool:
    if _I2C.search(net.name):
        return True
    return any(_I2C.search(node.pin_name or "") for node in net.nodes)


def _looks_like_positive_rail(name: str, netlist: Netlist) -> bool:
    if net_voltage(name) and net_voltage(name) > 1:
        return True
    net = netlist.net(name)
    if net is None:
        return False
    return any(node.pin_type == "power_out" for node in net.nodes)


def _boot_and_reset(netlist: Netlist, nets_of: dict, voltages: dict[str, float]) -> list[Finding]:
    findings: list[Finding] = []
    for net in netlist.nets:
        for node in net.nodes:
            name = node.pin_name or ""
            if _BOOT.match(name):
                findings.extend(_boot_pin(netlist, nets_of, voltages, net, node))
            elif _RESET.match(name):
                findings.extend(_reset_pin(netlist, nets_of, net, node))
    return findings


def _boot_pin(netlist, nets_of, voltages, net: Net, node: Node) -> list[Finding]:
    label = _pin_label(node)
    if is_ground(net.name) or (voltages.get(net.name) == 0):
        return [Finding("pass", "conn.boot_low", f"{label} is tied to ground (boot from flash).", refs=[node.ref], nets=[net.name], pins=[node.pin_name])]
    resistors = shunt_resistors(netlist, nets_of, net.name)
    to_ground = [(comp, other) for comp, other in resistors if is_ground(other)]
    to_rail = [
        (comp, other)
        for comp, other in resistors
        if not is_ground(other) and (voltages.get(other) or 0) > 1
    ]
    volts = voltages.get(net.name)
    if volts is not None and volts > 2.5 and not to_ground:
        return [
            Finding(
                "warn",
                "conn.boot_high",
                f"{label} sits on {net.name} ({volts:g} V). The part will boot the system loader instead of flash.",
                refs=[node.ref],
                nets=[net.name],
                pins=[node.pin_name],
            )
        ]
    if to_ground and not to_rail:
        comp = to_ground[0][0]
        return [
            Finding(
                "pass",
                "conn.boot_low",
                f"{label} is strapped low by {comp.ref} ({comp.value}).",
                refs=[node.ref, comp.ref],
                nets=[net.name],
                pins=[node.pin_name],
            )
        ]
    if to_ground and to_rail:
        return [
            Finding(
                "info",
                "conn.boot_divider",
                f"{label} has resistors to a rail and to ground. Confirm the divider holds BOOT0 low for a normal boot.",
                refs=[node.ref, to_ground[0][0].ref, to_rail[0][0].ref],
                nets=[net.name, to_rail[0][1]],
                pins=[node.pin_name],
            )
        ]
    if len(net.nodes) == 1:
        return [
            Finding(
                "fail",
                "conn.boot_floating",
                f"{label} is floating. Strap BOOT0 low (directly or through a resistor) to boot from flash.",
                refs=[node.ref],
                nets=[net.name],
                pins=[node.pin_name],
            )
        ]
    return [
        Finding(
            "warn",
            "conn.boot_unknown",
            f"{label} on {net.name} is not an obvious strap to ground.",
            refs=[node.ref],
            nets=[net.name],
            pins=[node.pin_name],
        )
    ]


def _reset_pin(netlist, nets_of, net: Net, node: Node) -> list[Finding]:
    label = _pin_label(node)
    caps = _caps_to_ground(netlist, nets_of, net.name)
    pullups = [
        (comp, other)
        for comp, other in shunt_resistors(netlist, nets_of, net.name)
        if not is_ground(other)
    ]
    findings: list[Finding] = []
    if caps:
        cap = caps[0]
        findings.append(
            Finding(
                "pass",
                "conn.reset_cap",
                f"{label} has {cap.ref} ({cap.value}) to ground.",
                refs=[node.ref, cap.ref],
                nets=[net.name],
                pins=[node.pin_name],
            )
        )
    else:
        findings.append(
            Finding(
                "warn",
                "conn.reset_cap_missing",
                f"{label} has no capacitor to ground. STM32 boards usually fit 100 nF on NRST.",
                refs=[node.ref],
                nets=[net.name],
                pins=[node.pin_name],
            )
        )
    if pullups:
        comp, other = pullups[0]
        findings.append(
            Finding(
                "info",
                "conn.reset_pullup",
                f"{label} has external pull-up {comp.ref} ({comp.value}) to {other}. STM32 NRST also has an internal pull-up.",
                refs=[node.ref, comp.ref],
                nets=[net.name, other],
                pins=[node.pin_name],
            )
        )
    elif len(net.nodes) == 1:
        findings.append(
            Finding(
                "fail",
                "conn.reset_floating",
                f"{label} is floating.",
                refs=[node.ref],
                nets=[net.name],
                pins=[node.pin_name],
            )
        )
    return findings


def _caps_to_ground(netlist: Netlist, nets_of: dict, net_name: str) -> list:
    found = []
    for component in netlist.components.values():
        if not is_capacitor(component):
            continue
        pins = nets_of.get(component.ref, {})
        ends = list(dict.fromkeys(pins.values()))
        if net_name in ends and any(is_ground(end) for end in ends):
            found.append(component)
    return found


def _vcap(netlist: Netlist, nets_of: dict) -> list[Finding]:
    findings: list[Finding] = []
    for net in netlist.nets:
        vcap_nodes = [node for node in net.nodes if _VCAP.match(node.pin_name or "")]
        if not vcap_nodes:
            continue
        vdd_nodes = [node for node in net.nodes if _POWER_PIN.match(node.pin_name or "")]
        labels = ", ".join(_pin_label(node) for node in vcap_nodes)
        if is_ground(net.name):
            findings.append(
                Finding(
                    "fail",
                    "conn.vcap_ground",
                    f"{labels} is shorted to ground. VCAP needs its own capacitor, not a ground pour.",
                    refs=[node.ref for node in vcap_nodes],
                    nets=[net.name],
                    pins=[node.pin_name for node in vcap_nodes],
                )
            )
            continue
        if vdd_nodes:
            findings.append(
                Finding(
                    "fail",
                    "conn.vcap_on_vdd",
                    f"{labels} shares {net.name} with a VDD pin. The internal regulator output must not be tied to VDD.",
                    refs=list({node.ref for node in vcap_nodes + vdd_nodes}),
                    nets=[net.name],
                    pins=[node.pin_name for node in vcap_nodes],
                )
            )
            continue
        caps = _caps_to_ground(netlist, nets_of, net.name)
        if not caps:
            findings.append(
                Finding(
                    "warn",
                    "conn.vcap_cap_missing",
                    f"{labels} has no capacitor to ground. STM32H7 wants about 2.2 µF on each VCAP pin.",
                    refs=[node.ref for node in vcap_nodes],
                    nets=[net.name],
                    pins=[node.pin_name for node in vcap_nodes],
                )
            )
            continue
        good = []
        odd = []
        for cap in caps:
            farads = parse_capacitance(cap.value)
            if farads is None or 1e-6 <= farads <= 10e-6:
                good.append(cap)
            else:
                odd.append(cap)
        if good:
            cap = good[0]
            findings.append(
                Finding(
                    "pass",
                    "conn.vcap",
                    f"{labels} is decoupled by {cap.ref} ({cap.value}) to ground.",
                    refs=list({node.ref for node in vcap_nodes} | {cap.ref}),
                    nets=[net.name],
                    pins=[node.pin_name for node in vcap_nodes],
                )
            )
        if odd:
            cap = odd[0]
            findings.append(
                Finding(
                    "warn",
                    "conn.vcap_value",
                    f"{cap.ref} on {labels} is {cap.value}. STM32H7 VCAP capacitors are typically 2.2 µF.",
                    refs=[cap.ref],
                    nets=[net.name],
                )
            )
    return findings


def _decoupling(netlist: Netlist, nets_of: dict) -> list[Finding]:
    """Count local ceramics per (IC, supply net). One finding per group."""
    groups: dict[tuple[str, str], list[Node]] = {}
    for net in netlist.nets:
        if is_ground(net.name):
            continue
        for node in net.nodes:
            if not _POWER_PIN.match(node.pin_name or ""):
                continue
            if node.pin_type not in {"power_in", "power_out", ""}:
                continue
            groups.setdefault((node.ref, net.name), []).append(node)
    findings: list[Finding] = []
    for (ref, net_name), nodes in sorted(groups.items()):
        caps = _caps_to_ground(netlist, nets_of, net_name)
        local = []
        for cap in caps:
            farads = parse_capacitance(cap.value)
            if farads is None or 1e-9 <= farads <= 1e-6:
                local.append(cap)
        pins = ", ".join(sorted({node.pin_name or node.pin for node in nodes}))
        if len(local) >= len(nodes):
            findings.append(
                Finding(
                    "pass",
                    "conn.decoupling",
                    f"{ref} has {len(local)} local capacitor(s) on {net_name} for {len(nodes)} supply pin(s) ({pins}).",
                    refs=[ref, *[cap.ref for cap in local[:6]]],
                    nets=[net_name],
                    pins=[node.pin_name for node in nodes if node.pin_name],
                )
            )
        else:
            findings.append(
                Finding(
                    "warn",
                    "conn.decoupling_short",
                    f"{ref} has {len(nodes)} supply pin(s) on {net_name} ({pins}) and {len(local)} local capacitor(s) to ground. "
                    "A netlist cannot see whether a capacitor is next to the pin.",
                    refs=[ref, *[cap.ref for cap in local[:6]]],
                    nets=[net_name],
                    pins=[node.pin_name for node in nodes if node.pin_name],
                )
            )
    return findings


def _filtered_children(tree: dict) -> set[str]:
    """Rails reached only through a ferrite or a few-ohm filter.

    The missing-source warning is kept on the rail in that group with the
    most loads (the one the others hang off). Filtered copies are not
    repeated.
    """
    parent: dict[str, str] = {}

    def find(name: str) -> str:
        parent.setdefault(name, name)
        if parent[name] != name:
            parent[name] = find(parent[name])
        return parent[name]

    def union(left: str, right: str) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for link in tree.get("links") or []:
        union(link["a"], link["b"])
    groups: dict[str, list] = {}
    for rail in tree["rails"]:
        groups.setdefault(find(rail["net"]), []).append(rail)
    children: set[str] = set()
    for group in groups.values():
        unsourced = [rail for rail in group if not rail["sourced"] and rail.get("loads")]
        if len(unsourced) < 2:
            continue
        upstream = max(unsourced, key=lambda rail: len(rail["loads"]))
        for rail in unsourced:
            if rail["net"] != upstream["net"]:
                children.add(rail["net"])
    return children


def _unsourced_rails(tree: dict) -> list[Finding]:
    findings = []
    children = _filtered_children(tree)
    for rail in tree["rails"]:
        if rail["net"] in children and not rail["sourced"]:
            continue
        if rail["sourced"]:
            volts = f" at {rail['voltage']:g} V" if rail["voltage"] is not None else ""
            source = ", ".join(rail["sources"][:3]) or "a power output"
            findings.append(
                Finding(
                    "pass",
                    "conn.rail_source",
                    f"{rail['net']}{volts} is sourced by {source}.",
                    nets=[rail["net"]],
                    refs=_refs_from_labels(rail["sources"]),
                )
            )
            continue
        if not rail["loads"]:
            continue
        findings.append(
            Finding(
                "warn",
                "conn.rail_unsourced",
                f"{rail['net']} feeds {len(rail['loads'])} power input(s) and has no regulator output, power flag, or battery source.",
                nets=[rail["net"]],
                refs=_refs_from_labels(rail["loads"]),
                details=", ".join(rail["loads"][:12]),
            )
        )
    return findings


def _regulator_notes(tree: dict) -> list[Finding]:
    findings = []
    for reg in tree["regulators"]:
        if reg["output_net"] is None:
            findings.append(
                Finding(
                    "info",
                    "conn.regulator_unparsed",
                    f"{reg['ref']} ({reg['value']}) looks like a regulator, but its output net could not be identified from pin names.",
                    refs=[reg["ref"]],
                    details="LDO symbols with VIN/VOUT are followed. Switchers are followed through the inductor on SW.",
                )
            )
            continue
        volts = f", expected {reg['expected_voltage']:g} V" if reg["expected_voltage"] is not None else ""
        findings.append(
            Finding(
                "info",
                "conn.regulator",
                f"{reg['ref']} ({reg['value']}) {reg['kind']}: {reg['input_net'] or '?'} → {reg['output_net']}{volts}.",
                refs=[reg["ref"]],
                nets=[net for net in (reg["input_net"], reg["output_net"]) if net],
            )
        )
    return findings


def _refs_from_labels(labels: list[str]) -> list[str]:
    refs = []
    for label in labels:
        ref = label.split(".", 1)[0].split(" ", 1)[0]
        if ref and ref not in refs and not ref.startswith("through"):
            refs.append(ref)
    return refs[:8]
