"""Job worker: runs one job in its own process (locally or on a cluster node).

The worker reads ``job.json`` (written by the scheduler) and reports through
``state.json``, ``job.log`` and ``report.json`` in the job directory, so it
needs no database or network access.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
import traceback
from pathlib import Path

from cryoplug import __version__
from cryoplug.jobs import get_job_type
from cryoplug.jobs.base import JobContext, JobError, JobKilled


def _raise_killed(signum, frame):  # pragma: no cover - signal handler
    raise JobKilled()


def run_worker(job_dir: str | Path) -> int:
    job_dir = Path(job_dir).resolve()
    spec = json.loads((job_dir / "job.json").read_text())
    ctx = JobContext(spec, job_dir)
    ctx.outputs, ctx.highlights, ctx.report = [], [], {"sections": []}
    try:
        ctx.snapshot()  # what exists before the program runs: recognises its outputs, even if written beside the inputs
    except Exception as exc:  # pragma: no cover - only helps finding the outputs
        ctx.warn(f"Could not list the job and input folders: {exc}")
    signal.signal(signal.SIGTERM, _raise_killed)
    signal.signal(signal.SIGINT, _raise_killed)
    ctx.write_state(status="running", pid=os.getpid(), host=ctx.hostname(), started_at=time.time(),
                    heartbeat=time.time(), progress=0.0, message="Running", error="")

    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(30):
            try:
                ctx.write_state(heartbeat=time.time())
            except OSError:
                pass

    threading.Thread(target=heartbeat, daemon=True).start()
    final, error = "failed", ""
    try:
        jt_cls = get_job_type(spec["type"])
        ctx.log(f"CryoPlug {__version__} | {spec['project_uid']}/{spec['uid']} | {jt_cls.title} ({spec['type']}) on {ctx.hostname()}")
        gpus = spec.get("resources", {}).get("gpus")
        if gpus:
            ctx.log(f"GPU(s): {', '.join(map(str, gpus))} (CUDA_VISIBLE_DEVICES={os.environ.get('CUDA_VISIBLE_DEVICES', '')})")
        for slot, inp in ctx.inputs.items():
            if inp:
                ctx.log(f"Input {slot}: {inp.source} -> {inp.path}")
        nondefault = {p.name: ctx.params.get(p.name) for p in jt_cls.params if ctx.params.get(p.name) != p.default}
        if nondefault:
            ctx.log("Parameters: " + ", ".join(f"{k}={v!r}" for k, v in nondefault.items()))
        job = jt_cls()
        if jt_cls.interactive:
            job.prepare(ctx)
            final = "waiting"
        else:
            job.run(ctx)
            final = "completed"
    except JobKilled:
        final, error = "killed", "Killed"
        ctx.log("Job killed", "error")
    except JobError as exc:
        error = str(exc)
        ctx.log(error, "error")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        ctx.log(traceback.format_exc(), "error")
    finally:
        stop.set()
    messages = {"completed": "Completed", "waiting": "Waiting for the user (interactive session)",
                "killed": "Killed", "failed": "Failed"}
    fields = {"status": final, "message": messages[final], "error": error, "heartbeat": time.time()}
    if final != "waiting":
        fields["ended_at"] = time.time()
    if final == "completed":
        fields["progress"] = 1.0
    ctx.write_state(**fields)
    ctx.log(f"Job {final}")
    return 0 if final in ("completed", "waiting") else 1


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("usage: python -m cryoplug worker JOB_DIR", file=sys.stderr)
        sys.exit(2)
    sys.exit(run_worker(argv[0]))
