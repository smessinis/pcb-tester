"""Simulate the analog fragments ngspice can honestly run."""

from __future__ import annotations

from pcbtester.checks.base import Check
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.elec import format_volts
from pcbtester.models import CheckResult, Finding, rollup
from pcbtester.power import estimate_voltages
from pcbtester.spice.battery import adc_full_scale, divider_headroom
from pcbtester.spice.extract import Subcircuit, detect_subcircuits
from pcbtester.spice.ngspice import (
    NgspiceError,
    divider_deck,
    ngspice_available,
    rc_deck,
    regulator_deck,
    run_deck,
)


class SpiceCheck(Check):
    id = "spice"
    title = "SPICE subcircuits"
    description = (
        "Detects voltage dividers, RC filters and fixed regulators, then runs "
        "ngspice on the ones you select. Dividers and RC filters use the "
        "schematic values. A battery divider is also evaluated at 4S (16.8 V) "
        "and 6S (25.2 V), or at the cell count in the net name, and flagged "
        "when the tap exceeds VDDA/VREF+. Regulators use a behavioral clamp (Vout = min(Vset, "
        "Vin − dropout)), not a vendor transistor model. If a fragment has no "
        "voltage or no model, it is listed and skipped."
    )

    def run(self, ctx: ProjectContext, options: RunOptions) -> CheckResult:
        if ctx.netlist is None:
            message = ctx.netlist_error or "No netlist is available."
            return CheckResult(self.id, self.title, "error", message)
        voltages = estimate_voltages(ctx.netlist)
        circuits = detect_subcircuits(ctx.netlist, voltages, options.stimuli)
        adc_max, adc_label = adc_full_scale(ctx.netlist, voltages)
        for circuit in circuits:
            if circuit.kind == "divider":
                circuit.params["adc_max"] = adc_max
                circuit.params["adc_label"] = adc_label
        selected = _select(circuits, options.spice_ids)
        if not circuits:
            return CheckResult(
                self.id,
                self.title,
                "skipped",
                "No divider, RC filter or fixed regulator was recognised.",
                artifacts={"subcircuits": []},
            )
        if not selected:
            return CheckResult(
                self.id,
                self.title,
                "skipped",
                "No subcircuits were selected.",
                artifacts={"subcircuits": [item.to_dict() for item in circuits]},
            )
        if not ngspice_available():
            return CheckResult(
                self.id,
                self.title,
                "error",
                "ngspice is not installed. Subcircuits were detected but not simulated.",
                findings=[
                    Finding("fail", "spice.missing", "ngspice is not on PATH.", details=_catalog(circuits))
                ],
                artifacts={"subcircuits": [item.to_dict() for item in circuits]},
            )
        findings: list[Finding] = []
        results: list[dict] = []
        for circuit in selected:
            finding, result = _simulate(circuit)
            findings.append(finding)
            results.append(result)
        status = rollup(findings)
        ran = sum(1 for item in results if item.get("ran"))
        summary = f"Simulated {ran} of {len(selected)} selected subcircuit(s)."
        return CheckResult(
            self.id,
            self.title,
            status,
            summary,
            findings=findings,
            artifacts={"subcircuits": [item.to_dict() for item in circuits], "results": results},
        )


def _tau(seconds: float) -> str:
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.3g} ms"
    if seconds >= 1e-6:
        return f"{seconds * 1e6:.3g} µs"
    return f"{seconds * 1e9:.3g} ns"


def _select(circuits: list[Subcircuit], spice_ids: list[str] | None) -> list[Subcircuit]:
    if spice_ids is None:
        return [item for item in circuits if item.simulatable]
    wanted = set(spice_ids)
    return [item for item in circuits if item.id in wanted]


def _catalog(circuits: list[Subcircuit]) -> str:
    return "\n".join(f"{item.id}: {item.reason}" for item in circuits)


def _simulate(circuit: Subcircuit) -> tuple[Finding, dict]:
    result = {"id": circuit.id, "kind": circuit.kind, "ran": False, "voltages": {}}
    if not circuit.simulatable:
        finding = Finding(
            "info",
            "spice.skipped",
            f"{circuit.title}: {circuit.reason}",
            refs=circuit.refs,
            nets=circuit.nets,
        )
        return finding, result
    try:
        if circuit.kind == "divider":
            return _divider(circuit, result)
        if circuit.kind == "rc_filter":
            return _rc(circuit, result)
        if circuit.kind == "regulator":
            return _regulator(circuit, result)
    except NgspiceError as exc:
        finding = Finding(
            "warn",
            "spice.failed",
            f"{circuit.title} did not converge in ngspice.",
            refs=circuit.refs,
            nets=circuit.nets,
            details=str(exc)[-800:],
        )
        return finding, result
    finding = Finding("info", "spice.skipped", f"{circuit.title} has no runner.", refs=circuit.refs)
    return finding, result


def _divider(circuit: Subcircuit, result: dict) -> tuple[Finding, dict]:
    params = circuit.params
    deck = divider_deck(params["r_top"], params["r_bottom"], params["vin"])
    printed = run_deck(deck)
    result["ran"] = True
    result["deck"] = deck
    mid = printed.get("v(mid)")
    result["voltages"] = {"mid": mid, "vin": params["vin"]}
    expected = params["vin"] * params["r_bottom"] / (params["r_top"] + params["r_bottom"])
    assumed = " Assumed battery voltage." if params.get("vin_assumed") else ""
    if mid is None:
        return (
            Finding(
                "warn",
                "spice.no_result",
                f"{circuit.title}: ngspice did not print V(mid).",
                refs=circuit.refs,
                nets=circuit.nets,
                details=str(printed.get("_log", ""))[-800:],
            ),
            result,
        )
    result["expected"] = expected
    close = abs(mid - expected) <= max(0.01, 0.01 * abs(expected))
    severity = "pass" if close else "warn"
    code = "spice.divider" if close else "spice.divider_mismatch"
    message = (
        f"{circuit.title}: V({params['mid_net']}) = {format_volts(mid)} "
        f"with {format_volts(params['vin'])} on {params['top_net']}.{assumed}"
    )
    extra, exceeds = divider_headroom(
        params["top_net"],
        params["mid_net"],
        params["r_top"],
        params["r_bottom"],
        float(params.get("adc_max") or 3.3),
        str(params.get("adc_label") or "3.3 V default"),
        nominal_vin=params["vin"],
    )
    if extra:
        message = f"{message} {extra}"
        result["pack_exceeds_adc"] = exceeds
    if exceeds:
        severity = "warn"
        code = "spice.divider_adc"
    return (
        Finding(severity, code, message, refs=circuit.refs, nets=circuit.nets, details=circuit.reason),
        result,
    )


def _rc(circuit: Subcircuit, result: dict) -> tuple[Finding, dict]:
    params = circuit.params
    deck = rc_deck(params["r"], params["c"], params["vin"])
    printed = run_deck(deck)
    result["ran"] = True
    result["deck"] = deck
    settled = printed.get("vsettle")
    result["voltages"] = {"settled": settled, "vin": params["vin"]}
    tau = params["r"] * params["c"]
    assumed = " Input voltage was assumed." if params.get("vin_assumed") else ""
    if settled is None:
        return (
            Finding(
                "warn",
                "spice.no_result",
                f"{circuit.title}: ngspice did not measure the settled voltage.",
                refs=circuit.refs,
                nets=circuit.nets,
                details=str(printed.get("_log", ""))[-800:],
            ),
            result,
        )
    message = (
        f"{circuit.title}: settled at {format_volts(settled)} after a {format_volts(params['vin'])} step "
        f"(τ = {_tau(tau)}).{assumed}"
    )
    return Finding("pass", "spice.rc", message, refs=circuit.refs, nets=circuit.nets, details=circuit.reason), result


def _regulator(circuit: Subcircuit, result: dict) -> tuple[Finding, dict]:
    params = circuit.params
    deck = regulator_deck(params["vin"], params["vset"], params["cout"], params.get("dropout", 0.3))
    printed = run_deck(deck)
    result["ran"] = True
    result["deck"] = deck
    vout = printed.get("vout")
    result["voltages"] = {"vout": vout, "vin": params["vin"], "vset": params["vset"]}
    if vout is None:
        return (
            Finding(
                "warn",
                "spice.no_result",
                f"{circuit.title}: ngspice did not measure Vout.",
                refs=circuit.refs,
                nets=circuit.nets,
                details=str(printed.get("_log", ""))[-800:],
            ),
            result,
        )
    target = min(params["vset"], max(0.0, params["vin"] - params.get("dropout", 0.3)))
    ok = abs(vout - target) <= max(0.05, 0.05 * target)
    severity = "pass" if ok else "warn"
    note = " Behavioral model, not a vendor SPICE model."
    if params.get("cout_assumed"):
        note += " Output capacitor was assumed (10 µF) because none was found on the rail."
    if params.get("vin_assumed"):
        note += " Input voltage was assumed."
    message = (
        f"{circuit.title}: V({params['output_net']}) = {format_volts(vout)} "
        f"from {format_volts(params['vin'])} in, target {format_volts(target)}.{note}"
    )
    return (
        Finding(severity, "spice.regulator", message, refs=circuit.refs, nets=circuit.nets, details=circuit.reason),
        result,
    )
