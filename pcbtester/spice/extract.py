"""Find analog fragments a netlist can actually simulate.

A whole flight controller is not a SPICE deck. Dividers, RC filters and a
behavioral stand-in for a fixed regulator are. Anything we cannot ground
in a value is returned with ``simulatable=False`` and a reason, so the UI
can show it and the run can skip it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pcbtester.elec import is_battery_name, is_ground, net_voltage, parse_capacitance, parse_resistance
from pcbtester.kicad.netlist import Component, Netlist
from pcbtester.power import (
    find_regulators,
    is_capacitor,
    is_resistor,
    pin_nets,
    shunt_resistors,
)

_ASSUMED_BATTERY = 12.6


@dataclass
class Subcircuit:
    id: str
    kind: str
    title: str
    refs: list[str]
    nets: list[str]
    params: dict = field(default_factory=dict)
    simulatable: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "refs": self.refs,
            "nets": self.nets,
            "params": self.params,
            "simulatable": self.simulatable,
            "reason": self.reason,
        }


def _ends(component: Component, nets_of: dict[str, dict[str, str]]) -> list[str]:
    pins = nets_of.get(component.ref, {})
    ordered: list[str] = []
    for num in sorted(pins, key=lambda item: (len(item), item)):
        net = pins[num]
        if net not in ordered:
            ordered.append(net)
    return ordered


def _anonymous(name: str) -> bool:
    return name.startswith("Net-") or name.startswith("unconnected-")


def detect_subcircuits(netlist: Netlist, voltages: dict[str, float] | None = None, stimuli: dict[str, float] | None = None) -> list[Subcircuit]:
    voltages = dict(voltages or {})
    stimuli = stimuli or {}
    voltages.update({name: value for name, value in stimuli.items()})
    nets_of = pin_nets(netlist)
    resistors = [comp for comp in netlist.components.values() if is_resistor(comp)]
    dividers = _dividers(netlist, resistors, nets_of, voltages)
    divider_refs = {ref for item in dividers for ref in item.refs}
    filters = _rc_filters(netlist, resistors, nets_of, voltages, divider_refs)
    regulators = _regulators(netlist, nets_of, voltages)
    found = dividers + filters + regulators
    # Named, simulatable circuits first. Cap the list so a dense board stays readable.
    found.sort(key=lambda item: (not item.simulatable, _anonymous(item.nets[0]) if item.nets else True, item.id))
    return found[:40]


def _dividers(netlist, resistors, nets_of, voltages) -> list[Subcircuit]:
    found: list[Subcircuit] = []
    seen: set[tuple[str, str]] = set()
    for top in resistors:
        top_ends = _ends(top, nets_of)
        if len(top_ends) != 2:
            continue
        for bottom in resistors:
            if bottom.ref == top.ref:
                continue
            key = tuple(sorted((top.ref, bottom.ref)))
            if key in seen:
                continue
            bot_ends = _ends(bottom, nets_of)
            if len(bot_ends) != 2:
                continue
            shared = set(top_ends) & set(bot_ends)
            if len(shared) != 1:
                continue
            mid = next(iter(shared))
            if is_ground(mid):
                continue
            top_other = top_ends[0] if top_ends[1] == mid else top_ends[1]
            bot_other = bot_ends[0] if bot_ends[1] == mid else bot_ends[1]
            if is_ground(top_other) and not is_ground(bot_other):
                top, bottom = bottom, top
                top_other, bot_other = bot_other, top_other
            if not is_ground(bot_other) or is_ground(top_other):
                continue
            seen.add(key)
            r_top = parse_resistance(top.value)
            r_bot = parse_resistance(bottom.value)
            vin = voltages.get(top_other)
            assumed = False
            if vin is None and is_battery_name(top_other):
                vin = _ASSUMED_BATTERY
                assumed = True
            cap = _cap_to_ground(netlist, nets_of, mid)
            params = {
                "top_net": top_other,
                "mid_net": mid,
                "r_top": r_top,
                "r_bottom": r_bot,
                "r_top_ref": top.ref,
                "r_bottom_ref": bottom.ref,
                "vin": vin,
                "vin_assumed": assumed,
                "cap_f": parse_capacitance(cap.value) if cap else None,
                "cap_ref": cap.ref if cap else None,
            }
            simulatable = r_top is not None and r_bot is not None and vin is not None and (r_top + r_bot) > 0
            if r_top is None or r_bot is None:
                reason = "Resistor values could not be parsed."
            elif vin is None:
                reason = f"No voltage for {top_other}. Set a stimulus such as {top_other}=12.6."
            else:
                ratio = r_bot / (r_top + r_bot)
                reason = f"DC divider, V({mid}) = {vin:g} × {ratio:.4f}."
                if assumed:
                    reason += f" Assumed {vin:g} V on {top_other} (3S full)."
            found.append(
                Subcircuit(
                    id=f"divider:{top.ref}:{bottom.ref}",
                    kind="divider",
                    title=f"Divider {top.ref}/{bottom.ref} on {top_other}",
                    refs=[top.ref, bottom.ref] + ([cap.ref] if cap else []),
                    nets=[top_other, mid, bot_other],
                    params=params,
                    simulatable=simulatable,
                    reason=reason,
                )
            )
    return found


def _cap_to_ground(netlist: Netlist, nets_of, net_name: str) -> Component | None:
    best: tuple[float, Component] | None = None
    for component in netlist.components.values():
        if not is_capacitor(component):
            continue
        ends = _ends(component, nets_of)
        if net_name not in ends or not any(is_ground(end) for end in ends):
            continue
        farads = parse_capacitance(component.value) or 0
        if best is None or farads > best[0]:
            best = (farads, component)
    return best[1] if best else None


def _is_supply_net(netlist: Netlist, name: str) -> bool:
    if is_ground(name) or net_voltage(name) is not None:
        return True
    net = netlist.net(name)
    if net is None:
        return False
    return any(node.pin_type in {"power_in", "power_out"} for node in net.nodes)


def _rc_filters(netlist, resistors, nets_of, voltages, divider_refs: set[str]) -> list[Subcircuit]:
    found = []
    for resistor in resistors:
        if resistor.ref in divider_refs:
            continue
        ends = _ends(resistor, nets_of)
        if len(ends) != 2:
            continue
        ohms = parse_resistance(resistor.value)
        for index, out_net in enumerate(ends):
            if is_ground(out_net) or _anonymous(out_net):
                continue
            cap = _cap_to_ground(netlist, nets_of, out_net)
            if cap is None:
                continue
            farads = parse_capacitance(cap.value)
            if farads is None or farads < 100e-12:
                continue
            in_net = ends[1 - index]
            if is_ground(in_net):
                continue
            in_supply = _is_supply_net(netlist, in_net)
            out_supply = _is_supply_net(netlist, out_net)
            # A pull-up from a signal onto a rail, with the rail's bulk cap, is not a filter.
            if out_supply and not in_supply and (ohms is None or ohms >= 470):
                continue
            # Two resistors meeting on the capacitor node are a bias network, not an RC.
            others = [
                other
                for other, far in shunt_resistors(netlist, nets_of, out_net)
                if other.ref != resistor.ref and not is_ground(far)
            ]
            if others and not (in_supply and out_supply):
                continue
            vin = voltages.get(in_net)
            assumed = False
            if vin is None:
                vin = 3.3
                assumed = True
            simulatable = ohms is not None and ohms > 0
            reason = "Series resistor into a capacitor to ground."
            if not simulatable:
                reason = "Resistor value could not be parsed."
            elif assumed:
                reason += f" Input step assumed {vin:g} V because {in_net} has no voltage."
            found.append(
                Subcircuit(
                    id=f"rc:{resistor.ref}:{cap.ref}",
                    kind="rc_filter",
                    title=f"RC {resistor.ref}/{cap.ref} → {out_net}",
                    refs=[resistor.ref, cap.ref],
                    nets=[in_net, out_net],
                    params={
                        "in_net": in_net,
                        "out_net": out_net,
                        "r": ohms,
                        "c": farads,
                        "vin": vin,
                        "vin_assumed": assumed,
                        "r_ref": resistor.ref,
                        "c_ref": cap.ref,
                    },
                    simulatable=simulatable,
                    reason=reason,
                )
            )
            break
    return found


def _regulators(netlist, nets_of, voltages) -> list[Subcircuit]:
    found = []
    for regulator in find_regulators(netlist, nets_of):
        vset = regulator.expected_voltage
        if regulator.output_net is None or vset is None:
            found.append(
                Subcircuit(
                    id=f"regulator:{regulator.ref}",
                    kind="regulator",
                    title=f"{regulator.ref} {regulator.value}",
                    refs=[regulator.ref],
                    nets=[net for net in (regulator.input_net, regulator.output_net) if net],
                    params={"ref": regulator.ref, "value": regulator.value},
                    simulatable=False,
                    reason="No fixed output voltage or no output net. There is no vendor SPICE model for this part in v1.",
                )
            )
            continue
        cap = _cap_to_ground(netlist, nets_of, regulator.output_net)
        farads = parse_capacitance(cap.value) if cap else None
        vin = voltages.get(regulator.input_net) if regulator.input_net else None
        assumed = False
        if vin is None and regulator.input_net and is_battery_name(regulator.input_net):
            vin = _ASSUMED_BATTERY
            assumed = True
        if vin is None:
            found.append(
                Subcircuit(
                    id=f"regulator:{regulator.ref}",
                    kind="regulator",
                    title=f"{regulator.ref} {regulator.value} → {regulator.output_net}",
                    refs=[regulator.ref] + ([cap.ref] if cap else []),
                    nets=[net for net in (regulator.input_net, regulator.output_net) if net],
                    params={"vset": vset},
                    simulatable=False,
                    reason=f"Input net {regulator.input_net or '?'} has no voltage, so the behavioral model was not run.",
                )
            )
            continue
        if farads is None:
            farads = 10e-6
        found.append(
            Subcircuit(
                id=f"regulator:{regulator.ref}",
                kind="regulator",
                title=f"{regulator.ref} {regulator.value} behavioral → {regulator.output_net}",
                refs=[regulator.ref] + ([cap.ref] if cap else []),
                nets=[net for net in (regulator.input_net, regulator.output_net) if net],
                params={
                    "vset": vset,
                    "vin": vin,
                    "vin_assumed": assumed,
                    "cout": farads,
                    "cout_assumed": cap is None,
                    "input_net": regulator.input_net,
                    "output_net": regulator.output_net,
                    "dropout": 0.3,
                },
                simulatable=True,
                reason=(
                    "Behavioral clamp, not a vendor model: Vout = min(Vset, Vin − 0.3 V) after a step on the input."
                ),
            )
        )
    return found
