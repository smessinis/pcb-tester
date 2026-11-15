"""A plain PDF of the same findings. Core fonts only, so nothing is bundled."""

from __future__ import annotations

from typing import Any

from fpdf import FPDF


def render_pdf(report: dict[str, Any]) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    def line(text: str, size: int = 10, bold: bool = False) -> None:
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", "B" if bold else "", size)
        pdf.multi_cell(0, 5 if size < 14 else 8, _safe(text), new_x="LMARGIN", new_y="NEXT")

    project = report.get("project") or {}
    line(project.get("name") or "PCB Tester report", size=16, bold=True)
    line(
        f"{report.get('tool', '')}  {report.get('generated_at', '')}  status {report.get('status', '')}"
    )
    counts = report.get("counts") or {}
    line(
        f"fail {counts.get('fail', 0)}   warn {counts.get('warn', 0)}   "
        f"pass {counts.get('pass', 0)}   info {counts.get('info', 0)}"
    )
    pdf.ln(2)
    for check in report.get("checks") or []:
        line(f"{check.get('status', '').upper()}  {check.get('title', '')}", size=12, bold=True)
        line(check.get("summary") or "")
        for finding in check.get("findings") or []:
            refs = ", ".join(finding.get("refs") or [])
            nets = ", ".join(finding.get("nets") or [])
            extra = ""
            if refs:
                extra += f"  [{refs}]"
            if nets:
                extra += f"  nets {nets}"
            line(f"  {finding.get('severity', '')}: {finding.get('message', '')}{extra}")
        pdf.ln(2)
    return bytes(pdf.output())


def _safe(text: str) -> str:
    return (
        str(text)
        .replace("µ", "u")
        .replace("Ω", "ohm")
        .replace("→", "->")
        .replace("—", "-")
        .replace("–", "-")
        .encode("latin-1", "replace")
        .decode("latin-1")
    )
