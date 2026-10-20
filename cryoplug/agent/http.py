"""Streamable HTTP transport of the MCP server, on the CryoPlug server itself: ``POST /mcp``.

For AI clients that connect to a URL rather than start a program (Open WebUI, LM Studio with a remote server,
``claude mcp add --transport http``...): ``http://<server>:39500/mcp``, with the credential of the HTTP API in an
``Authorization: Bearer`` header (an API token of a user account, the access token or the shared password; none on
localhost without login).  The access control of the server applies first, as to any request.

Stateless: each POST carries one JSON-RPC message (or a batch) and is answered with JSON — no session, no stream
opened by the server (GET and DELETE answer 405).  The tools call the HTTP API inside the process, through the
whole application (authentication, permissions), with the credentials of the MCP request: an agent can do what its
credential allows, nothing more.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import anyio
import anyio.from_thread
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from cryoplug.agent import tools as toolmod
from cryoplug.agent.client import api_error
from cryoplug.agent.mcp import (
    INVALID_REQUEST,
    PARSE_ERROR,
    PROTOCOL_VERSIONS,
    Server,
    _error,
)

FORWARDED_HEADERS = {b"authorization", b"cookie", b"host", b"user-agent", b"x-forwarded-host", b"x-forwarded-for"}
DEFAULT_PROTOCOL = "2025-03-26"  # what a request without MCP-Protocol-Version is assumed to speak


async def _asgi_call(app: Any, scope: dict[str, Any], body: bytes) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    status, headers, chunks = 500, [], []
    sent = False
    finished = anyio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status, headers = message["status"], list(message.get("headers") or [])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                finished.set()

    try:
        await app(scope, receive, send)
    finally:
        finished.set()
    return status, headers, b"".join(chunks)


class AsgiClient:
    """The tools' client inside the server: each request goes through the application (authentication,
    permissions) without the network, with the headers (credentials) of the MCP request."""

    def __init__(self, app: Any, request: Request):
        self.app = app
        self.headers = [(k, v) for k, v in request.scope.get("headers", []) if k in FORWARDED_HEADERS]
        self.client = request.scope.get("client") or ("127.0.0.1", 0)
        self.server = request.scope.get("server") or ("127.0.0.1", 80)
        self.scheme = request.url.scheme
        self.url = str(request.base_url).rstrip("/")

    def request(self, method: str, path: str, params: dict[str, Any] | None = None, body: Any = None,
                raw: bool = False, timeout: float | None = None, max_bytes: int | None = None) -> Any:
        query = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in (params or {}).items() if v is not None}
        data = b"" if body is None else json.dumps(body).encode()
        headers = list(self.headers)
        if body is not None:
            headers.append((b"content-type", b"application/json"))
        headers.append((b"content-length", str(len(data)).encode()))
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
                 "method": method, "scheme": self.scheme, "path": path, "raw_path": path.encode(),
                 "query_string": urlencode(query).encode(), "root_path": "", "headers": headers,
                 "client": self.client, "server": self.server}
        status, resp_headers, content = anyio.from_thread.run(_asgi_call, self.app, scope, data)
        if status >= 400:
            raise api_error(status, content, "", path)
        if raw:
            ctype = next((v.decode() for k, v in resp_headers if k.lower() == b"content-type"), "")
            return (content[:max_bytes] if max_bytes else content), ctype
        return json.loads(content) if content else None


def agent_overview(app: FastAPI) -> dict[str, Any]:
    """What Settings › AI agents shows: how to connect a client, the tools and the skills."""
    from cryoplug.agent.skills import all_skills

    exe = shutil.which("cryoplug")
    return {
        "auth": app.state.auth.mode,
        "mcp_path": "/mcp",
        "command": ["cryoplug", "mcp"],
        "command_path": str(Path(exe).resolve()) if exe else "",
        "protocol_versions": list(PROTOCOL_VERSIONS),
        "tools": [{"name": t.name, "title": t.title, "description": t.description,
                   "kind": "read" if t.read_only else "destructive" if t.destructive else "write"}
                  for t in toolmod.TOOLS.values()],
        "skills": [{"name": sk.name, "description": sk.description} for sk in all_skills()],
    }


def install(app: FastAPI) -> None:
    """Add the MCP endpoint ``/mcp`` (and ``/api/agent``, for Settings › AI agents) to the CryoPlug application."""

    @app.get("/api/agent")
    def agent_info() -> dict[str, Any]:
        return agent_overview(app)

    @app.post("/mcp", include_in_schema=False)
    async def mcp_endpoint(request: Request) -> Response:
        try:
            message = json.loads(await request.body() or b"null")
        except ValueError as exc:
            return JSONResponse(_error(None, PARSE_ERROR, f"Parse error: {exc}"), status_code=400)
        version = request.headers.get("mcp-protocol-version")
        if version and version not in PROTOCOL_VERSIONS:
            return JSONResponse(_error(None, INVALID_REQUEST, f"Unsupported MCP protocol version {version} "
                                                              f"(supported: {', '.join(PROTOCOL_VERSIONS)})"), status_code=400)
        try:  # /mcp?tools=basic (small local models) or ?tools=read (look, change nothing)
            names = toolmod.select_tools(request.query_params.get("tools"))
        except ValueError as exc:
            return JSONResponse(_error(None, INVALID_REQUEST, str(exc)), status_code=400)
        server = Server(lambda: toolmod.Session(AsgiClient(app, request)), tools=names)
        server.protocol = version or DEFAULT_PROTOCOL
        messages = message if isinstance(message, list) else [message]
        # In a worker thread: the tools call the application back (AsgiClient) and may wait (wait_for_jobs).
        replies = await run_in_threadpool(lambda: [r for r in (server.handle(m) for m in messages) if r is not None])
        if not replies:
            return Response(status_code=202)  # notifications and responses only
        return JSONResponse(replies if isinstance(message, list) else replies[0])

    @app.get("/mcp", include_in_schema=False)
    def mcp_stream() -> Response:
        return Response("This MCP server does not open a server-sent event stream: POST JSON-RPC messages to /mcp.",
                        status_code=405, headers={"Allow": "POST"})

    @app.delete("/mcp", include_in_schema=False)
    def mcp_end() -> Response:
        return Response("No MCP session to end: this server is stateless.", status_code=405, headers={"Allow": "POST"})
