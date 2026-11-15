from pathlib import Path

from pcbtester.checks.mcu_pins import analyze_mcus, resolve_function
from pcbtester.kicad.netlist import parse_netlist
from pcbtester.mcu.db import load_mcus, match_mcu

NETLIST = parse_netlist(Path(__file__).parent / "fixtures" / "h743_min.xml")


def test_part_number_matches_lqfp100_not_lqfp144():
    mcus = load_mcus()
    assert {mcu.id for mcu in mcus} >= {"STM32H743VITx", "STM32H743ZITx"}
    vit = match_mcu("STM32H743VIT6")
    assert vit is not None and vit.id == "STM32H743VITx"
    symbol = match_mcu("STM32H743VITx")
    assert symbol is not None and symbol.id == "STM32H743VITx"
    zit = match_mcu("STM32H743ZIT6")
    assert zit is not None and zit.id == "STM32H743ZITx"
    assert match_mcu("LM7805") is None


def test_pin_database_agrees_with_the_datasheet_sample():
    mcu = match_mcu("STM32H743VIT6")
    assert mcu.pins["22"].name == "PA0"
    assert mcu.pins["68"].name == "PA9"
    assert mcu.pins["94"].name == "BOOT0"
    assert mcu.pins["48"].role == "vcap"
    assert mcu.pins["11"].role == "vdd"
    assert mcu.pins["68"].function("USART1_TX") is not None
    assert mcu.pins["22"].function("USART1_TX") is None
    assert mcu.pins["72"].name == "PA13"
    assert "SWDIO" in mcu.pins["72"].function_names()
    assert resolve_function("UART1_TX", mcu) == "USART1_TX"
    assert resolve_function("SPI1_SCLK", mcu) in {None, "SPI1_SCK"} or resolve_function("SPI1_SCLK", mcu) == "SPI1_SCK"


def test_fixture_pinmux_failures():
    findings, artifacts = analyze_mcus(NETLIST)
    codes = {finding.code for finding in findings}
    assert artifacts["matched"] == 1
    assert "mcu.function_unavailable" in codes
    assert "mcu.duplicate_function" in codes
    assert "mcu.vdd_overvoltage" in codes
    assert "mcu.functions_ok" in codes
    assert "mcu.vdda_unfiltered" in codes
    bad = [item for item in findings if item.code == "mcu.function_unavailable"]
    assert any("PA0" in item.message for item in bad)
    over = [item for item in findings if item.code == "mcu.vdd_overvoltage"]
    assert any("+5V" in item.nets for item in over)
    rows = {row["pin_name"]: row for row in artifacts["pinmux"]}
    assert rows["PA9"]["function"] == "USART1_TX"
    assert rows["PA9"]["ok"] is True
    assert rows["PA0"]["ok"] is False
