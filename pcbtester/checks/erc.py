"""Electrical rules check via kicad-cli."""

from __future__ import annotations

from pcbtester.checks.base import Check
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.kicad.cli import KicadCliError, export_erc, kicad_available
from pcbtester.kicad.violations import iter_violations, load_report, violation_refs
from pcbtester.models import CheckResult, Finding, rollup

_SEVERITY = {"error": "fail", "warning": "warn", "exclusion": "info", "ignore": "info"}


class ErcCheck(Check):
    id = "erc"
    title = "Schematic ERC"
    description = (
        "Runs KiCad's electrical rules check (kicad-cli sch erc) and lists "
        "each violation. This is KiCad's own pin-type check: unconnected pins, "
        "power pins driven by nothing, output conflicts. It does not know which "
        "MCU pin can be USART1_TX, and it does not count decoupling capacitors."
    )

    def run(self, ctx: ProjectContext, options: RunOptions) -> CheckResult:
        del options
        if ctx.sch is None:
            return CheckResult(self.id, self.title, "skipped", "No schematic in this project.")
        if not kicad_available():
            return CheckResult(
                self.id,
                self.title,
                "error",
                "kicad-cli is not installed, so ERC could not run.",
            )
        dest = ctx.work / "erc.json"
        try:
            completed = export_erc(ctx.sch, dest)
        except KicadCliError as exc:
            return CheckResult(self.id, self.title, "error", str(exc), findings=[
                Finding("fail", "erc.cli", str(exc), details=exc.output or None)
            ])
        if not dest.exists():
            output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
            return CheckResult(
                self.id,
                self.title,
                "error",
                "kicad-cli did not write an ERC report.",
                findings=[Finding("fail", "erc.cli", "ERC report missing", details=output or None)],
            )
        report = load_report(dest)
        findings = _findings(report)
        status = rollup(findings)
        errors = sum(1 for item in findings if item.severity == "fail")
        warns = sum(1 for item in findings if item.severity == "warn")
        if status == "pass":
            summary = "ERC reported no violations."
        else:
            summary = f"ERC reported {errors} error(s) and {warns} warning(s)."
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
        return [Finding("pass", "erc.clean", "KiCad ERC returned no violations.")]
    findings: list[Finding] = []
    for item in violations:
        kind = str(item.get("type") or "violation")
        severity = _SEVERITY.get(str(item.get("severity") or "warning").lower(), "warn")
        # A pass-severity does not exist in ERC output. Map "info" through.
        if severity not in {"fail", "warn", "info"}:
            severity = "warn"
        refs, descriptions = violation_refs(item)
        message = str(item.get("description") or kind).strip()
        detail = "; ".join(descriptions) if descriptions else None
        findings.append(
            Finding(
                severity,  # type: ignore[arg-type]
                f"erc.{kind}",
                message,
                refs=refs[:8],
                details=detail,
            )
        )
    return findings
