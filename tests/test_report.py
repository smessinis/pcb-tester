from pcbtester.models import CheckResult, Finding
from pcbtester.report.html import render_html
from pcbtester.report.pdf import render_pdf


def test_reports_include_the_finding():
    report = {
        "tool": "PCB Tester 0.1.0",
        "generated_at": "2026-01-01",
        "kicad_version": "9.0.0",
        "project": {"name": "demo", "schematic": "demo.kicad_sch", "pcb": None},
        "status": "fail",
        "counts": {"pass": 0, "info": 0, "warn": 0, "fail": 1},
        "checks": [
            CheckResult(
                "mcu_pins",
                "MCU pinmux and power",
                "fail",
                "one error",
                findings=[
                    Finding("fail", "mcu.vdd_overvoltage", "VDD on +5V", refs=["U1"], nets=["+5V"], pins=["100:VDD"])
                ],
                artifacts={"power": {"rails": [{"net": "+5V", "voltage": 5, "sourced": True, "sources": ["PWR1"], "loads": ["U1.VDD"], "local_caps": []}], "regulators": []}},
            ).to_dict()
        ],
    }
    html = render_html(report)
    assert "VDD on +5V" in html
    assert "+5V" in html
    assert "<html" in html
    pdf = render_pdf(report)
    assert pdf.startswith(b"%PDF")
