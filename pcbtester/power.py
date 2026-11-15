"""Power-rail structure recovered from a netlist.

This is connectivity, not simulation. A regulator is recognised from its
library, its value, or VIN/VOUT (or SW + inductor) pins. Voltage comes
from the net name or a fixed-output part number. Placement is invisible
from a netlist, so decoupling is counted per rail, not per millimetre.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pcbtester.elec import (
    is_battery_name,
    is_ground,
    net_voltage,
    parse_capacitance,
    parse_resistance,
    voltage_from_part,
)
from pcbtester.kicad.netlist import Component, Netlist

_IN_PINS = {"VIN", "VI", "IN"}
_OUT_PINS = {"VOUT", "VO", "OUT"}
_REG_VALUE = re.compile(
    r"(?i)(AMS1117|AP2112|AP7361|AP7365|LDL1117|LDL212|LM1117|NCP1117|"
    r"TLV1117|XC6206|ME6211|RT9013|MIC5365|TPS629|TPS621|TPS543|TPS7|"
    r"MP1584|MP2315|MP2359|SY8303|LMR336|LT1963|ISL85005|AP63205|"
    r"LM2596|LM2678|NCP1117|SPX3819|MIC5504|AP2112K)"
)


@dataclass
class Regulator:
    ref: str
    value: str
    kind: str
    input_net: str | None
    output_net: str | None
    expected_voltage: float | None
    enable_net: str | None = None

    def to_dict(self) -> dict:
        return {
            "ref": self.ref,
            "value": self.value,
            "kind": self.kind,
            "input_net": self.input_net,
            "output_net": self.output_net,
            "expected_voltage": self.expected_voltage,
            "enable_net": self.enable_net,
        }


@dataclass
class SeriesLink:
    ref: str
    kind: str  # ferrite, zero_ohm
    a: str
    b: str

    def to_dict(self) -> dict:
        return {"ref": self.ref, "kind": self.kind, "a": self.a, "b": self.b}


@dataclass
class Rail:
    net: str
    voltage: float | None
    sourced: bool
    sources: list[str] = field(default_factory=list)
    loads: list[str] = field(default_factory=list)
    local_caps: list[str] = field(default_factory=list)
    bulk_caps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "net": self.net,
            "voltage": self.voltage,
            "sourced": self.sourced,
            "sources": self.sources,
            "loads": self.loads,
            "local_caps": self.local_caps,
            "bulk_caps": self.bulk_caps,
        }


def pin_nets(netlist: Netlist) -> dict[str, dict[str, str]]:
    """ref -> pin number -> net name."""
    mapping: dict[str, dict[str, str]] = {ref: {} for ref in netlist.components}
    for net in netlist.nets:
        for node in net.nodes:
            mapping.setdefault(node.ref, {})[node.pin] = net.name
    return mapping


def _named_pin(component: Component, candidates: set[str]) -> str | None:
    for pin in component.pins.values():
        if pin.name.upper().replace("+", "") in candidates or pin.name.upper() in candidates:
            return pin.num
    return None


def _is_passive_ref(ref: str, prefix: str) -> bool:
    return bool(re.match(rf"(?i){prefix}\d", ref))


def is_resistor(component: Component) -> bool:
    if _is_passive_ref(component.ref, "R") and not component.ref.upper().startswith("RN"):
        return True
    return parse_resistance(component.value) is not None and component.ref.upper().startswith("R")


def is_capacitor(component: Component) -> bool:
    if _is_passive_ref(component.ref, "C"):
        return True
    return False


def is_ferrite(component: Component) -> bool:
    blob = f"{component.ref} {component.value} {component.part} {component.description}".lower()
    if component.ref.upper().startswith("FB"):
        return True
    return "ferrite" in blob or "@100mhz" in blob.replace(" ", "")


def is_inductor(component: Component) -> bool:
    return _is_passive_ref(component.ref, "L")


def is_connector(component: Component) -> bool:
    ref = component.ref.upper()
    blob = f"{component.lib} {component.part} {component.value}".lower()
    return ref.startswith("J") or ref.startswith("P") or "conn_" in blob or blob.startswith("conn")


def is_regulator(component: Component) -> bool:
    """A part is a regulator when its library or value says so.

    Pin names alone are not enough: video chips use VIN/VOUT for the signal,
    and that was tripping the detector.
    """
    blob = f"{component.lib} {component.part} {component.value} {component.description}"
    if "regulator" in blob.lower():
        return True
    if _REG_VALUE.search(blob):
        return True
    return False


def _two_nets(component: Component, nets_of: dict[str, dict[str, str]]) -> list[str]:
    pins = nets_of.get(component.ref, {})
    ordered = []
    for num in sorted(pins, key=lambda item: (len(item), item)):
        net = pins[num]
        if net not in ordered:
            ordered.append(net)
    return ordered


def find_regulators(netlist: Netlist, nets_of: dict[str, dict[str, str]] | None = None) -> list[Regulator]:
    nets_of = nets_of if nets_of is not None else pin_nets(netlist)
    found: list[Regulator] = []
    for component in netlist.components.values():
        if not is_regulator(component):
            continue
        pins = nets_of.get(component.ref, {})
        in_num = _named_pin(component, _IN_PINS)
        out_num = _named_pin(component, _OUT_PINS)
        en_num = _named_pin(component, {"EN", "ENABLE", "CE", "SHDN"})
        names = {pin.name.upper(): pin.num for pin in component.pins.values()}
        kind = "ldo"
        input_net = pins.get(in_num) if in_num else None
        output_net = pins.get(out_num) if out_num else None
        if "SW" in names and out_num is None:
            kind = "switcher"
            sw_net = pins.get(names["SW"])
            output_net = _inductor_far_side(netlist, nets_of, sw_net)
        elif "SW" in names:
            kind = "switcher"
        expected = voltage_from_part(component.value) or voltage_from_part(component.part)
        found.append(
            Regulator(
                ref=component.ref,
                value=component.value or component.part,
                kind=kind,
                input_net=input_net,
                output_net=output_net,
                expected_voltage=expected,
                enable_net=pins.get(en_num) if en_num else None,
            )
        )
    return found


def _inductor_far_side(
    netlist: Netlist, nets_of: dict[str, dict[str, str]], sw_net: str | None
) -> str | None:
    if not sw_net:
        return None
    for component in netlist.components.values():
        if not is_inductor(component):
            continue
        ends = _two_nets(component, nets_of)
        if len(ends) == 2 and sw_net in ends:
            return ends[0] if ends[1] == sw_net else ends[1]
    return None


def find_series_links(netlist: Netlist, nets_of: dict[str, dict[str, str]] | None = None) -> list[SeriesLink]:
    nets_of = nets_of if nets_of is not None else pin_nets(netlist)
    links: list[SeriesLink] = []
    for component in netlist.components.values():
        ends = _two_nets(component, nets_of)
        if len(ends) != 2 or ends[0] == ends[1]:
            continue
        if is_ground(ends[0]) or is_ground(ends[1]):
            continue
        if is_ferrite(component):
            links.append(SeriesLink(component.ref, "ferrite", ends[0], ends[1]))
            continue
        if is_resistor(component):
            ohms = parse_resistance(component.value)
            # 0R jumpers tie rails. A few ohms is the usual series filter
            # between a rail and VDDA / an IMU, not an I2C series resistor.
            # 22 Ω still shows up as a supply filter. 33 Ω on this class of
            # board is a series resistor on a SPI or I2C line, so it stops there.
            if ohms is not None and ohms <= 22.0:
                kind = "zero_ohm" if ohms <= 1.0 else "filter"
                links.append(SeriesLink(component.ref, kind, ends[0], ends[1]))
    return links


def _power_out_refs(netlist: Netlist, net_name: str) -> list[str]:
    net = netlist.net(net_name)
    if net is None:
        return []
    refs = []
    for node in net.nodes:
        if node.pin_type == "power_out":
            label = f"{node.ref}.{node.pin_name or node.pin}"
            if label not in refs:
                refs.append(label)
    return refs


def estimate_voltages(netlist: Netlist) -> dict[str, float]:
    nets_of = pin_nets(netlist)
    voltages: dict[str, float] = {}
    for net in netlist.nets:
        parsed = net_voltage(net.name)
        if parsed is not None:
            voltages[net.name] = parsed
    for regulator in find_regulators(netlist, nets_of):
        if regulator.expected_voltage is not None and regulator.output_net:
            voltages[regulator.output_net] = regulator.expected_voltage
    links = find_series_links(netlist, nets_of)
    changed = True
    while changed:
        changed = False
        for link in links:
            if link.a in voltages and link.b not in voltages:
                voltages[link.b] = voltages[link.a]
                changed = True
            elif link.b in voltages and link.a not in voltages:
                voltages[link.a] = voltages[link.b]
                changed = True
    return voltages


def _caps_on_net(netlist: Netlist, nets_of: dict[str, dict[str, str]], net_name: str) -> tuple[list[str], list[str]]:
    local: list[str] = []
    bulk: list[str] = []
    for component in netlist.components.values():
        if not is_capacitor(component):
            continue
        ends = _two_nets(component, nets_of)
        if net_name not in ends:
            continue
        other = [end for end in ends if end != net_name]
        if not other or not is_ground(other[0]):
            continue
        farads = parse_capacitance(component.value)
        label = component.ref if farads is None else f"{component.ref} ({component.value})"
        if farads is None:
            local.append(label)
        elif farads > 1e-6:
            bulk.append(label)
        elif farads >= 1e-9:
            local.append(label)
        else:
            # picofarad parts are not supply decoupling
            continue
    return local, bulk


def source_map(netlist: Netlist) -> dict[str, list[str]]:
    """Net name -> human labels of what sources it."""
    nets_of = pin_nets(netlist)
    regulators = find_regulators(netlist, nets_of)
    links = find_series_links(netlist, nets_of)
    sources: dict[str, list[str]] = {net.name: [] for net in netlist.nets}

    def add(net_name: str | None, label: str) -> None:
        if not net_name or net_name not in sources:
            return
        if label not in sources[net_name]:
            sources[net_name].append(label)

    for net in netlist.nets:
        for label in _power_out_refs(netlist, net.name):
            add(net.name, label)
        if is_battery_name(net.name) or (net_voltage(net.name) == 5.0 and _clean_connector(netlist, net.name)):
            for node in net.nodes:
                component = netlist.component(node.ref)
                if component and is_connector(component):
                    add(net.name, f"{node.ref} connector")
        if is_battery_name(net.name):
            # A named battery net is a source even without a connector symbol.
            if not sources[net.name]:
                add(net.name, "battery net name")

    for regulator in regulators:
        if regulator.output_net:
            volts = f" {regulator.expected_voltage:g} V" if regulator.expected_voltage is not None else ""
            add(regulator.output_net, f"{regulator.ref} {regulator.value}{volts}".strip())

    changed = True
    while changed:
        changed = False
        for link in links:
            if sources[link.a] and not sources[link.b]:
                add(link.b, f"through {link.ref}")
                changed = True
            elif sources[link.b] and not sources[link.a]:
                add(link.a, f"through {link.ref}")
                changed = True
    return sources


def _clean_connector(netlist: Netlist, net_name: str) -> bool:
    net = netlist.net(net_name)
    if net is None:
        return False
    return any(is_connector(netlist.components[node.ref]) for node in net.nodes if node.ref in netlist.components)


def build_power_tree(netlist: Netlist) -> dict:
    nets_of = pin_nets(netlist)
    regulators = find_regulators(netlist, nets_of)
    links = find_series_links(netlist, nets_of)
    voltages = estimate_voltages(netlist)
    sources = source_map(netlist)
    rails: list[Rail] = []
    for net in netlist.nets:
        if is_ground(net.name):
            continue
        loads = []
        interesting = False
        for node in net.nodes:
            if node.pin_type in {"power_in", "power_out"}:
                interesting = True
                if node.pin_type == "power_in":
                    loads.append(f"{node.ref}.{node.pin_name or node.pin}")
        if net.name in voltages or sources.get(net.name):
            interesting = True
        if not interesting:
            continue
        local, bulk = _caps_on_net(netlist, nets_of, net.name)
        rails.append(
            Rail(
                net=net.name,
                voltage=voltages.get(net.name),
                sourced=bool(sources.get(net.name)),
                sources=sources.get(net.name, []),
                loads=loads,
                local_caps=local,
                bulk_caps=bulk,
            )
        )
    rails.sort(key=lambda rail: (rail.voltage is None, -(rail.voltage or 0), rail.net))
    return {
        "regulators": [item.to_dict() for item in regulators],
        "links": [item.to_dict() for item in links],
        "rails": [item.to_dict() for item in rails],
        "voltages": voltages,
    }


def resistor_between(netlist: Netlist, nets_of: dict[str, dict[str, str]], a: str, b: str) -> list[Component]:
    found = []
    for component in netlist.components.values():
        if not is_resistor(component):
            continue
        ends = _two_nets(component, nets_of)
        if len(ends) == 2 and set(ends) == {a, b}:
            found.append(component)
    return found


def shunt_resistors(netlist: Netlist, nets_of: dict[str, dict[str, str]], net_name: str) -> list[tuple[Component, str]]:
    """Resistors with one end on net_name. Returns (component, other_net)."""
    found = []
    for component in netlist.components.values():
        if not is_resistor(component):
            continue
        ends = _two_nets(component, nets_of)
        if len(ends) == 2 and net_name in ends:
            other = ends[0] if ends[1] == net_name else ends[1]
            found.append((component, other))
    return found
