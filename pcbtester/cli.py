"""Command line: ``pcbtester serve`` and ``pcbtester run``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pcbtester.project import ProjectError, discover
from pcbtester.runner import RunOptions, default_check_ids, execute, write_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pcbtester", description="Pre-production checks for a KiCad board")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="Start the web UI")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)

    run = sub.add_parser("run", help="Check a project directory, .kicad_pro or .kicad_sch")
    run.add_argument("project", type=Path)
    run.add_argument(
        "--checks",
        default=",".join(default_check_ids()),
        help="Comma-separated check ids. Default is everything except the Renode slot.",
    )
    run.add_argument("--spice", default="", help="Comma-separated subcircuit ids. Empty means all that can run.")
    run.add_argument("--stimulus", action="append", default=[], help="Net voltage override, e.g. VBAT=16.8")
    run.add_argument("--html", type=Path)
    run.add_argument("--pdf", type=Path)
    run.add_argument("--json", type=Path)
    run.add_argument("--work", type=Path, help="Directory for kicad-cli output. Default is <project>/.pcbtester")

    args = parser.parse_args(argv)
    if args.cmd == "serve":
        import uvicorn

        from pcbtester.server import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    try:
        ctx = discover(args.project)
    except ProjectError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.work:
        ctx.work = args.work
    stimuli = _stimuli(args.stimulus)
    spice_ids = [item for item in args.spice.split(",") if item] or None
    report = execute(
        ctx,
        [item for item in args.checks.split(",") if item],
        RunOptions(spice_ids=spice_ids, stimuli=stimuli),
    )
    write_outputs(report, html_path=args.html, pdf_path=args.pdf, json_path=args.json)
    print(json.dumps({"status": report["status"], "counts": report["counts"], "project": report["project"]["name"]}, indent=2))
    for check in report["checks"]:
        print(f"  {check['status']:8} {check['title']}: {check['summary']}")
    return 0 if report["status"] in {"pass", "warn"} else 1


def _stimuli(items: list[str]) -> dict[str, float]:
    found: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"Stimulus must look like VBAT=16.8, got {item}")
        name, raw = item.split("=", 1)
        found[name.strip()] = float(raw)
    return found


if __name__ == "__main__":
    raise SystemExit(main())
