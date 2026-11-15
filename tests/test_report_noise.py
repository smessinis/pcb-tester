"""ERC grouping, empty check selection, and battery-divider headroom."""

import pytest

from pcbtester.checks.erc import _findings
from pcbtester.context import ProjectContext
from pcbtester.kicad.netlist import parse_netlist
from pcbtester.models import CheckResult
from pcbtester.runner import execute
from pcbtester.spice.battery import battery_check_points, divider_headroom
from pathlib import Path


def test_erc_groups_repeats_and_library_warnings():
    report = {
        "violations": [
            {"type": "endpoint_off_grid", "description": "Symbol pin or wire end off connection grid", "severity": "error",
             "items": [{"description": "Pin 1 of U1"}]},
            {"type": "endpoint_off_grid", "description": "Symbol pin or wire end off connection grid", "severity": "error",
             "items": [{"description": "Pin 2 of U1"}]},
            {"type": "endpoint_off_grid", "description": "Symbol pin or wire end off connection grid", "severity": "error",
             "items": [{"description": "Pin 3 of R1"}]},
            {"type": "pin_not_connected", "description": "Pin not connected", "severity": "warning",
             "items": [{"description": "Pin 4 of U2"}]},
            {"type": "lib_symbol_issues", "description": "The current configuration does not include the symbol library 'Device'", "severity": "warning",
             "items": []},
            {"type": "lib_symbol_issues", "description": "The current configuration does not include the footprint library 'Resistor_SMD'", "severity": "warning",
             "items": []},
        ]
    }
    findings, errors, warns = _findings(report)
    assert errors == 3
    assert warns == 1
    assert findings[0].severity == "fail"
    assert findings[0].message == "3x Symbol pin or wire end off connection grid"
    assert "Pin 1 of U1" in (findings[0].details or "")
    assert findings[1].message == "Pin not connected"
    assert findings[1].severity == "warn"
    assert findings[-1].severity == "info"
    assert findings[-1].code == "erc.library_missing"
    assert "Device" in (findings[-1].details or "")
    assert "Resistor_SMD" in (findings[-1].details or "")


def test_empty_check_list_runs_every_check(tmp_path, monkeypatch):
    class Dummy:
        def __init__(self, check_id: str) -> None:
            self.id = check_id
            self.title = check_id
            self.description = ""
            self.default_enabled = True

        def run(self, ctx, options):
            return CheckResult(self.id, self.title, "pass", "ok")

    checks = [Dummy("erc"), Dummy("spice")]
    monkeypatch.setattr("pcbtester.runner.available_checks", lambda: checks)
    monkeypatch.setattr("pcbtester.runner.kicad_version", lambda: "10.0.8")
    ctx = ProjectContext(root=tmp_path, name="board", pro=None, sch=None, pcb=None, work=tmp_path / "work")
    report = execute(ctx, [])
    assert [item["check_id"] for item in report["checks"]] == ["erc", "spice"]
    assert [item["status"] for item in report["checks"]] == ["pass", "pass"]


def test_battery_divider_flags_adc_clip_and_respects_cell_count(monkeypatch):
    monkeypatch.delenv("PCBTESTER_BATTERY_MAX_V", raising=False)
    monkeypatch.delenv("PCBTESTER_BATTERY_PACKS", raising=False)
    points = battery_check_points("VBAT")
    assert [label for label, _volts in points] == ["4S", "6S"]
    assert points[0][1] == pytest.approx(16.8)
    assert points[1][1] == pytest.approx(25.2)
    implied = battery_check_points("VBAT_6S")
    assert implied[0][0] == "6S" and implied[0][1] == pytest.approx(25.2)
    text, exceeds = divider_headroom("VBAT", "SENSE", 100_000, 10_000, 3.3, "VDDA on +3V3")
    assert exceeds is False
    assert "16.8 V" in text and "25.2 V" in text
    text, exceeds = divider_headroom("VBAT", "SENSE", 10_000, 10_000, 3.3, "3.3 V default")
    assert exceeds is True
    assert "Exceeds the ADC range" in text
    monkeypatch.setenv("PCBTESTER_BATTERY_MAX_V", "20")
    assert battery_check_points("PACK") == [("max", 20.0)]


def test_html_report_hides_grouped_rows_behind_a_disclosure():
    from pcbtester.report.html import render_html

    html = render_html(
        {
            "tool": "PCB Tester",
            "generated_at": "now",
            "project": {"name": "board", "schematic": "a.kicad_sch", "pcb": None},
            "status": "fail",
            "counts": {"fail": 1, "warn": 0, "pass": 0, "info": 0},
            "checks": [
                {
                    "check_id": "erc",
                    "title": "Schematic ERC",
                    "status": "fail",
                    "summary": "ERC reported 3 error(s) and 0 warning(s).",
                    "findings": [
                        {
                            "severity": "fail",
                            "code": "erc.endpoint_off_grid",
                            "message": "3x Symbol pin or wire end off connection grid",
                            "refs": ["U1"],
                            "nets": [],
                            "pins": [],
                            "details": "Pin 1 of U1\nPin 2 of U1\nPin 3 of R1",
                        }
                    ],
                    "artifacts": {},
                }
            ],
        }
    )
    assert "3x Symbol pin or wire end off connection grid" in html
    assert "<details>" in html
    assert "show 3 item(s)" in html


def test_fixture_netlist_still_parses():
    netlist = parse_netlist(Path(__file__).parent / "fixtures" / "h743_min.xml")
    assert netlist.component("U1") is not None
