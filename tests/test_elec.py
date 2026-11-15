import pytest

from pcbtester.elec import (
    format_volts,
    is_battery_name,
    is_ground,
    net_voltage,
    parse_capacitance,
    parse_resistance,
    voltage_from_part,
)


def test_resistance_notations():
    assert parse_resistance("10k") == 10_000
    assert parse_resistance("4.7k") == 4700
    assert parse_resistance("2k2") == 2200
    assert parse_resistance("4R7") == 4.7
    assert parse_resistance("100R") == 100
    assert parse_resistance("0") == 0
    assert parse_resistance("R") is None


def test_capacitance_notations():
    assert parse_capacitance("100n") == pytest.approx(100e-9)
    assert parse_capacitance("100nF") == pytest.approx(100e-9)
    assert parse_capacitance("0.1uF") == pytest.approx(0.1e-6)
    assert parse_capacitance("2.2uF") == pytest.approx(2.2e-6)
    assert parse_capacitance("10pF") == pytest.approx(10e-12)
    assert parse_capacitance("22U") == pytest.approx(22e-6)
    assert parse_capacitance("4n7") == pytest.approx(4.7e-9)
    assert parse_capacitance("C") is None


def test_net_voltage_and_ground():
    assert is_ground("GND")
    assert is_ground("AGND")
    assert net_voltage("GND") == 0
    assert net_voltage("+3V3") == 3.3
    assert net_voltage("+3.3VA") == 3.3
    assert net_voltage("+5V") == 5
    assert net_voltage("VBUS") == 5
    assert net_voltage("VDD") is None
    assert is_battery_name("VBAT")
    assert voltage_from_part("AP2112K-3.3") == 3.3
    assert voltage_from_part("LT1963A-1.8") == 1.8
    assert format_volts(3.3) == "3.3 V"
