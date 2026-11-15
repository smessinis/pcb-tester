from pathlib import Path

from pcbtester.checks.connectivity import analyze_connectivity
from pcbtester.kicad.netlist import parse_netlist

NETLIST = parse_netlist(Path(__file__).parent / "fixtures" / "h743_min.xml")


def _codes(findings):
    return {finding.code for finding in findings}


def test_fixture_catches_the_planted_mistakes():
    findings, tree = analyze_connectivity(NETLIST)
    codes = _codes(findings)
    assert "conn.floating_power" in codes
    assert "conn.i2c_pullup_missing" in codes
    assert "conn.i2c_pullup" in codes
    assert "conn.boot_low" in codes
    assert "conn.reset_cap" in codes
    assert "conn.vcap" in codes
    assert "conn.decoupling_short" in codes
    assert "conn.rail_source" in codes

    missing = [item for item in findings if item.code == "conn.i2c_pullup_missing"]
    assert any("IMU_SDA" in item.nets for item in missing)

    rails = {rail["net"]: rail for rail in tree["rails"]}
    assert rails["+3V3"]["voltage"] == 3.3
    assert rails["+3V3"]["sourced"] is True
    assert rails["+5V"]["voltage"] == 5
    assert rails["VBAT"]["sourced"] is True
    regs = tree["regulators"]
    assert regs[0]["ref"] == "U2"
    assert regs[0]["output_net"] == "+3V3"
    assert regs[0]["expected_voltage"] == 3.3
