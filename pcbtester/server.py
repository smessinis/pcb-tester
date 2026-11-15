"""Small HTTP API and the static UI."""

from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pcbtester.project import ProjectError, discover, extract_zip
from pcbtester.runner import RunOptions, catalog, execute, preview_project
from pcbtester.report.html import render_html
from pcbtester.report.pdf import render_pdf

STATIC_DIR = Path(__file__).resolve().parent / "static"


class JobRequest(BaseModel):
    checks: list[str] = Field(default_factory=list)
    spice_ids: list[str] | None = None
    stimuli: dict[str, float] = Field(default_factory=dict)


def create_app(data_dir: Path | None = None) -> FastAPI:
    root = data_dir or Path(os.environ.get("PCBTESTER_DATA", "./data"))
    root.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="PCB Tester")
    lock = threading.Lock()
    jobs: dict[str, dict] = {}

    @app.get("/api/health")
    def health() -> dict:
        from pcbtester.kicad.cli import kicad_available, kicad_version
        from pcbtester.spice.ngspice import ngspice_available

        return {
            "ok": True,
            "kicad": kicad_available(),
            "kicad_version": kicad_version(),
            "ngspice": ngspice_available(),
        }

    @app.get("/api/checks")
    def checks() -> list[dict]:
        return catalog()

    @app.post("/api/projects")
    async def upload(file: UploadFile = File(...)) -> dict:
        blob = await file.read()
        if not blob:
            raise HTTPException(400, "Empty upload")
        if len(blob) > 80 * 1024 * 1024:
            raise HTTPException(400, "Upload is larger than 80 MB")
        project_id = uuid.uuid4().hex[:12]
        dest = root / "projects" / project_id
        try:
            extracted = extract_zip(blob, dest)
            ctx = discover(extracted)
        except ProjectError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(400, f"Could not read the upload: {exc}") from exc
        ctx.work = dest / "work"
        meta = {
            "id": project_id,
            "root": str(extracted),
            "work": str(ctx.work),
        }
        (dest / "project.json").write_text(json.dumps(meta))
        preview = preview_project(ctx)
        preview["project_id"] = project_id
        return preview

    @app.post("/api/projects/{project_id}/jobs")
    def start_job(project_id: str, body: JobRequest) -> dict:
        dest = root / "projects" / project_id
        if not (dest / "project.json").exists():
            raise HTTPException(404, "Unknown project")
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "project_id": project_id,
            "status": "running",
            "steps": [],
            "report": None,
            "error": None,
        }
        with lock:
            jobs[job_id] = job

        def progress(step_id: str, status: str, message: str) -> None:
            with lock:
                steps = job["steps"]
                for step in steps:
                    if step["id"] == step_id:
                        step["status"] = status
                        step["message"] = message
                        return
                steps.append({"id": step_id, "status": status, "message": message})

        def worker() -> None:
            try:
                meta = json.loads((dest / "project.json").read_text())
                ctx = discover(Path(meta["root"]))
                ctx.work = Path(meta["work"])
                report = execute(
                    ctx,
                    body.checks,
                    RunOptions(spice_ids=body.spice_ids, stimuli=body.stimuli),
                    progress=progress,
                )
                (dest / f"{job_id}.json").write_text(json.dumps(report))
                with lock:
                    job["report"] = report
                    job["status"] = "done"
            except Exception as exc:
                with lock:
                    job["status"] = "error"
                    job["error"] = str(exc)

        threading.Thread(target=worker, daemon=True, name=f"pcbtester-{job_id}").start()
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict:
        with lock:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(404, "Unknown job")
            return {
                "id": job["id"],
                "status": job["status"],
                "steps": list(job["steps"]),
                "error": job["error"],
                "report": job["report"],
            }

    @app.get("/api/jobs/{job_id}/report.html")
    def job_html(job_id: str) -> HTMLResponse:
        report = _report_of(job_id, jobs, lock, root)
        return HTMLResponse(render_html(report))

    @app.get("/api/jobs/{job_id}/report.pdf")
    def job_pdf(job_id: str) -> Response:
        report = _report_of(job_id, jobs, lock, root)
        return Response(content=render_pdf(report), media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{report["project"]["name"]}-pcbtester.pdf"'
        })

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _report_of(job_id: str, jobs: dict, lock: threading.Lock, root: Path) -> dict:
    with lock:
        job = jobs.get(job_id)
        if job and job.get("report"):
            return job["report"]
        project_id = job["project_id"] if job else None
    if project_id:
        path = root / "projects" / project_id / f"{job_id}.json"
        if path.exists():
            return json.loads(path.read_text())
    raise HTTPException(404, "Report is not ready")
