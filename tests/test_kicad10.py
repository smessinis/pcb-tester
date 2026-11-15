"""KiCad 10 files: version gate, empty boards, and the hierarchical netlist."""

from pathlib import Path

from pcbtester.checks.drc import DrcCheck
from pcbtester.checks.mcu_pins import analyze_mcus
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.kicad.files import board_has_footprints, project_too_new, read_stamp
from pcbtester.kicad.netlist import parse_netlist
from pcbtester.runner import execute

FIXTURE = Path(__file__).parent / "fixtures" / "kicad10_hierarchy.xml"
STUB = '(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")\n)'


def _sch(major_label: str, version: int) -> str:
    return (
        f'(kicad_sch (version {version}) (generator "eeschema") '
        f'(generator_version "{major_label}")\n)\n'
    )


def test_kicad10_project_is_one_error_on_kicad9(tmp_path, monkeypatch):
    sch = tmp_path / "board.kicad_sch"
    pcb = tmp_path / "board.kicad_pcb"
    sch.write_text(_sch("10.0", 20260306))
    pcb.write_text(STUB)
    monkeypatch.setattr("pcbtester.runner.kicad_version", lambda: "9.0.9")
    monkeypatch.setattr(
        "pcbtester.runner.export_netlist",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("netlist export should not run")),
    )
    ctx = ProjectContext(
        root=tmp_path,
        name="board",
        pro=None,
        sch=sch,
        pcb=pcb,
        work=tmp_path / "work",
    )
    report = execute(ctx, ["erc", "drc", "connectivity", "mcu_pins", "spice", "renode"])
    assert report["status"] == "fail"
    assert len(report["checks"]) == 1
    assert report["checks"][0]["summary"] == (
        "Project saved with KiCad 10.0, tester has kicad-cli 9.0.9"
    )
    assert report["checks"][0]["findings"][0]["code"] == "kicad.too_new"


def test_kicad10_files_are_readable_by_kicad10(tmp_path):
    sch = tmp_path / "board.kicad_sch"
    pcb = tmp_path / "board.kicad_pcb"
    sch.write_text(_sch("10.0", 20260306))
    pcb.write_text(STUB)
    assert project_too_new(sch, pcb, "10.0.8") is None
    assert read_stamp(sch, "sch").kicad_major == 10
    assert read_stamp(pcb, "pcb").format_version == 20260206
    older = tmp_path / "old.kicad_sch"
    older.write_text(_sch("8.0", 20231120))
    assert project_too_new(older, None, "10.0.8") is None
    legacy = tmp_path / "seven.kicad_sch"
    legacy.write_text('(kicad_sch (version 20230121) (generator eeschema)\n)\n')
    assert read_stamp(legacy, "sch").kicad_major == 7
    assert project_too_new(legacy, None, "9.0.9") is None


def test_empty_pcb_skips_drc(tmp_path):
    pcb = tmp_path / "stub.kicad_pcb"
    pcb.write_text(STUB)
    assert pcb.stat().st_size == 79
    assert board_has_footprints(pcb) is False
    laid_out = tmp_path / "real.kicad_pcb"
    laid_out.write_text(STUB + '\n(footprint "Package:R_0603" (layer "F.Cu"))\n')
    assert board_has_footprints(laid_out) is True
    ctx = ProjectContext(
        root=tmp_path,
        name="stub",
        pro=None,
        sch=None,
        pcb=pcb,
        work=tmp_path / "work",
    )
    result = DrcCheck().run(ctx, RunOptions())
    assert result.status == "skipped"
    assert result.summary == "No board layout yet."
    assert result.findings[0].severity == "info"


def test_kicad10_hierarchical_netlist_uses_symbol_pin_names():
    netlist = parse_netlist(FIXTURE)
    mcu = netlist.component("U1")
    assert mcu is not None
    assert mcu.properties["Sheetname"] == "MCU"
    assert mcu.properties["Sheetfile"] == "untitled.kicad_sch"
    tx = netlist.net("USART1_TX")
    assert tx is not None
    node = tx.nodes[0]
    assert node.pin == "68"
    assert node.pin_name == "PA9"
    boot = netlist.net("/MCU/BOOT0").nodes[0]
    assert boot.pin_name == "BOOT0"
    findings, artifacts = analyze_mcus(netlist)
    assert artifacts["matched"] == 1
    assert any(item.code == "mcu.functions_ok" for item in findings)
    row = artifacts["pinmux"][0]
    assert row["mcu"] == "STM32H743VITx"
    assert row["pin_name"] == "PA9"
    assert row["function"] == "USART1_TX"
    assert row["ok"] is True
