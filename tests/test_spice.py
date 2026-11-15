from pathlib import Path

import pytest

from pcbtester.checks.spice import SpiceCheck
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.kicad.netlist import parse_netlist
from pcbtester.spice.extract import detect_subcircuits
from pcbtester.spice.ngspice import ngspice_available, parse_prints

FIXTURE = Path(__file__).parent / "fixtures" / "h743_min.xml"


def test_divider_is_detected_and_battery_voltage_is_assumed():
    netlist = parse_netlist(FIXTURE)
    circuits = detect_subcircuits(netlist)
    dividers = [item for item in circuits if item.kind == "divider"]
    assert len(dividers) == 1
    divider = dividers[0]
    assert divider.simulatable
    assert divider.params["vin"] == 12.6
    assert divider.params["vin_assumed"] is True
    assert {divider.params["r_top_ref"], divider.params["r_bottom_ref"]} == {"R1", "R2"}


def test_print_parser():
    log = "v(mid) = 1.145455e+00\nvsettle              =  3.300000e+00\n"
    parsed = parse_prints(log)
    assert parsed["v(mid)"] == pytest.approx(1.145455)
    assert parsed["vsettle"] == pytest.approx(3.3)


@pytest.mark.skipif(not ngspice_available(), reason="ngspice is not installed")
def test_ngspice_runs_the_divider(tmp_path):
    netlist = parse_netlist(FIXTURE)
    ctx = ProjectContext(
        root=tmp_path,
        name="h743_min",
        pro=None,
        sch=None,
        pcb=None,
        work=tmp_path,
        netlist=netlist,
    )
    result = SpiceCheck().run(ctx, RunOptions())
    assert result.status == "pass"
    divider = next(item for item in result.findings if item.code == "spice.divider")
    assert "VSENSE" in divider.message
    # 12.6 * 10k / (100k + 10k) = 1.14545 V. 4S and 6S stay under a 3.3 V ADC.
    assert "1.145" in divider.message
    assert "16.8 V" in divider.message
    assert "25.2 V" in divider.message
