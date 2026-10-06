"""Command-line interface: ``cryoplug init | start | tools | fetch-viewer | status | jobtypes | service | worker``."""
from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path

from cryoplug import __version__
from cryoplug.config import DEFAULT_CONFIG_PATH, EXAMPLE_CONFIG, MOLSTAR_VERSION, load_config


def cmd_init(args: argparse.Namespace) -> None:
    path = Path(args.config or os.environ.get("CRYOPLUG_CONFIG") or DEFAULT_CONFIG_PATH).expanduser()
    if path.exists() and not args.force:
        print(f"Configuration already exists: {path} (use --force to overwrite)")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(EXAMPLE_CONFIG)
        print(f"Wrote {path}")
    cfg = load_config(path)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.projects_root.mkdir(parents=True, exist_ok=True)
    print(f"Data directory:     {cfg.data_dir}")
    print(f"Projects directory: {cfg.projects_root}")
    print("Next: edit the [tools.*] sections, then run `cryoplug tools` and `cryoplug start`.")


def cmd_start(args: argparse.Namespace) -> None:
    import uvicorn

    from cryoplug.server.app import create_app

    cfg = load_config(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    app = create_app(cfg)
    if not app.state.manager.tool_status:
        print("Checking external tools (first start)...")
        app.state.manager.check_tools()
    if cfg.host not in ("127.0.0.1", "localhost") and not cfg.password:
        print("WARNING: CryoPlug is exposed on the network without a password ([server] password).", file=sys.stderr)
    print(f"CryoPlug {__version__} on http://{cfg.host}:{cfg.port}  (config: {cfg.config_path or 'defaults'})")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=args.log_level)


def cmd_tools(args: argparse.Namespace) -> None:
    from cryoplug.manager import Manager

    cfg = load_config(args.config)
    manager = Manager(cfg)
    status = manager.check_tools()
    width = max(len(k) for k in status)
    for key, st in status.items():
        mark = {"found": "OK ", "missing": "-- ", "disabled": "off"}.get(st["status"], "?? ")
        detail = st.get("path") or st.get("message", "")
        version = f"  [{st['version']}]" if st.get("version") else ""
        print(f"{mark} {key:<{width}}  {detail}{version}")


def cmd_fetch_viewer(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    dest = cfg.viewer_dir
    dest.mkdir(parents=True, exist_ok=True)
    if args.tgz:
        data = Path(args.tgz).read_bytes()
    else:
        url = f"https://registry.npmjs.org/molstar/-/molstar-{args.version}.tgz"
        print(f"Downloading {url}")
        with urllib.request.urlopen(url, timeout=300) as resp:
            data = resp.read()
    wanted = {"package/build/viewer/molstar.js": "molstar.js", "package/build/viewer/molstar.css": "molstar.css"}
    found = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name in wanted and member.isfile():
                fh = tar.extractfile(member)
                if fh is None:
                    continue
                with open(dest / wanted[member.name], "wb") as out:
                    shutil.copyfileobj(fh, out)
                found += 1
    if found != len(wanted):
        sys.exit("molstar.js / molstar.css not found in the archive")
    print(f"Mol* viewer installed in {dest} (served locally, no internet needed).")


def cmd_status(args: argparse.Namespace) -> None:
    from cryoplug.manager import Manager

    manager = Manager(load_config(args.config))
    overview = manager.queue_overview()
    for lane in overview["lanes"]:
        print(f"Lane {lane['name']} ({lane['type']}): {lane['running']}/{lane['max_jobs']} running, GPUs used {lane['gpus_used']}")
    for j in overview["jobs"]:
        print(f"  {j['project_uid']}/{j['uid']:<5} {j['status']:<9} {j['type']:<28} {j.get('message', '')}")
    if not overview["jobs"]:
        print("  (no active jobs)")


def cmd_jobtypes(args: argparse.Namespace) -> None:
    from cryoplug.jobs import all_job_types

    current = None
    for jt in all_job_types():
        if jt.category != current:
            current = jt.category
            print(f"\n{current}")
        tool = f" [{jt.tool}]" if jt.tool else ""
        print(f"  {jt.name:<32} {jt.title}{tool}")


def cmd_demo(args: argparse.Namespace) -> None:
    from cryoplug.demo import make_demo_dataset

    paths = make_demo_dataset(args.directory)
    print(f"CryoSPARC-like job folder: {paths['cryosparc_job']}")
    print(f"Model:                     {paths['model']}")
    print(f"Sequence:                  {paths['fasta']}")
    print("In CryoPlug: New project > Import from CryoSPARC > select the J42 folder.")


def cmd_docs_jobs(args: argparse.Namespace) -> None:
    from cryoplug.jobhelp import render_markdown

    text = render_markdown()
    if args.out == "-":
        sys.stdout.write(text)
    else:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}")


def cmd_service(args: argparse.Namespace) -> None:
    exe = shutil.which("cryoplug") or f"{sys.executable} -m cryoplug"
    cfg = args.config or str(DEFAULT_CONFIG_PATH.expanduser())
    print(f"""[Unit]
Description=CryoPlug cryo-EM post-processing server
After=network.target

[Service]
Type=simple
User={os.environ.get('USER', 'cryoem')}
Environment=CRYOPLUG_CONFIG={cfg}
ExecStart={exe} start
Restart=on-failure
KillMode=process

[Install]
WantedBy=multi-user.target
""")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cryoplug", description="Browser-based cryo-EM post-processing suite")
    parser.add_argument("--version", action="version", version=f"cryoplug {__version__}")
    parser.add_argument("-c", "--config", help="Configuration file (default: $CRYOPLUG_CONFIG or ~/.cryoplug/config.toml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="Write an example configuration and create directories")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("start", help="Start the web server and the job scheduler")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--log-level", default="warning")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("tools", help="Detect the external programs")
    p.set_defaults(func=cmd_tools)

    p = sub.add_parser("fetch-viewer", help="Download the Mol* 3D viewer for offline use")
    p.add_argument("--version", default=MOLSTAR_VERSION)
    p.add_argument("--tgz", help="Use a local molstar-X.Y.Z.tgz (npm pack molstar) instead of downloading")
    p.set_defaults(func=cmd_fetch_viewer)

    p = sub.add_parser("status", help="Show lanes and active jobs")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("jobtypes", help="List available job types")
    p.set_defaults(func=cmd_jobtypes)

    p = sub.add_parser("demo-data", help="Write a small synthetic dataset to try CryoPlug")
    p.add_argument("directory")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("docs-jobs", help="Write the job reference (French) as Markdown")
    p.add_argument("--out", default="docs/JOBS.md", help="Output file ('-' for stdout)")
    p.set_defaults(func=cmd_docs_jobs)

    p = sub.add_parser("service", help="Print a systemd unit file")
    p.set_defaults(func=cmd_service)

    p = sub.add_parser("worker", help=argparse.SUPPRESS)
    p.add_argument("job_dir")
    p.set_defaults(func=lambda a: __import__("cryoplug.worker", fromlist=["main"]).main([a.job_dir]))

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
