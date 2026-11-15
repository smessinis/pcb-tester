from pathlib import Path

from pcbtester.kicad.netlist import parse_netlist
from pcbtester.kicad.violations import iter_violations

FIXTURE = Path(__file__).parent / "fixtures" / "h743_min.xml"


def test_parse_fixture_netlist():
    netlist = parse_netlist(FIXTURE)
    assert netlist.component("U1").value == "STM32H743VIT6"
    assert netlist.component("U1").pins["68"].name == "PA9"
    gnd = netlist.net("GND")
    assert gnd is not None
    refs = {node.ref for node in gnd.nodes}
    assert "U1" in refs and "C1" in refs
    tx = netlist.net("USART1_TX")
    assert {node.pin for node in tx.nodes} == {"22", "68"}
    assert tx.nodes[0].pin_name == "PA0" or tx.nodes[1].pin_name == "PA9"


def test_violation_report_flattens_sheets():
    import json

    report = json.loads((Path(__file__).parent / "fixtures" / "erc_sample.json").read_text())
    kinds = [item["type"] for item in iter_violations(report)]
    assert kinds == ["pin_not_connected", "power_pin_not_driven", "endpoint_off_grid"]
