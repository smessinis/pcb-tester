"""Shared result types for every check."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Severity = Literal["pass", "info", "warn", "fail"]
CheckStatus = Literal["pass", "warn", "fail", "error", "skipped"]


@dataclass
class Finding:
    severity: Severity
    code: str
    message: str
    refs: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    pins: list[str] = field(default_factory=list)
    details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["details"] is None:
            data.pop("details")
        return data


@dataclass
class CheckResult:
    check_id: str
    title: str
    status: CheckStatus
    summary: str
    findings: list[Finding] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "findings": [finding.to_dict() for finding in self.findings],
            "artifacts": self.artifacts,
            "duration_ms": self.duration_ms,
        }


def rollup(findings: list[Finding], *, empty: CheckStatus = "pass") -> CheckStatus:
    severities = {finding.severity for finding in findings}
    if "fail" in severities:
        return "fail"
    if "warn" in severities:
        return "warn"
    if findings:
        return "pass"
    return empty
