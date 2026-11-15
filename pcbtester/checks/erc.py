"""Electrical rules check via kicad-cli."""

from __future__ import annotations

import re
from collections import OrderedDict

from pcbtester.checks.base import Check
from pcbtester.context import ProjectContext, RunOptions
from pcbtester.kicad.cli import KicadCliError, export_erc, kicad_available
from pcbtester.kicad.violations import iter_violations, load_report, violation_refs
from pcbtester.models import CheckResult, Finding, Severity, rollup

_SEVERITY = {"error": "fail", "warning": "warn", "exclusion": "info", "ignore": "info"}
_SEVERITY_ORDER = {"fail": 0, "warn": 1, "info": 2, "pass": 3}

# Missing global library tables, not a mistake in the schematic.
_LIB_MISSING = re.compile(
    r"does not include the (?:symbol |footprint )?library\s+['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)


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
        findings, errors, warns = _findings(report)
        status = rollup(findings)
        if errors == 0 and warns == 0:
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


def _findings(report: dict) -> tuple[list[Finding], int, int]:
    """Group identical ERC rows and return raw error and warning totals.

    Library-table misses are one info, not one warning per nickname. Everything
    else with the same severity, type and description becomes ``714x …`` with
    the individual rows in the details. Errors are listed before warnings.
    """
    violations = iter_violations(report)
    if not violations:
        return [Finding("pass", "erc.clean", "KiCad ERC returned no violations.")], 0, 0
    groups: OrderedDict[tuple[str, str, str], list[tuple[list[str], str | None]]] = OrderedDict()
    libraries: list[str] = []
    errors = 0
    warns = 0
    for item in violations:
        kind = str(item.get("type") or "violation")
        severity: Severity = _SEVERITY.get(str(item.get("severity") or "warning").lower(), "warn")  # type: ignore[assignment]
        if severity not in {"fail", "warn", "info"}:
            severity = "warn"
        refs, descriptions = violation_refs(item)
        message = str(item.get("description") or kind).strip()
        blob = " ".join([message, *descriptions])
        library = _library_name(blob)
        if library is not None:
            libraries.append(library)
            continue
        if severity == "fail":
            errors += 1
        elif severity == "warn":
            warns += 1
        detail = "; ".join(descriptions) if descriptions else None
        groups.setdefault((severity, kind, message), []).append((refs, detail))
    findings: list[Finding] = []
    ordered = sorted(groups.items(), key=lambda item: (_SEVERITY_ORDER.get(item[0][0], 9), -len(item[1]), item[0][2]))
    for (severity, kind, message), rows in ordered:
        details = [detail for _refs, detail in rows if detail]
        refs: list[str] = []
        for row_refs, _detail in rows:
            for ref in row_refs:
                if ref not in refs:
                    refs.append(ref)
        if len(rows) == 1:
            text = message
            detail_text = details[0] if details else None
        else:
            text = f"{len(rows)}x {message}"
            shown = details[:80]
            detail_text = "\n".join(shown)
            if len(details) > len(shown):
                detail_text += f"\n… and {len(details) - len(shown)} more"
        findings.append(
            Finding(
                severity,  # type: ignore[arg-type]
                f"erc.{kind}",
                text,
                refs=refs[:8],
                details=detail_text,
            )
        )
    if libraries:
        unique = sorted(set(libraries))
        findings.append(
            Finding(
                "info",
                "erc.library_missing",
                f"{len(libraries)}x symbol or footprint library is not in the KiCad library tables.",
                details="\n".join(unique),
            )
        )
    return findings, errors, warns


def _library_name(text: str) -> str | None:
    match = _LIB_MISSING.search(text)
    if match:
        return match.group(1)
    lowered = text.lower()
    if "does not include the" in lowered and "library" in lowered:
        return text.strip()
    return None
