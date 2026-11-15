"""A self-contained HTML report. No external assets, so it can be emailed."""

from __future__ import annotations

import html
from typing import Any


def render_html(report: dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    checks = report.get("checks") or []
    project = report.get("project") or {}
    status = report.get("status") or "pass"
    body = [
        _header(report, project, status, counts),
        _power_section(checks),
        "".join(_check_section(check) for check in checks),
    ]
    # Findings can contain braces (ngspice expressions). Escape them so str.format
    # only fills the two placeholders in the page skeleton.
    body_html = "\n".join(body).replace("{", "{{").replace("}", "}}")
    title = html.escape(project.get("name") or "PCB Tester report")
    return _PAGE.format(title=title, body=body_html)


def _header(report: dict, project: dict, status: str, counts: dict) -> str:
    chips = "".join(
        f'<span class="count {name}">{counts.get(name, 0)} {name}</span>'
        for name in ("fail", "warn", "pass", "info")
    )
    schematic = html.escape(project.get("schematic") or "—")
    pcb = html.escape(project.get("pcb") or "—")
    kicad = html.escape(str(report.get("kicad_version") or "not available"))
    return f"""
    <header>
      <p class="eyebrow">{html.escape(report.get("tool") or "PCB Tester")} · {html.escape(report.get("generated_at") or "")}</p>
      <h1>{html.escape(project.get("name") or "Project")}</h1>
      <p class="status {html.escape(status)}">{html.escape(status.upper())}</p>
      <div class="counts">{chips}</div>
      <dl class="meta">
        <div><dt>Schematic</dt><dd>{schematic}</dd></div>
        <div><dt>PCB</dt><dd>{pcb}</dd></div>
        <div><dt>KiCad</dt><dd>{kicad}</dd></div>
      </dl>
    </header>
    """


def _power_section(checks: list[dict]) -> str:
    for check in checks:
        power = (check.get("artifacts") or {}).get("power")
        if not power:
            continue
        rails = power.get("rails") or []
        regulators = power.get("regulators") or []
        if not rails and not regulators:
            return ""
        rows = []
        for rail in rails:
            volts = "—" if rail.get("voltage") is None else f"{rail['voltage']:g} V"
            source = html.escape(", ".join(rail.get("sources") or []) or "none")
            caps = html.escape(", ".join((rail.get("local_caps") or [])[:6]) or "—")
            sourced = "yes" if rail.get("sourced") else "no"
            rows.append(
                "<tr>"
                f"<td><code>{html.escape(rail.get('net') or '')}</code></td>"
                f"<td>{html.escape(volts)}</td>"
                f"<td class=\"{ 'ok' if rail.get('sourced') else 'bad'}\">{sourced}</td>"
                f"<td>{source}</td>"
                f"<td>{len(rail.get('loads') or [])}</td>"
                f"<td>{caps}</td>"
                "</tr>"
            )
        reg_lines = []
        for reg in regulators:
            volts = "" if reg.get("expected_voltage") is None else f" → {reg['expected_voltage']:g} V"
            reg_lines.append(
                f"<li><code>{html.escape(reg.get('ref') or '')}</code> "
                f"{html.escape(reg.get('value') or '')} "
                f"{html.escape(reg.get('input_net') or '?')} to {html.escape(reg.get('output_net') or '?')}"
                f"{html.escape(volts)}</li>"
            )
        regs = f"<ul>{''.join(reg_lines)}</ul>" if reg_lines else ""
        return f"""
        <section>
          <h2>Power rails</h2>
          {regs}
          <table>
            <thead><tr><th>Net</th><th>Voltage</th><th>Sourced</th><th>Source</th><th>Loads</th><th>Local caps</th></tr></thead>
            <tbody>{''.join(rows)}</tbody>
          </table>
        </section>
        """
    return ""


def _check_section(check: dict) -> str:
    findings = check.get("findings") or []
    items = []
    for finding in findings:
        refs = " ".join(f"<code>{html.escape(ref)}</code>" for ref in finding.get("refs") or [])
        nets = " ".join(f"<code>{html.escape(net)}</code>" for net in finding.get("nets") or [])
        pins = " ".join(f"<code>{html.escape(pin)}</code>" for pin in finding.get("pins") or [])
        details = finding.get("details")
        detail_html = f"<pre>{html.escape(details)}</pre>" if details else ""
        items.append(
            f'<li class="sev-{html.escape(finding.get("severity") or "info")}">'
            f'<span class="tag">{html.escape(finding.get("severity") or "")}</span> '
            f'{html.escape(finding.get("message") or "")}'
            f'<div class="chips">{refs} {nets} {pins}</div>'
            f"{detail_html}</li>"
        )
    pinmux = (check.get("artifacts") or {}).get("pinmux") or []
    mux_html = ""
    if pinmux:
        mux_rows = []
        for row in pinmux:
            flag = "ok" if row.get("ok") else "no"
            af = "—" if row.get("af") is None else str(row.get("af"))
            mux_rows.append(
                "<tr>"
                f"<td>{html.escape(flag)}</td>"
                f"<td><code>{html.escape(str(row.get('pin_name') or ''))}</code></td>"
                f"<td>{html.escape(str(row.get('pin') or ''))}</td>"
                f"<td><code>{html.escape(str(row.get('net') or ''))}</code></td>"
                f"<td>{html.escape(str(row.get('function') or ''))}</td>"
                f"<td>{html.escape(af)}</td>"
                "</tr>"
            )
        mux_html = (
            "<h3>Constrained pins</h3><table><thead><tr>"
            "<th></th><th>Pin</th><th>#</th><th>Net</th><th>Function</th><th>AF</th>"
            f"</tr></thead><tbody>{''.join(mux_rows)}</tbody></table>"
        )
    repl = (check.get("artifacts") or {}).get("repl")
    repl_html = f"<h3>Renode skeleton</h3><pre>{html.escape(repl)}</pre>" if repl else ""
    duration = check.get("duration_ms")
    timing = f" · {duration} ms" if duration else ""
    return f"""
    <section class="check">
      <h2><span class="tag sev-{html.escape(check.get('status') or '')}">{html.escape(check.get('status') or '')}</span>
      {html.escape(check.get('title') or '')}</h2>
      <p class="summary">{html.escape(check.get('summary') or '')}{timing}</p>
      <ul class="findings">{''.join(items)}</ul>
      {mux_html}
      {repl_html}
    </section>
    """


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ margin: 0 auto; max-width: 980px; padding: 32px 20px 80px;
          font: 15px/1.45 "Segoe UI", Helvetica, sans-serif; color: #1c1915; background: #f6f1e8; }}
  header, section {{ background: #fffdf8; border: 1px solid #e2d8c8; padding: 18px 20px; margin-bottom: 14px; }}
  h1 {{ margin: 0 0 8px; font-size: 28px; letter-spacing: -0.03em; }}
  h2 {{ margin: 0 0 8px; font-size: 18px; }}
  h3 {{ margin: 16px 0 6px; font-size: 14px; }}
  .eyebrow {{ margin: 0; color: #6b6256; font-size: 12px; letter-spacing: 0.04em; text-transform: uppercase; }}
  .status {{ display: inline-block; margin: 0 0 10px; padding: 2px 8px; font-weight: 700; letter-spacing: 0.06em; }}
  .status.pass, .tag.sev-pass, .sev-pass .tag {{ background: #e5f2e6; color: #1f6b32; }}
  .status.warn, .tag.sev-warn, .sev-warn .tag {{ background: #fff1d6; color: #8a5a00; }}
  .status.fail, .tag.sev-fail, .sev-fail .tag, .status.error {{ background: #fde2e0; color: #9f2d2d; }}
  .status.skipped, .tag.sev-skipped, .sev-info .tag, .tag.sev-info {{ background: #ece7df; color: #4a453e; }}
  .counts {{ display: flex; gap: 8px; margin: 8px 0 12px; }}
  .count {{ padding: 2px 8px; background: #f3eee6; font-variant-numeric: tabular-nums; }}
  .meta {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin: 0; }}
  .meta dt {{ color: #6b6256; font-size: 12px; }}
  .meta dd {{ margin: 0; word-break: break-all; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee4d6; vertical-align: top; }}
  th {{ color: #6b6256; font-weight: 600; }}
  code {{ font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace; font-size: 12px; }}
  .ok {{ color: #1f6b32; }} .bad {{ color: #9f2d2d; }}
  ul.findings {{ list-style: none; padding: 0; margin: 0; }}
  ul.findings li {{ padding: 8px 0; border-top: 1px solid #f0e8dc; }}
  .tag {{ display: inline-block; min-width: 3.2em; padding: 1px 6px; font-size: 11px; font-weight: 700;
          letter-spacing: 0.04em; text-transform: uppercase; }}
  .chips {{ margin-top: 4px; }}
  .chips code {{ margin-right: 6px; background: #f6f1e8; padding: 1px 4px; }}
  pre {{ white-space: pre-wrap; background: #f6f1e8; padding: 10px; font-size: 12px; overflow: auto; }}
  .summary {{ color: #4a453e; margin-top: 0; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""
