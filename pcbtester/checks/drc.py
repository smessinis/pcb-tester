"""Design rules check via kicad-cli."""

from __future__ import annotations

from pcbtester.checks.base import Check
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.kicad.cli import KicadCliError, export_drc, kicad_available
from pcbtester.kicad.violations import iter_violations, load_report, violation_refs
from pcbtester.models import CheckResult, Finding, rollup

_SEVERITY = {"error": "fail", "warning": "warn", "exclusion": "info"}


class DrcCheck(Check):
    id = "drc"
    title = "PCB DRC"
    description = (
        "Runs KiCad's design rules check (kicad-cli pcb drc): clearances, "
        "shorts, courtyard overlaps, unconnected tracks. It checks the copper "
        "against the board setup in the project. It does not check whether a "
        "trace is wide enough for a motor current you only wrote in a comment, "
        "and schematic-parity is left off so a board you have not pushed from "
        "the schematic yet is still reported on its own rules."
    )

    def run(self, ctx: ProjectContext, options: RunOptions) -> CheckResult:
        del options
        if ctx.pcb is None:
            return CheckResult(self.id, self.title, "skipped", "No PCB layout in this project.")
        if not kicad_available():
            return CheckResult(self.id, self.title, "error", "kicad-cli is not installed, so DRC could not run.")
        dest = ctx.work / "drc.json"
        try:
            completed = export_drc(ctx.pcb, dest)
        except KicadCliError as exc:
            return CheckResult(
                self.id,
                self.title,
                "error",
                str(exc),
                findings=[Finding("fail", "drc.cli", str(exc), details=exc.output or None)],
            )
        if not dest.exists():
            output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
            return CheckResult(
                self.id,
                self.title,
                "error",
                "kicad-cli did not write a DRC report.",
                findings=[Finding("fail", "drc.cli", "DRC report missing", details=output or None)],
            )
        report = load_report(dest)
        findings = _findings(report)
        status = rollup(findings)
        errors = sum(1 for item in findings if item.severity == "fail")
        warns = sum(1 for item in findings if item.severity == "warn")
        summary = "DRC reported no violations." if status == "pass" else (
            f"DRC reported {errors} error(s) and {warns} warning(s)."
        )
        return CheckResult(
            self.id,
            self.title,
            status,
            summary,
            findings=findings,
            artifacts={"kicad_version": report.get("kicad_version"), "source": report.get("source")},
        )


def _findings(report: dict) -> list[Finding]:
    violations = iter_violations(report)
    if not violations:
        return [Finding("pass", "drc.clean", "KiCad DRC returned no violations.")]
    findings: list[Finding] = []
    for item in violations:
        kind = str(item.get("type") or "violation")
        severity = _SEVERITY.get(str(item.get("severity") or "warning").lower(), "warn")
        refs, descriptions = violation_refs(item)
        message = str(item.get("description") or kind).strip()
        detail = "; ".join(descriptions) if descriptions else None
        findings.append(
            Finding(severity, f"drc.{kind}", message, refs=refs[:8], details=detail)  # type: ignore[arg-type]
        )
    return findings
