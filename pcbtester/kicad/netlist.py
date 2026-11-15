"""Parse a KiCad XML netlist (kicad-cli --format kicadxml)."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PinInfo:
    num: str
    name: str
    type: str


@dataclass
class Component:
    ref: str
    value: str
    footprint: str = ""
    lib: str = ""
    part: str = ""
    description: str = ""
    properties: dict[str, str] = field(default_factory=dict)
    pins: dict[str, PinInfo] = field(default_factory=dict)

    @property
    def identity(self) -> str:
        return " ".join(piece for piece in (self.value, self.part, self.lib, self.description) if piece)


@dataclass
class Node:
    ref: str
    pin: str
    pin_name: str
    pin_type: str


@dataclass
class Net:
    code: str
    name: str
    nodes: list[Node] = field(default_factory=list)


@dataclass
class Netlist:
    source: str
    tool: str
    components: dict[str, Component]
    nets: list[Net]

    def net(self, name: str) -> Net | None:
        for item in self.nets:
            if item.name == name:
                return item
        return None

    def component(self, ref: str) -> Component | None:
        return self.components.get(ref)


def _pin_name(pin_info: PinInfo | None, pinfunction: str, pin_num: str) -> str:
    """Prefer the symbol pin name over KiCad 10's ``PA9_68`` pinfunction.

    KiCad 10 writes ``pinfunction="PA9_68"`` on net nodes. The libpart still
    carries the datasheet name (``PA9``), which the strap and power checks
    match. When the libpart is missing, strip the ``_<pin number>`` suffix.
    """
    if pin_info and pin_info.name and pin_info.name not in {"~", ""}:
        return pin_info.name
    suffix = f"_{pin_num}"
    if pinfunction.endswith(suffix) and len(pinfunction) > len(suffix):
        return pinfunction[: -len(suffix)]
    return pinfunction


def _text(element: ET.Element | None, child: str, default: str = "") -> str:
    if element is None:
        return default
    node = element.find(child)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def parse_netlist(source: str | Path) -> Netlist:
    if isinstance(source, Path):
        root = ET.parse(source).getroot()
    else:
        text = source.strip()
        if text.startswith("<") or text.startswith("<?xml"):
            root = ET.fromstring(text)
        else:
            root = ET.parse(text).getroot()
    if root.tag != "export":
        raise ValueError(f"Not a KiCad XML netlist (root is <{root.tag}>)")

    design = root.find("design")
    libparts: dict[tuple[str, str], dict[str, PinInfo]] = {}
    for libpart in root.findall("./libparts/libpart"):
        lib = libpart.get("lib") or ""
        part = libpart.get("part") or ""
        pins: dict[str, PinInfo] = {}
        for pin in libpart.findall("./pins/pin"):
            num = pin.get("num") or ""
            pins[num] = PinInfo(num=num, name=pin.get("name") or "", type=pin.get("type") or "")
        libparts[(lib, part)] = pins

    components: dict[str, Component] = {}
    for comp in root.findall("./components/comp"):
        ref = comp.get("ref") or ""
        libsource = comp.find("libsource")
        lib = (libsource.get("lib") if libsource is not None else "") or ""
        part = (libsource.get("part") if libsource is not None else "") or ""
        description = (libsource.get("description") if libsource is not None else "") or ""
        properties = {
            prop.get("name") or "": (prop.get("value") or "")
            for prop in comp.findall("property")
            if prop.get("name")
        }
        component = Component(
            ref=ref,
            value=_text(comp, "value"),
            footprint=_text(comp, "footprint"),
            lib=lib,
            part=part,
            description=description,
            properties=properties,
            pins={num: PinInfo(pin.num, pin.name, pin.type) for num, pin in libparts.get((lib, part), {}).items()},
        )
        components[ref] = component

    nets: list[Net] = []
    for net_el in root.findall("./nets/net"):
        nodes: list[Node] = []
        for node_el in net_el.findall("node"):
            ref = node_el.get("ref") or ""
            pin = node_el.get("pin") or ""
            component = components.get(ref)
            pin_info = component.pins.get(pin) if component else None
            pin_name = _pin_name(pin_info, node_el.get("pinfunction") or "", pin)
            pin_type = node_el.get("pintype") or (pin_info.type if pin_info else "")
            nodes.append(Node(ref=ref, pin=pin, pin_name=pin_name, pin_type=pin_type))
        nets.append(Net(code=net_el.get("code") or "", name=net_el.get("name") or "", nodes=nodes))

    return Netlist(
        source=_text(design, "source"),
        tool=_text(design, "tool"),
        components=components,
        nets=nets,
    )
