"""Command-line interface: ``cryoplug init | start | url | user | tools | fetch-viewer | status | jobtypes | mcp | ai |
service | worker``."""
from __future__ import annotations

import argparse
import io
import json
import os
import shlex
import shutil
import sys
import tarfile
import time
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

    cfg = _server_config(args)
    if bool(cfg.ssl_certfile) != bool(cfg.ssl_keyfile):
        sys.exit("[server] ssl_certfile and ssl_keyfile must be set together")
    for path in (cfg.ssl_certfile, cfg.ssl_keyfile):
        if path and not Path(path).is_file():
            sys.exit(f"HTTPS: {path} not found")
    app = create_app(cfg)
    if not app.state.manager.tool_status:
        print("Checking external tools (first start)...")
        app.state.manager.check_tools()
    print(f"CryoPlug {__version__}  (config: {cfg.config_path or 'defaults'})")
    print_access(cfg, app.state.auth.mode, app.state.auth.token)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=args.log_level,
                ssl_certfile=cfg.ssl_certfile or None, ssl_keyfile=cfg.ssl_keyfile or None)


def _server_config(args: argparse.Namespace):
    cfg = load_config(args.config)
    if args.host:
        cfg.host = args.host
    if args.port:
        cfg.port = args.port
    return cfg


def print_access(cfg, mode: str | None, token: str) -> None:
    """Tell the user which address to open, and how to log in."""
    import getpass
    import socket

    from cryoplug.server.auth import access_urls, is_loopback

    local = is_loopback(cfg.host)
    print("Open in a browser on this machine:" if local else "Open from any computer on the network:")
    for url in access_urls(cfg, token if mode == "token" else ""):
        print(f"  {url}")
    if mode == "users":
        print("Log in with your CryoPlug account (administrators manage accounts in Settings > Users,")
        print("or with `cryoplug user`).")
        if not local and not cfg.ssl_certfile:
            print("Passwords cross the network unencrypted over http: consider HTTPS ([server] ssl_certfile).")
    elif mode == "password":
        print("Log in with the password set in [server] password.")
    elif mode == "token":
        print(f"Access token: {token}  (`cryoplug url` prints these links again)")
        print("Personal accounts (as in CryoSPARC): Settings > Users in the interface, or `cryoplug user add`.")
    if local:
        print(f'From another computer: set [server] host = "0.0.0.0" (or `cryoplug start --host 0.0.0.0`), '
              f"or open an SSH tunnel on that computer:\n"
              f"  ssh -N -L {cfg.port}:localhost:{cfg.port} {getpass.getuser()}@{socket.gethostname()}")


def cmd_url(args: argparse.Namespace) -> None:
    from cryoplug.db import Database
    from cryoplug.server.auth import access_token, auth_mode

    cfg = _server_config(args)
    if args.reset_token:
        access_token(cfg, reset=True)
        print("New access token: restart the server (browsers logged in with the old one must log in again).")
    mode = auth_mode(cfg, cfg.db_path.exists() and Database(cfg.db_path).has_users())
    print_access(cfg, mode, access_token(cfg) if mode == "token" else "")


def _ask_password(args: argparse.Namespace, users) -> tuple[str, bool]:
    """--password, --generate, or typed twice. Returns (password, generated)."""
    import getpass

    if getattr(args, "generate", False):
        return users.generate_password(), True
    if getattr(args, "password", None):
        return args.password, False
    if not sys.stdin.isatty():
        sys.exit("No terminal to type the password: use --generate (or --password)")
    first = getpass.getpass("Password: ")
    users.check_password(first, getattr(args, "name", ""))
    if getpass.getpass("Password again: ") != first:
        sys.exit("The passwords differ")
    return first, False


def cmd_user(args: argparse.Namespace) -> None:
    from datetime import datetime

    from cryoplug.db import Database
    from cryoplug.users import UserError, Users

    cfg = load_config(args.config)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    users = Users(Database(cfg.db_path), cfg)
    when = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M") if t else "never"  # noqa: E731
    try:
        if args.action == "list":
            accounts = users.list()
            if not accounts:
                print("No accounts: CryoPlug is protected by the access token or [server] password (or localhost only).")
            for u in accounts:
                flags = [u["role"]] + (["disabled"] if u["disabled"] else []) + (["must change password"] if u["must_change_password"] else [])
                print(f"{u['username']:<20} {', '.join(flags):<32} last login {when(u['last_login_at'])}")
                print(f"{'':<20} projects: {users.projects_dir(u)}")
                for folder in u["allowed_paths"]:
                    print(f"{'':<20} may read: {folder}")
        elif args.action == "add":
            first = not users.exist()
            password, generated = _ask_password(args, users)
            user = users.create(args.name, password, role="admin" if args.admin or first else "user", full_name=args.full_name or "",
                                email=args.email or "", projects_dir=args.projects_dir or "", allowed_paths=args.allow or [],
                                must_change_password=args.must_change)
            print(f"Created {user['role']} account '{user['username']}'" + (f" with password: {password}" if generated else ""))
            if first:
                adopted = users.adopt_projects(user["username"])
                print("Accounts are now required: everyone logs in with a user name and a password"
                      + (f"; the {adopted} existing project(s) belong to {user['username']}." if adopted else "."))
        elif args.action == "passwd":
            password, generated = _ask_password(args, users)
            users.set_password(args.name, password, must_change=args.must_change)
            print(f"New password for '{users.require(args.name)['username']}'" + (f": {password}" if generated else ""))
        elif args.action == "set":
            fields = {}
            if args.role:
                fields["role"] = args.role
            if args.full_name is not None:
                fields["full_name"] = args.full_name
            if args.email is not None:
                fields["email"] = args.email
            if args.projects_dir is not None:
                fields["projects_dir"] = args.projects_dir
            if args.allow is not None:
                fields["allowed_paths"] = args.allow
            user = users.update(args.name, **fields)
            print(f"Updated '{user['username']}' ({user['role']}, projects in {users.projects_dir(user)})")
        elif args.action in ("disable", "enable"):
            user = users.update(args.name, disabled=args.action == "disable")
            print(f"'{user['username']}' {'disabled' if user['disabled'] else 'enabled'}")
        elif args.action == "delete":
            users.delete(args.name, args.transfer_to or "")
            print(f"Deleted '{args.name}'" + (f"; projects given to {args.transfer_to}" if args.transfer_to else ""))
        elif args.action == "token":
            if not users.exist():
                sys.exit("No user accounts: scripts and AI agents use the access token (read by `cryoplug mcp` on this "
                         "machine, shown by `cryoplug url`) or the [server] password.")
            user = users.require(args.name)
            if args.list:
                tokens = users.tokens(user)
                if not tokens:
                    print(f"'{user['username']}' has no API token.")
                for t in tokens:
                    print(f"{t['id']}  {t['label']:<30} created {when(t['created_at'])}, last used {when(t['last_used_at'])}, "
                          f"expires {when(t['expires_at'])}")
            elif args.revoke:
                gone = users.revoke_token(user, args.revoke)
                print(f"Revoked the API token {gone['id']} ({gone['label']}) of '{user['username']}'.")
            else:
                token, info = users.new_token(user, args.label or "AI agent", args.days, agent="cryoplug user token")
                print(token)  # alone on stdout: export CRYOPLUG_TOKEN=$(cryoplug user token NAME)
                print(f"API token '{info['label']}' of '{user['username']}' (id {info['id']}), valid until "
                      f"{when(info['expires_at'])}. It is shown only once: give it to the AI client as CRYOPLUG_TOKEN.\n"
                      f"It acts as {user['username']} (same projects and folders); revoke it with "
                      f"`cryoplug user token {user['username']} --revoke {info['id']}` or in Settings > AI agents.",
                      file=sys.stderr)
    except UserError as exc:
        sys.exit(str(exc))


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


# ------------------------------------------------------------------ AI agents
def cmd_mcp(args: argparse.Namespace) -> None:
    from cryoplug.agent.mcp import stdio_main

    stdio_main(args.config, args.url, args.token, allow_commands=args.allow_commands, tools=args.tools)


def _agent_command() -> list[str]:
    """How an AI client starts the MCP server: the absolute path, as desktop applications ignore the shell PATH."""
    exe = shutil.which("cryoplug")
    return [str(Path(exe).resolve()), "mcp"] if exe else [sys.executable, "-m", "cryoplug", "mcp"]


def cmd_ai(args: argparse.Namespace) -> None:
    if args.action == "skills":
        _ai_skills(args)
    elif args.action == "setup":
        _ai_setup(args)
    elif args.action == "tools":
        from cryoplug.agent.tools import TOOLS

        for t in TOOLS.values():
            kind = "read-only" if t.read_only else "destructive" if t.destructive else "changes"
            print(f"{t.name:<22} [{kind}{', cryoplug mcp only' if t.local_only else ''}] {t.description}")
    elif args.action in ("call", "check"):
        _ai_call(args)


def _ai_skills(args: argparse.Namespace) -> None:
    from cryoplug.agent import skills as sk

    if args.write_reference:
        print(f"Wrote {sk.write_job_index()}")
        return
    if args.install:
        dest = sk.DESTINATIONS.get(args.install, (args.install, ""))[0]
        try:
            written = sk.install(dest, args.names or None, force=args.force)
        except (FileExistsError, KeyError) as exc:
            sys.exit(str(exc).strip("'\""))
        for path in written:
            print(f"Installed {path}")
        print("Restart the AI client to load them.")
        return
    for skill in sk.all_skills():
        print(f"{skill.name}\n  {skill.description}\n  {skill.path}")
    print("\nInstall them: cryoplug ai skills --install claude   (Claude Code: ~/.claude/skills)")
    print("               cryoplug ai skills --install agents   (Codex and other Agent Skills clients: ~/.agents/skills)")
    print("               cryoplug ai skills --install DIR      (any folder, e.g. a project's .claude/skills)")


def _ai_setup(args: argparse.Namespace) -> None:
    from cryoplug.agent.client import local_url
    from cryoplug.db import Database
    from cryoplug.server.auth import auth_mode

    cfg = _server_config(args)
    mode = auth_mode(cfg, cfg.db_path.exists() and Database(cfg.db_path).has_users())
    command = _agent_command()
    token = args.token or ("<API token>" if mode == "users" else "")
    env = {"CRYOPLUG_TOKEN": token} if token else {}
    if args.url:
        env["CRYOPLUG_URL"] = args.url
    url = (args.url or local_url(cfg)).rstrip("/")
    print(f"CryoPlug server: {url} (protection: {mode or 'none, localhost only'})")
    if mode == "users":
        print("With user accounts, the AI acts as one account through an API token: create it in Settings > AI agents,")
        print("or here with `cryoplug user token <name>`, and put it in place of <API token> below.")
    elif mode in ("token", "password"):
        print(f"`{' '.join(command)}` reads the {'access token' if mode == 'token' else 'password'} of this server by itself "
              "when the AI client runs on this machine as this Unix account; elsewhere, set CRYOPLUG_URL and CRYOPLUG_TOKEN.")
    envflags = "".join(f" --env {k}={v}" for k, v in env.items())
    print("\n# Claude Code")
    print(f"claude mcp add cryoplug{envflags} -- {' '.join(command)}")
    print("cryoplug ai skills --install claude")
    print("\n# Codex (~/.codex/config.toml)")
    print("[mcp_servers.cryoplug]")
    print(f"command = {json.dumps(command[0])}")
    print(f"args = {json.dumps(command[1:])}")
    if env:
        print("env = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }")
    print("# then: cryoplug ai skills --install agents")
    print("\n# Claude Desktop, LM Studio (mcp.json), Cursor, Gemini CLI (settings.json), Goose, Jan...")
    entry: dict = {"command": command[0], "args": command[1:]}
    if env:
        entry["env"] = env
    print(json.dumps({"mcpServers": {"cryoplug": entry}}, indent=2))
    print("\n# Clients that connect to a URL (Open WebUI: Admin > Settings > Integrations > MCP (Streamable HTTP))")
    print(f"URL: {url}/mcp" + (f"   Authorization: Bearer {token or '<access token or password>'}" if mode else "   (no authentication)"))
    if mode is None:
        print("Note: without login, CryoPlug answers only on localhost; a client in a container needs a login mode.")
    print("\nSmall local model: `cryoplug mcp --tools basic` (12 tools); an assistant that changes nothing: --tools read")
    print("(for the URL: /mcp?tools=basic). Test the connection: cryoplug ai check")


def _ai_call(args: argparse.Namespace) -> None:
    from cryoplug.agent import tools as toolmod
    from cryoplug.agent.client import AgentError, Client, connection

    try:
        session = toolmod.Session(Client(connection(args.config, args.url, args.token)), allow_commands=args.allow_commands,
                                  local_files=True)
    except AgentError as exc:
        sys.exit(str(exc))
    name = "server_status" if args.action == "check" else args.tool
    arguments: dict = {}
    for item in getattr(args, "arguments", None) or []:
        if item.lstrip().startswith("{"):
            try:
                arguments.update(json.loads(item))
            except ValueError as exc:
                sys.exit(f"Invalid JSON arguments: {exc}")
            continue
        key, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"Arguments are key=value (or a JSON object), not {item!r}")
        try:
            arguments[key] = json.loads(value)
        except ValueError:
            arguments[key] = value
    reply = toolmod.call(session, name, arguments)
    print(reply.text)
    if reply.images:
        folder = Path(args.images or ".")
        folder.mkdir(parents=True, exist_ok=True)
        for i, (mime, data) in enumerate(reply.images, 1):
            path = folder / f"{name}_{i}.{mime.split('/')[-1].replace('jpeg', 'jpg')}"
            path.write_bytes(data)
            print(f"[image saved: {path}]")
    if reply.error:
        sys.exit(1)


def cmd_upload(args: argparse.Namespace) -> None:
    """``cryoplug upload P1 FILE...``: send files or folders of this computer into the uploads folder of a project."""
    from cryoplug.agent import tools as toolmod
    from cryoplug.agent.client import AgentError, Client, connection, upload_file, upload_folder

    paths = [Path(p).expanduser() for p in args.paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        sys.exit(f"Not found: {', '.join(missing)}")
    try:
        session = toolmod.Session(Client(connection(args.config, args.url, args.token)), local_files=True)
        project = session.project(args.project)
    except AgentError as exc:
        sys.exit(str(exc))
    tty = sys.stderr.isatty()
    for path in paths:
        started = time.time()

        def progress(sent: int, total: int, name: str = path.name, started: float = started) -> None:
            if tty:
                rate = sent / max(time.time() - started, 1e-3)
                print(f"\r  {name}: {sent / 2**20:,.0f} / {total / 2**20:,.0f} MB ({rate / 2**20:,.0f} MB/s)  ",
                      end="", file=sys.stderr, flush=True)

        try:
            if path.is_dir():
                res = upload_folder(session.client, project["uid"], path, progress)
                files = [e["path"] for e in session.get(f"/api/projects/{project['uid']}/uploads")["files"]
                         if e["rel"].startswith(f"{res['folder']}/")]
                line = f"{path}/ -> {res['path']} ({res['files']} files, {toolmod.size(res['size'])})"
                suggestions = toolmod.import_suggestions(files, res["path"])
            else:
                res = upload_file(session.client, project["uid"], path, progress=progress)
                line = f"{path} -> {res['path']}" + (" (already there)" if res.get("reused") else "")
                suggestions = toolmod.import_suggestions([res["path"]])
        except (AgentError, OSError) as exc:
            if tty:
                print(file=sys.stderr)
            sys.exit(f"{path}: {exc}")
        if tty:
            print("\r\033[K", end="", file=sys.stderr)
        print(line)
        for jtype, params, note in suggestions:  # in the shell, as an agent would create the job
            print(f"  import: cryoplug ai call create_job project={project['uid']} type={jtype} "
                  f"{shlex.quote('params=' + json.dumps(params))} queue=true" + (f"   # {note}" if note else ""))


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
Environment=PYTHONUNBUFFERED=1
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
    p.add_argument("--host", help='Listening address; "0.0.0.0" = reachable from other computers')
    p.add_argument("--port", type=int)
    p.add_argument("--log-level", default="warning")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("url", help="Print the address(es) to open, with the access token")
    p.add_argument("--host", help="Same as given to `cryoplug start`, if it differs from the configuration")
    p.add_argument("--port", type=int)
    p.add_argument("--reset-token", action="store_true", help="Create a new access token (restart the server afterwards)")
    p.set_defaults(func=cmd_url)

    p = sub.add_parser("user", help="Manage user accounts (also in Settings > Users)")
    usub = p.add_subparsers(dest="action", required=True)
    usub.add_parser("list", help="List the accounts")
    up = usub.add_parser("add", help="Create an account (the first one is an administrator)")
    up.add_argument("name")
    up.add_argument("--admin", action="store_true", help="Administrator (manages accounts, sees every project)")
    up.add_argument("--full-name")
    up.add_argument("--email")
    up.add_argument("--projects-dir", help="Where the user's projects go (default: <projects_root>/<name>)")
    up.add_argument("--allow", action="append", metavar="DIR", help="A folder the user may read data from (repeat)")
    up.add_argument("--generate", action="store_true", help="Generate a random password and print it")
    up.add_argument("--password", help=argparse.SUPPRESS)
    up.add_argument("--must-change", action="store_true", help="Ask for a new password at the first login")
    up = usub.add_parser("passwd", help="Set a new password")
    up.add_argument("name")
    up.add_argument("--generate", action="store_true")
    up.add_argument("--password", help=argparse.SUPPRESS)
    up.add_argument("--must-change", action="store_true")
    up = usub.add_parser("set", help="Change the role, name, e-mail or folders of an account")
    up.add_argument("name")
    up.add_argument("--role", choices=["admin", "user"])
    up.add_argument("--full-name")
    up.add_argument("--email")
    up.add_argument("--projects-dir")
    up.add_argument("--allow", action="append", metavar="DIR", help="Replaces the folders the user may read (repeat)")
    for action in ("disable", "enable"):
        usub.add_parser(action, help=f"{action.capitalize()} an account").add_argument("name")
    up = usub.add_parser("delete", help="Delete an account")
    up.add_argument("name")
    up.add_argument("--transfer-to", metavar="USER", help="Give the user's projects to this account")
    up = usub.add_parser("token", help="API token of an account, for scripts and AI agents (printed once)")
    up.add_argument("name")
    up.add_argument("--label", help="What it is for (default: AI agent)")
    up.add_argument("--days", type=int, default=365, help="Validity in days (default: 365)")
    up.add_argument("--list", action="store_true", help="List the account's API tokens instead")
    up.add_argument("--revoke", metavar="ID", help="Revoke the API token with this id (from --list)")
    p.set_defaults(func=cmd_user)

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

    def connection_options(q: argparse.ArgumentParser) -> None:
        q.add_argument("--url", help="CryoPlug server (default: $CRYOPLUG_URL, else the server of the configuration)")
        q.add_argument("--token", help="Credential (default: $CRYOPLUG_TOKEN, else found for the local server)")
        q.add_argument("--allow-commands", action="store_true",
                       help="Let the agent use custom commands and extra program arguments (administrators)")

    p = sub.add_parser("mcp", help="MCP server for AI agents, over stdio (started by the AI client)")
    connection_options(p)
    p.add_argument("--tools", default="all", metavar="PROFILE",
                   help="all (default), basic (12 tools, for small local models), read (change nothing) or tool names")
    p.set_defaults(func=cmd_mcp)

    p = sub.add_parser("ai", help="AI agents: connect a client, install the skills, call a tool")
    asub = p.add_subparsers(dest="action", required=True)
    ap = asub.add_parser("setup", help="How to connect Claude Code, Codex, Claude Desktop, LM Studio, Open WebUI...")
    ap.add_argument("--url", help="Address of the server as seen by the AI client (another machine)")
    ap.add_argument("--token", help="API token to write in the snippets")
    ap.add_argument("--host", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, help=argparse.SUPPRESS)
    ap = asub.add_parser("skills", help="List the agent skills, or install them for an AI client")
    ap.add_argument("--install", metavar="WHERE", help="claude (~/.claude/skills), agents (~/.agents/skills) or a folder")
    ap.add_argument("--force", action="store_true", help="Replace skills already installed")
    ap.add_argument("--write-reference", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("names", nargs="*", help="Only these skills")
    asub.add_parser("tools", help="List the tools offered to agents")
    ap = asub.add_parser("check", help="Test the connection as an agent would (server_status)")
    connection_options(ap)
    ap = asub.add_parser("call", help="Call an agent tool from the shell, e.g. cryoplug ai call get_project project=P1")
    ap.add_argument("tool")
    ap.add_argument("arguments", nargs="*", help="key=value (values in JSON: 3, true, \"J3.map\") or a JSON object")
    ap.add_argument("--images", metavar="DIR", help="Folder for the images a tool returns (default: here)")
    connection_options(ap)
    p.set_defaults(func=cmd_ai)

    p = sub.add_parser("upload", help="Send files or folders of this computer to a project (its uploads folder)")
    p.add_argument("project", help="Project id (P1) or title")
    p.add_argument("paths", nargs="+", metavar="PATH", help="Files or folders (a folder keeps its inner paths)")
    p.add_argument("--url", help="CryoPlug server (default: $CRYOPLUG_URL, else the server of the configuration)")
    p.add_argument("--token", help="Credential (default: $CRYOPLUG_TOKEN, else found for the local server)")
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser("worker", help=argparse.SUPPRESS)
    p.add_argument("job_dir")
    p.set_defaults(func=lambda a: __import__("cryoplug.worker", fromlist=["main"]).main([a.job_dir]))

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
