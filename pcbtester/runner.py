"""Run a chosen set of checks and build one report."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from pcbtester import __version__
from pcbtester.checks.registry import available_checks, get_check
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.kicad.cli import KicadCliError, export_netlist, kicad_version
from pcbtester.kicad.files import project_too_new
from pcbtester.kicad.netlist import parse_netlist
from pcbtester.models import CheckResult, Finding

Progress = Callable[[str, str, str], None]

_NEEDS_NETLIST = {"connectivity", "mcu_pins", "spice", "renode"}


def execute(
    ctx: ProjectContext,
    check_ids: list[str],
    options: RunOptions | None = None,
    progress: Progress | None = None,
) -> dict:
    options = options or RunOptions()
    ctx.work.mkdir(parents=True, exist_ok=True)
    mismatch = project_too_new(ctx.sch, ctx.pcb, kicad_version())
    if mismatch:
        if progress:
            progress("kicad_version", "error", mismatch)
        return _report(
            ctx,
            [
                CheckResult(
                    "kicad_version",
                    "KiCad version",
                    "error",
                    mismatch,
                    findings=[Finding("fail", "kicad.too_new", mismatch)],
                )
            ],
        )
    known = {check.id: check for check in available_checks()}
    selected = []
    unknown = []
    for check_id in check_ids:
        check = known.get(check_id) or get_check(check_id)
        if check is None:
            unknown.append(check_id)
        else:
            selected.append(check)
    if _NEEDS_NETLIST.intersection(check.id for check in selected):
        _ensure_netlist(ctx, progress)
    results: list[CheckResult] = []
    for check in selected:
        if progress:
            progress(check.id, "running", check.title)
        started = time.perf_counter()
        try:
            result = check.run(ctx, options)
        except Exception as exc:  # a check bug should not drop the rest of the report
            result = CheckResult(
                check.id,
                check.title,
                "error",
                f"{check.title} crashed: {exc}",
                findings=[Finding("fail", f"{check.id}.crash", str(exc))],
            )
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        results.append(result)
        if progress:
            progress(check.id, result.status, result.summary)
    if unknown:
        results.append(
            CheckResult(
                "unknown",
                "Unknown checks",
                "error",
                "Some requested checks are not registered: " + ", ".join(unknown),
            )
        )
    return _report(ctx, results)


def _ensure_netlist(ctx: ProjectContext, progress: Progress | None) -> None:
    if ctx.netlist is not None or ctx.netlist_error:
        return
    if ctx.sch is None:
        ctx.netlist_error = "This project has no schematic, so no netlist could be exported."
        return
    mismatch = project_too_new(ctx.sch, ctx.pcb, kicad_version())
    if mismatch:
        ctx.netlist_error = mismatch
        if progress:
            progress("netlist", "error", mismatch)
        return
    if progress:
        progress("netlist", "running", "Exporting netlist")
    dest = ctx.work / "netlist.xml"
    try:
        export_netlist(ctx.sch, dest)
        ctx.netlist = parse_netlist(dest)
    except (KicadCliError, OSError, ValueError) as exc:
        detail = getattr(exc, "output", "") or ""
        ctx.netlist_error = f"{exc}" + (f"\n{detail}" if detail else "")
    if progress:
        progress("netlist", "error" if ctx.netlist_error else "pass", ctx.netlist_error or "Netlist exported")


def _report(ctx: ProjectContext, results: list[CheckResult]) -> dict:
    counts = {"pass": 0, "info": 0, "warn": 0, "fail": 0}
    for result in results:
        for finding in result.findings:
            if finding.severity in counts:
                counts[finding.severity] += 1
    statuses = {result.status for result in results}
    if "fail" in statuses or "error" in statuses:
        overall = "fail"
    elif "warn" in statuses:
        overall = "warn"
    elif results and all(result.status in {"pass", "skipped"} for result in results):
        overall = "pass"
    else:
        overall = "pass" if results else "skipped"
    return {
        "tool": f"PCB Tester {__version__}",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "kicad_version": kicad_version(),
        "project": {
            "name": ctx.name,
            "schematic": str(ctx.sch) if ctx.sch else None,
            "pcb": str(ctx.pcb) if ctx.pcb else None,
            "root": str(ctx.root),
        },
        "status": overall,
        "counts": counts,
        "checks": [result.to_dict() for result in results],
    }


def default_check_ids() -> list[str]:
    return [check.id for check in available_checks() if check.default_enabled]


def catalog() -> list[dict]:
    return [
        {
            "id": check.id,
            "title": check.title,
            "description": check.description,
            "default": check.default_enabled,
        }
        for check in available_checks()
    ]


def preview_project(ctx: ProjectContext) -> dict:
    """Netlist-only summary used by the UI before a full run."""
    from pcbtester.mcu.db import load_mcus, looks_like_stm32, match_mcu
    from pcbtester.power import build_power_tree
    from pcbtester.spice.extract import detect_subcircuits

    _ensure_netlist(ctx, None)
    mcus = []
    spice = []
    regulators = []
    error = ctx.netlist_error
    if ctx.netlist is not None:
        catalog_mcu = load_mcus()
        for component in ctx.netlist.components.values():
            matched = match_mcu(component.value, component.part, component.lib, catalog_mcu)
            if matched or looks_like_stm32(component.value, component.part):
                mcus.append(
                    {
                        "ref": component.ref,
                        "value": component.value,
                        "part": component.part,
                        "database": matched.id if matched else None,
                    }
                )
        tree = build_power_tree(ctx.netlist)
        regulators = tree["regulators"]
        spice = [item.to_dict() for item in detect_subcircuits(ctx.netlist, tree["voltages"])]
    return {
        "name": ctx.name,
        "schematic": ctx.sch.name if ctx.sch else None,
        "pcb": ctx.pcb.name if ctx.pcb else None,
        "netlist_error": error,
        "mcus": mcus,
        "regulators": regulators,
        "spice": spice,
        "checks": catalog(),
    }


def write_outputs(report: dict, html_path: Path | None = None, pdf_path: Path | None = None, json_path: Path | None = None) -> None:
    import json

    from pcbtester.report.html import render_html
    from pcbtester.report.pdf import render_pdf

    if json_path:
        json_path.write_text(json.dumps(report, indent=2))
    if html_path:
        html_path.write_text(render_html(report))
    if pdf_path:
        pdf_path.write_bytes(render_pdf(report))
