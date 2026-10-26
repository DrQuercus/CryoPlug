"""AI agents: API tokens of user accounts, the agent tools (through the HTTP API), the MCP server over stdio and
over HTTP (/mcp), the skills and the `cryoplug ai` / `cryoplug mcp` commands."""
from __future__ import annotations

import io
import json
import re
import threading
import time

import pytest
from conftest import run_until_done
from fastapi.testclient import TestClient

from cryoplug.agent import mcp as mcpmod
from cryoplug.agent import skills as sk
from cryoplug.agent import tools as toolmod
from cryoplug.agent.client import (
    AgentError,
    Connection,
    api_error,
    connection,
    local_url,
)
from cryoplug.cli import main as cli_main
from cryoplug.server.app import create_app

BASE = "http://localhost:39500"


class ApiAdapter:
    """The agent tools' client over FastAPI's TestClient (what Client does over the network)."""

    def __init__(self, client: TestClient, token: str = ""):
        self.client = client
        self.url = BASE
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def request(self, method, path, params=None, body=None, raw=False, timeout=None, max_bytes=None, data=None):
        query = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in (params or {}).items() if v is not None}
        if data is not None:  # a chunk of a file
            r = self.client.request(method, path, params=query, content=data, headers=self.headers)
        else:
            r = self.client.request(method, path, params=query, json=body, headers=self.headers)
        if r.status_code >= 400:
            raise api_error(r.status_code, r.content, r.reason_phrase, path)
        if raw:
            return (r.content[:max_bytes] if max_bytes else r.content), r.headers.get("content-type", "")
        return r.json() if r.content else None


def call(session, tool, /, **arguments):
    return toolmod.call(session, tool, arguments)


@pytest.fixture()
def app_client(manager):
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url=BASE) as client:
        yield app, client


# ------------------------------------------------------------------ API tokens
def test_api_tokens_of_accounts(manager):
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    users = app.state.users
    alice = users.create("alice", "correct horse battery", role="admin")
    users.create("bob", "another long password")
    with TestClient(app, base_url=BASE) as client:
        assert client.get("/api/info").status_code == 401
        refused = client.get("/api/info", headers={"Authorization": "Bearer nope"})
        assert refused.status_code == 401 and refused.json()["auth"] == "users"

        token, info = users.new_token(alice, "Claude Code", 30)
        assert token.startswith("cpt_") and info["label"] == "Claude Code"
        bearer = {"Authorization": f"Bearer {token}"}
        me = client.get("/api/me", headers=bearer).json()
        assert me["username"] == "alice"
        p = client.post("/api/projects", json={"title": "Token project"}, headers=bearer)
        assert p.status_code == 200 and p.json()["owner"] == "alice"
        # A token cannot manage accounts, tokens or settings, nor change the password
        assert client.post("/api/users", json={"username": "eve", "password": "x" * 12}, headers=bearer).status_code == 403
        assert client.post("/api/me/tokens", json={"label": "more"}, headers=bearer).status_code == 403
        assert client.patch("/api/settings", json={"session_days": 3}, headers=bearer).status_code == 403
        assert client.get("/api/users", headers=bearer).status_code == 200  # reading is fine (administrator)

        # In the web interface: create, list, revoke (browser session of bob)
        login = client.post("/login", data={"username": "bob", "password": "another long password", "next": "/"},
                            follow_redirects=False)
        assert login.status_code == 303
        made = client.post("/api/me/tokens", json={"label": "LM Studio", "days": 7}).json()
        assert made["token"].startswith("cpt_") and made["label"] == "LM Studio" and made["last_used_at"] is None
        assert [t["label"] for t in client.get("/api/me/tokens").json()] == ["LM Studio"]
        assert all(not s.get("label") for s in client.get("/api/me/sessions").json())  # tokens are not browsers
        client.post("/api/me/sessions/end-others")  # logs out other browsers, keeps the tokens
        browser = dict(client.cookies)
        client.cookies.clear()  # from here, the token alone
        bob_bearer = {"Authorization": f"Bearer {made['token']}"}
        assert client.get("/api/me", headers=bob_bearer).json()["username"] == "bob"
        assert users.tokens(users.get("bob"))[0]["last_used_at"] is not None
        # bob does not see alice's project, even with his token
        assert all(x["title"] != "Token project" for x in client.get("/api/projects", headers=bob_bearer).json())
        client.cookies.update(browser)
        assert client.delete(f"/api/me/tokens/{made['id']}").json()["label"] == "LM Studio"
        client.cookies.clear()
        assert client.get("/api/me", headers=bob_bearer).status_code == 401

        # A new password ends the tokens too; a disabled account cannot use them
        users.set_password("alice", "a brand new password")
        assert client.get("/api/me", headers=bearer).status_code == 401
        token2, _ = users.new_token(users.get("alice"), "agent")
        users.create("carol", "carol long password", role="admin")
        users.update("alice", disabled=True)
        assert client.get("/api/me", headers={"Authorization": f"Bearer {token2}"}).status_code == 401
    with pytest.raises(ValueError):
        users.new_token(alice, "x", 0)


def test_user_token_command(manager, capsys, tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'[server]\ndata_dir = "{manager.config.data_dir}"\nprojects_root = "{manager.config.projects_root}"\n')
    with pytest.raises(SystemExit, match="No user accounts"):
        cli_main(["-c", str(cfg_path), "user", "token", "alice"])
    cli_main(["-c", str(cfg_path), "user", "add", "alice", "--password", "correct horse battery"])
    capsys.readouterr()
    cli_main(["-c", str(cfg_path), "user", "token", "alice", "--label", "Codex", "--days", "10"])
    out = capsys.readouterr()
    token = out.out.strip()
    assert token.startswith("cpt_") and "\n" not in token and "shown only once" in out.err
    cli_main(["-c", str(cfg_path), "user", "token", "alice", "--list"])
    listed = capsys.readouterr().out
    assert "Codex" in listed
    token_id = listed.split()[0]
    cli_main(["-c", str(cfg_path), "user", "token", "alice", "--revoke", token_id])
    assert "Revoked" in capsys.readouterr().out


# ------------------------------------------------------------------ client
def test_connection_resolution(tmp_path, monkeypatch):
    for var in ("CRYOPLUG_URL", "CRYOPLUG_TOKEN", "CRYOPLUG_TOKEN_FILE", "CRYOPLUG_CA_FILE"):
        monkeypatch.delenv(var, raising=False)
    data = tmp_path / "data"
    data.mkdir()
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[server]\nhost = "0.0.0.0"\nport = 40001\ndata_dir = "{data}"\n')
    conn = connection(cfg)
    assert conn.url == "http://127.0.0.1:40001" and conn.token == "" and conn.local and conn.loopback
    (data / "access_token").write_text("secret-token\n")
    conn = connection(cfg)
    assert conn.token == "secret-token" and conn.source.endswith("access_token")
    # Another server: the local credential is never sent there
    monkeypatch.setenv("CRYOPLUG_URL", "gpu-box:39500")
    conn = connection(cfg)
    assert conn.url == "http://gpu-box:39500" and conn.token == "" and not conn.loopback
    monkeypatch.setenv("CRYOPLUG_TOKEN", "cpt_abc")
    assert connection(cfg).token == "cpt_abc"
    assert connection(cfg, url="https://x:1", token="t").url == "https://x:1"
    cfg.write_text(f'[server]\nhost = "::"\nport = 40002\npassword = "pw"\ndata_dir = "{data}"\n')
    monkeypatch.delenv("CRYOPLUG_URL")
    monkeypatch.delenv("CRYOPLUG_TOKEN")
    conn = connection(cfg)
    assert conn.url == "http://[::1]:40002" and conn.token == "pw"
    assert local_url(type("C", (), {"host": "10.0.0.5", "port": 1, "ssl_certfile": "c.pem"})()) == "https://10.0.0.5:1"


def test_client_errors_explain_the_credential():
    err = api_error(401, json.dumps({"detail": "Authentication required", "auth": "users"}).encode(), "", "/api/info", "CRYOPLUG_TOKEN")
    assert "refused" in str(err) and "API token" in str(err) and err.status == 401
    assert "new password" in str(api_error(403, b'{"detail": "x", "code": "password_change"}'))
    assert str(api_error(400, b'{"detail": [{"msg": "field required"}]}')) == "field required"
    from cryoplug.agent.client import Client
    with pytest.raises(AgentError, match="Cannot reach CryoPlug"):
        Client(Connection("http://127.0.0.1:9"), timeout=2).get("/api/info")


# ------------------------------------------------------------------ tools
def test_agent_tools_end_to_end(app_client, manager, synthetic):
    _, client = app_client
    s = toolmod.Session(ApiAdapter(client))

    status = call(s, "server_status")
    assert not status.error
    assert "Acting as the owner of the server" in status.text and "local (local, GPUs 0,1)" in status.text
    assert "Programs installed:" in status.text and "Skills (read_skill NAME): cryoplug" in status.text

    made = call(s, "create_project", title="Agent test", description="from the tests")
    assert "Created P1" in made.text
    assert "P1" in call(s, "list_projects").text

    types = call(s, "list_job_types", category="map processing")
    assert "map_fsc — Half-map FSC [built-in]" in types.text and "import_model" not in types.text
    assert "NOT INSTALLED" not in call(s, "list_job_types", search="modelangelo").text
    described = call(s, "describe_job_type", name="map_fsc")
    assert "half_maps (half_maps)" in described.text and "Outputs: fsc (fsc)" in described.text
    assert "Guidance" in described.text
    wrong = call(s, "describe_job_type", name="model_angelo")
    assert wrong.error and "modelangelo_build" in wrong.text

    imp = call(s, "create_job", project="Agent test", type="import_maps", queue=True, params={
        "half_map_a": str(synthetic["half_a"]), "half_map_b": str(synthetic["half_b"]),
        "map_sharp": str(synthetic["map"]), "mask": str(synthetic["mask"]), "resolution": "3.0"})
    assert not imp.error, imp.text
    assert "Created P1/J1" in imp.text and "Queued on lane local" in imp.text

    # The FSC job is connected to J1 before J1 finished: planned outputs, the job waits
    planned = call(s, "find_outputs", project="P1", type="half_maps")
    assert "J1.half_maps (half_maps)" in planned.text and "planned" in planned.text
    fsc = call(s, "create_job", project="p1", type="map_fsc", inputs={"half_maps": "J1", "mask": "1.mask"})
    assert not fsc.error, fsc.text
    assert "Inputs: half_maps ← J1.half_maps, mask ← J1.mask" in fsc.text
    assert "Ready: queue_job runs it (it will wait for J1 (queued))" in fsc.text
    queued = call(s, "queue_job", project="P1", job="2")
    assert "waiting for J1" in queued.text, queued.text

    run_until_done(manager, "P1", ["J1", "J2"])
    waited = call(s, "wait_for_jobs", project="P1", jobs=["J1", "J2"], timeout_seconds=5)
    assert "Completed: J1, J2" in waited.text, waited.text

    job = call(s, "get_job", project="P1", job="J2")
    assert "status completed" in job.text and "half_maps ← J1.half_maps" in job.text
    assert re.search(r"crosses 0\.143 at \d+\.\d+ Å", job.text), job.text
    outputs = call(s, "find_outputs", project="P1", type="map")
    assert "J1.map_sharp (map)" in outputs.text and "pixel 1 Å" in outputs.text and "box 48³" in outputs.text

    log = call(s, "get_job_log", project="P1", job="J1", search="Output 'half_maps'")
    assert "1 matching line" in log.text
    assert "last" in call(s, "get_job_log", project="P1", job="J1", tail=5).text
    info = call(s, "map_info", project="P1", path="J1.map_sharp")
    assert "box" in info.text.lower()
    files = call(s, "list_job_files", project="P1", job="J1")
    assert "J1/job.log" in files.text
    text = call(s, "read_project_file", project="P1", path="J1/job.log", max_lines=3)
    assert "lines 1-3 of" in text.text
    assert call(s, "read_project_file", project="P1", path="J1/" + synthetic["map"].name).error
    images = call(s, "get_job_images", project="P1", job="J1")
    assert images.images and images.images[0][0] == "image/png" and images.images[0][1][:4] == b"\x89PNG"

    # Clone with other parameters; update merges inputs; refusals
    clone = call(s, "clone_job", project="P1", job="J2", title="FSC copy")
    assert "Copied J2 as J3" in clone.text
    upd = call(s, "update_job", project="P1", job="J3", inputs={"mask": None})
    assert "Inputs: half_maps ← J1.half_maps" in upd.text and "mask ←" not in upd.text
    no_param = call(s, "update_job", project="P1", job="J3", params={"resolution": 3})
    assert no_param.error and "map_fsc has no parameters" in no_param.text
    bad_param = call(s, "create_job", project="P1", type="create_mask", inputs={"map": "J1"}, params={"dilation": 3})
    assert bad_param.error and "did you mean dilate" in bad_param.text
    bad_slot = call(s, "create_job", project="P1", type="map_fsc", inputs={"halfmaps": "J1"})
    assert bad_slot.error and "half_maps" in bad_slot.text
    wrong_type = call(s, "create_job", project="P1", type="map_fsc", inputs={"half_maps": "J1.map_sharp"})
    assert wrong_type.error and "takes half_maps" in wrong_type.text
    shell = call(s, "create_job", project="P1", type="custom_command")
    assert shell.error and "not available to agents" in shell.text
    assert call(s, "clear_job", project="P1", job="J2").error  # completed: refused
    assert "nothing to stop" in call(s, "kill_job", project="P1", job="J2").text
    assert "not running" in call(s, "control_job", project="P1", job="J2", action="stop").text
    resumed = call(s, "resume_job", project="P1", job="J2")  # completed: nothing to resume
    assert resumed.error and "failed or killed" in resumed.text
    call(s, "queue_job", project="P1", job="J3")
    killed = call(s, "kill_job", project="P1", job="J3")
    assert "queued → building" in killed.text

    wf = call(s, "list_workflows")
    assert "map_enhancement" in wf.text
    applied = call(s, "apply_workflow", project="P1", workflow="validate_deposit", include=[])
    assert not applied.error and "Created" in applied.text, applied.text
    project = call(s, "get_project", project="P1", status="completed")
    assert "J1   completed import_maps" in project.text and "J2   completed map_fsc" in project.text

    # workspaces, tags and a chain copied as a chain
    ws = call(s, "create_workspace", project="P1", title="Maps", jobs=["J1", "J2"])
    assert "Created workspace W1 “Maps” in P1 with 2 jobs" in ws.text, ws.text
    tagged = call(s, "update_job", project="P1", job="J2", tags="best, dataset 2")
    assert not tagged.error, tagged.text
    chain = call(s, "clone_jobs", project="P1", jobs=["J2", "J1"], workspace="W1")
    assert "Copied J1, J2 as" in chain.text and "half_maps←J" in chain.text, chain.text
    in_ws = call(s, "get_project", project="P1", workspace="w1")
    assert "Workspaces: W1 “Maps” (4 jobs) — showing W1" in in_ws.text and "[best, dataset 2]" in in_ws.text, in_ws.text
    assert "J3 " not in in_ws.text  # not in the workspace
    assert "best" in call(s, "get_project", project="P1", tag="BEST").text
    assert call(s, "get_project", project="P1", workspace="W7").error

    browse = call(s, "browse_server_files", path=str(synthetic["cryosparc_job"]), pattern="*half*")
    assert "half_A.mrc" in browse.text and "volume_map_sharp" not in browse.text
    assert "cryoplug-model-building" in call(s, "list_skills").text
    assert call(s, "read_skill", name="cryoplug").text.startswith("---")
    assert call(s, "read_skill", name="nope").error
    ignored = call(s, "list_projects", colour="red")
    assert "Ignored unknown argument: colour" in ignored.text


def test_failed_job_shows_error_and_log(app_client, manager):
    _, client = app_client
    s = toolmod.Session(ApiAdapter(client))
    call(s, "create_project", title="Failing")
    made = call(s, "create_job", project="P1", type="import_model", params={"source": "file", "path": "/nonexistent/model.pdb"},
                queue=True)
    assert not made.error, made.text
    run_until_done(manager, "P1", ["J1"])
    job = call(s, "get_job", project="P1", job="J1")
    assert "status failed" in job.text and "Error:" in job.text and "End of the log:" in job.text
    cleared = call(s, "clear_job", project="P1", job="J1")
    assert "back to building" in cleared.text


# ------------------------------------------------------------------ MCP
def _rpc(server, method, params=None, msg_id=1):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


def test_mcp_protocol(app_client):
    _, client = app_client
    server = mcpmod.Server(lambda: toolmod.Session(ApiAdapter(client)))
    init = _rpc(server, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}})["result"]
    assert init["protocolVersion"] == "2025-06-18" and init["serverInfo"]["name"] == "cryoplug"
    assert "tools" in init["capabilities"] and "server_status" in init["instructions"]
    assert _rpc(server, "initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"] == "2025-11-25"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert _rpc(server, "ping")["result"] == {}

    tools = _rpc(server, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"server_status", "create_job", "wait_for_jobs", "read_skill"} <= names
    assert not names & {"delete_job", "delete_project"}
    kill = next(t for t in tools if t["name"] == "kill_job")
    assert kill["annotations"]["destructiveHint"] is True and kill["annotations"]["readOnlyHint"] is False
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and set(t["inputSchema"]["required"]) <= set(t["inputSchema"]["properties"])
        assert len(t["description"]) < 700, t["name"]

    result = _rpc(server, "tools/call", {"name": "create_project", "arguments": {"title": "MCP"}})["result"]
    assert result["content"][0]["type"] == "text" and "Created P1" in result["content"][0]["text"]
    assert "isError" not in result
    error = _rpc(server, "tools/call", {"name": "get_project", "arguments": {"project": "P9"}})["result"]
    assert error["isError"] is True and "not found" in error["content"][0]["text"]
    unknown = _rpc(server, "tools/call", {"name": "nope", "arguments": {}})
    assert unknown["error"]["code"] == mcpmod.INVALID_PARAMS

    resources = _rpc(server, "resources/list")["result"]["resources"]
    uris = {r["uri"] for r in resources}
    assert "cryoplug://guide" in uris and "cryoplug://skills/cryoplug/SKILL.md" in uris
    skill = _rpc(server, "resources/read", {"uri": "cryoplug://skills/cryoplug/SKILL.md"})["result"]["contents"][0]
    assert skill["mimeType"] == "text/markdown" and "name: cryoplug" in skill["text"]
    project = _rpc(server, "resources/read", {"uri": "cryoplug://projects/P1"})["result"]["contents"][0]
    assert project["text"].startswith("P1 “MCP”")
    missing = _rpc(server, "resources/read", {"uri": "cryoplug://nothing"})
    assert missing["error"]["code"] == mcpmod.RESOURCE_NOT_FOUND
    templates = _rpc(server, "resources/templates/list")["result"]["resourceTemplates"]
    assert any("{job}" in t["uriTemplate"] for t in templates)

    prompts = _rpc(server, "prompts/list")["result"]["prompts"]
    assert {p["name"] for p in prompts} >= {"review_project", "troubleshoot_job"}
    got = _rpc(server, "prompts/get", {"name": "troubleshoot_job", "arguments": {"project": "P1", "job": "J4"}})["result"]
    assert "J4" in got["messages"][0]["content"]["text"] and got["messages"][0]["role"] == "user"
    assert _rpc(server, "prompts/get", {"name": "troubleshoot_job", "arguments": {}})["error"]["code"] == mcpmod.INVALID_PARAMS
    assert _rpc(server, "nope/nope")["error"]["code"] == mcpmod.METHOD_NOT_FOUND
    assert server.handle({"id": 3, "method": "ping"})["error"]["code"] == mcpmod.INVALID_REQUEST


def test_tool_profiles(app_client):
    _, client = app_client
    assert len(toolmod.select_tools("basic")) == 12 and set(toolmod.select_tools("all")) == set(toolmod.TOOLS)
    read = toolmod.select_tools("read")
    assert "get_job" in read and not {"create_job", "kill_job", "clear_job", "apply_workflow"} & set(read)
    assert toolmod.select_tools("get_job, server_status") == ["get_job", "server_status"]
    with pytest.raises(ValueError):
        toolmod.select_tools("get_job,rm_rf")
    server = mcpmod.Server(lambda: toolmod.Session(ApiAdapter(client)), tools=read)
    listed = {t["name"] for t in _rpc(server, "tools/list")["result"]["tools"]}
    assert listed == set(read)
    refused = _rpc(server, "tools/call", {"name": "create_project", "arguments": {"title": "x"}})
    assert refused["error"]["code"] == mcpmod.INVALID_PARAMS and "not enabled" in refused["error"]["message"]


def test_mcp_stdio_transport(app_client):
    _, client = app_client
    server = mcpmod.Server(lambda: toolmod.Session(ApiAdapter(client)))
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "list_projects", "arguments": {}}},
        [{"jsonrpc": "2.0", "id": 3, "method": "ping"}, {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}],
    ]
    data = "\n".join(json.dumps(x) for x in lines) + "\nnot json\n\n"
    out = io.BytesIO()
    mcpmod.serve_stdio(server, io.BytesIO(data.encode()), out)
    replies = [json.loads(line) for line in out.getvalue().decode().splitlines()]
    assert replies[0]["id"] == 1 and replies[0]["result"]["protocolVersion"] == "2025-11-25"
    by_id = {r["id"]: r for r in replies if isinstance(r, dict)}
    assert "No project yet" in by_id[2]["result"]["content"][0]["text"]
    assert any(r.get("error", {}).get("code") == mcpmod.PARSE_ERROR for r in replies if isinstance(r, dict))
    batch = next(r for r in replies if isinstance(r, list))
    assert {r["id"] for r in batch} == {3, 4}


def test_mcp_progress_and_cancellation(app_client, manager, synthetic):
    _, client = app_client
    s = toolmod.Session(ApiAdapter(client))
    call(s, "create_project", title="Waits")
    call(s, "create_job", project="P1", type="import_maps", queue=True,
         params={"half_map_a": str(synthetic["half_a"]), "half_map_b": str(synthetic["half_b"])})
    server = mcpmod.Server(lambda: s)
    notes = []
    result: dict = {}

    def run():
        result["reply"] = server.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "wait_for_jobs", "arguments": {"project": "P1", "timeout_seconds": 60}, "_meta": {"progressToken": "tok"}}},
            notes.append)

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.5)
    server.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7}})
    thread.join(15)
    assert not thread.is_alive()
    assert result["reply"] is None  # cancelled: no answer
    assert notes and notes[0]["method"] == "notifications/progress" and notes[0]["params"]["progressToken"] == "tok"


def test_mcp_over_http(manager):
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    users = app.state.users
    users.create("admin", "admin long password", role="admin")
    alice = users.create("alice", "correct horse battery", role="user")
    token, _ = users.new_token(alice, "Open WebUI")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18"}
    with TestClient(app, base_url=BASE) as client:
        assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401
        init = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                          "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        assert init.status_code == 200 and init.json()["result"]["protocolVersion"] == "2025-06-18"
        assert client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
        made = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "create_project", "arguments": {"title": "Over HTTP"}}}).json()
        assert "Created P1" in made["result"]["content"][0]["text"]
        assert manager.db.get_project("P1")["owner"] == "alice"
        status = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "server_status", "arguments": {}}}).json()["result"]["content"][0]["text"]
        assert "Acting as alice (user)" in status and "protection: user accounts" in status
        batch = client.post("/mcp", headers=headers, json=[{"jsonrpc": "2.0", "id": 4, "method": "ping"},
                                                           {"jsonrpc": "2.0", "id": 5, "method": "tools/list"}]).json()
        assert [r["id"] for r in batch] == [4, 5]
        assert client.post("/mcp", headers=headers, content=b"{oops").json()["error"]["code"] == mcpmod.PARSE_ERROR
        bad = dict(headers, **{"MCP-Protocol-Version": "1999-01-01"})
        assert client.post("/mcp", headers=bad, json={"jsonrpc": "2.0", "id": 6, "method": "ping"}).status_code == 400
        assert client.get("/mcp", headers=headers).status_code == 405
        read_only = client.post("/mcp?tools=read", headers=headers, json={"jsonrpc": "2.0", "id": 8, "method": "tools/list"}).json()
        assert "create_job" not in {t["name"] for t in read_only["result"]["tools"]}
        assert client.post("/mcp?tools=nope", headers=headers, json={"jsonrpc": "2.0", "id": 9, "method": "ping"}).status_code == 400
        # A browser page of another site cannot drive it (Origin check)
        evil = dict(headers, Origin="http://evil.example")
        assert client.post("/mcp", headers=evil, json={"jsonrpc": "2.0", "id": 7, "method": "ping"}).status_code == 403


# ------------------------------------------------------------------ files of the agent's computer
def test_import_suggestions_from_file_names():
    guess = toolmod.import_suggestions
    assert [toolmod.map_role(n) for n in ("J12_005_volume_map_half_A.mrc", "run_half2_class001_unfil.mrc",
                                          "emd_1234_half_map_1.map", "emd_1234_msk_1.map", "postprocess_masked.mrc",
                                          "J12_005_volume_mask_fsc_auto.mrc", "map_pp.mrc", "J12_005_volume_map.mrc")] == [
        "half_map_a", "half_map_b", "half_map_a", "mask", "map_sharp", "mask", "map_sharp", "map"]
    assert guess(["/u/emd_1234.map"]) == [("import_maps", {"map_sharp": "/u/emd_1234.map"}, "")]
    maps = guess(["/u/run_half1_class001_unfil.mrc", "/u/run_half2_class001_unfil.mrc", "/u/mask.mrc", "/u/model.cif",
                  "/u/seq.fasta", "/u/particles.star", "/u/stack.mrcs", "/u/notes.txt"])
    assert maps[0] == ("import_maps", {"half_map_a": "/u/run_half1_class001_unfil.mrc",
                                       "half_map_b": "/u/run_half2_class001_unfil.mrc", "mask": "/u/mask.mrc"}, "")
    assert [m[0] for m in maps[1:]] == ["import_model", "import_sequence", "import_particles"]
    assert guess(["/u/J9/job.json", "/u/J9/J9_002_volume_map.mrc"], "/u/J9")[0][:2] == ("import_cryosparc", {"job_dir": "/u/J9"})
    assert guess(["/u/s/frame_000.mrc", "/u/s/frame_001.mrc"], "/u/s")[0][:2] == ("import_volume_series", {"path": "/u/s"})
    assert guess(["/u/half_A.mrc"])[0][2] == "add the other half map"


def test_upload_tool_and_command(app_client, manager, synthetic, tmp_path, monkeypatch, capsys):
    app, client = app_client
    local = toolmod.Session(ApiAdapter(client), local_files=True)  # cryoplug mcp, on the computer with the files
    assert "Created P1" in call(local, "create_project", title="From my computer").text
    half_a = synthetic["half_a"]
    sent = call(local, "upload_file", project="P1", path=str(half_a))
    uploaded = manager.project_dir("P1") / "uploads" / half_a.name
    assert not sent.error and uploaded.read_bytes() == half_a.read_bytes(), sent.text
    assert f'create_job project=P1 type=import_maps params={{"half_map_a": "{uploaded}"}} queue=true' in sent.text
    assert call(local, "upload_file", project="P1", path=str(half_a)).text.startswith("Already uploaded")
    assert "No file or folder" in call(local, "upload_file", project="P1", path=str(tmp_path / "nope.mrc")).text
    # A CryoSPARC job folder: kept whole, then imported (hard links of the uploads)
    folder = call(local, "upload_file", project="P1", path=str(synthetic["cryosparc_job"]))
    job_dir = manager.project_dir("P1") / "uploads" / "J12"
    assert not folder.error and "6 files" in folder.text and f'"job_dir": "{job_dir}"' in folder.text, folder.text
    assert (job_dir / "J12_005_volume_map_half_B.mrc").read_bytes() == synthetic["half_b"].read_bytes()
    made = call(local, "create_job", project="P1", type="import_cryosparc", params={"job_dir": str(job_dir)}, queue=True)
    run_until_done(manager, "P1", ["J1"])
    detail = client.get("/api/projects/P1/jobs/J1").json()
    assert detail["status"] == "completed", (made.text, detail.get("error"))
    # Inside the server (/mcp), "this computer" would be the server: refused, and not offered
    remote = toolmod.Session(ApiAdapter(client))
    refused = call(remote, "upload_file", project="P1", path="/etc/passwd")
    assert refused.error and "cryoplug mcp" in refused.text
    assert "upload_file" not in toolmod.select_tools("all", local_files=False)
    with pytest.raises(ValueError, match="agent's computer"):
        toolmod.select_tools("upload_file", local_files=False)
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    assert "upload_file" not in {t["name"] for t in listed} and "browse_server_files" in {t["name"] for t in listed}
    over_http = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        "name": "upload_file", "arguments": {"project": "P1", "path": "/etc/passwd"}}}).json()
    assert over_http["error"]["code"] == mcpmod.INVALID_PARAMS
    assert next(t for t in client.get("/api/agent").json()["tools"] if t["name"] == "upload_file")["local"] is True
    # The same from the shell: cryoplug upload
    monkeypatch.setattr("cryoplug.agent.client.connection", lambda *a, **k: None)
    monkeypatch.setattr("cryoplug.agent.client.Client", lambda conn: ApiAdapter(client))
    cli_main(["upload", "P1", str(synthetic["model"]), str(synthetic["fasta"])])
    out = capsys.readouterr().out
    assert f"-> {manager.project_dir('P1') / 'uploads' / 'helix.pdb'}" in out
    assert "import: cryoplug ai call create_job project=P1 type=import_model 'params={\"source\": \"file\"" in out
    assert "type=import_sequence" in out
    with pytest.raises(SystemExit, match="Not found"):
        cli_main(["upload", "P1", str(tmp_path / "nope.pdb")])


# ------------------------------------------------------------------ skills
def test_skills_follow_the_agent_skills_format():
    skills = sk.all_skills()
    assert skills[0].name == "cryoplug" and len(skills) >= 6
    for skill in skills:
        assert sk.problems(skill) == [], skill.name
        assert len(skill.read().splitlines()) < 500, f"{skill.name}: keep SKILL.md under 500 lines"


def test_skills_mention_only_real_job_types_and_tools():
    from cryoplug.jobs import REGISTRY, all_job_types
    from cryoplug.workflows import WORKFLOWS
    all_job_types()
    known = set(REGISTRY) | set(toolmod.TOOLS) | {w["id"] for w in WORKFLOWS}
    pattern = re.compile(r"`([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`")
    for skill in sk.all_skills():
        for file in skill.files():
            for name in pattern.findall(skill.read(file)):
                if name.split("_")[0] in ("import", "phenix", "relion", "cryodrgn", "modelangelo", "denovo", "heterogeneity"):
                    assert name in known, f"{skill.name}/{file}: unknown job type, tool or workflow `{name}`"


def test_job_index_is_current():
    path = sk.SKILLS_DIR / sk.CORE / sk.JOB_INDEX
    assert path.read_text() == sk.job_index(), "run: cryoplug ai skills --write-reference"


def test_ai_commands(tmp_path, capsys, monkeypatch):
    dest = tmp_path / "skills"
    cli_main(["ai", "skills", "--install", str(dest)])
    assert (dest / "cryoplug" / "SKILL.md").exists() and "Installed" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="Already installed"):
        cli_main(["ai", "skills", "--install", str(dest)])
    cli_main(["ai", "skills", "--install", str(dest), "--force", "cryoplug"])
    capsys.readouterr()
    cfg = tmp_path / "c.toml"
    cfg.write_text(f'[server]\nport = 40123\ndata_dir = "{tmp_path / "data"}"\n')
    cli_main(["-c", str(cfg), "ai", "setup"])
    out = capsys.readouterr().out
    assert "claude mcp add cryoplug -- " in out and '"mcpServers"' in out and "http://127.0.0.1:40123/mcp" in out
    monkeypatch.delenv("CRYOPLUG_URL", raising=False)
    monkeypatch.delenv("CRYOPLUG_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        cli_main(["-c", str(cfg), "ai", "check"])
    assert "Cannot reach CryoPlug at http://127.0.0.1:40123" in capsys.readouterr().out
