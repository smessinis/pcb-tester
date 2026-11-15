"""The ordered list of checks the runner and the UI know about."""

from __future__ import annotations

from pcbtester.checks.base import Check
from pcbtester.checks.connectivity import ConnectivityCheck
from pcbtester.checks.drc import DrcCheck
from pcbtester.checks.erc import ErcCheck
from pcbtester.checks.mcu_pins import McuPinCheck
from pcbtester.checks.renode import RenodeCheck
from pcbtester.checks.spice import SpiceCheck


def available_checks() -> list[Check]:
    return [
        ErcCheck(),
        DrcCheck(),
        ConnectivityCheck(),
        McuPinCheck(),
        SpiceCheck(),
        RenodeCheck(),
    ]


def get_check(check_id: str) -> Check | None:
    for check in available_checks():
        if check.id == check_id:
            return check
    return None
