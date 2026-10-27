"""Chat assistants: the OpenAPI tool server (/openapi, as Open WebUI's tool servers use it) and logging in with the
session of another web application ([external_login], as with Open WebUI), against a fake Open WebUI."""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from conftest import run_until_done
from fastapi.testclient import TestClient

from cryoplug.agent import openapi as oa
from cryoplug.agent import tools as toolmod
from cryoplug.agent.tools_fr import FR
from cryoplug.config import ExternalLoginConfig, OpenApiConfig, load_config
from cryoplug.server.app import create_app

BASE = "http://localhost:39500"


# ------------------------------------------------------------------ what Open WebUI does with a tool server
def owui_tools(spec: dict) -> list[dict]:
    """The function specs Open WebUI makes of an OpenAPI tool server (convert_openapi_to_tool_payload)."""
    out = []
    for methods in spec["paths"].values():
        for op in methods.values():
            if not op.get("operationId"):
                continue
            tool = {"name": op["operationId"], "description": op.get("description", op.get("summary", "")),
                    "parameters": {"type": "object", "properties": {}, "required": []}}
            for param in op.get("parameters", []):
                tool["parameters"]["properties"][param["name"]] = {"type": param["schema"].get("type", "string")}
            schema = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if schema and schema.get("properties"):
                tool["parameters"]["properties"].update(schema["properties"])
                tool["parameters"]["required"] = schema.get("required", [])
            out.append(tool)
    return out


def owui_call(client: TestClient, url: str, spec: dict, name: str, params: dict, headers: dict | None = None):
    """What Open WebUI's execute_tool_server sends: the operation's route appended to the server URL, the arguments
    as JSON body (POST)."""
    route = next(path for path, methods in spec["paths"].items() for op in methods.values() if op.get("operationId") == name)
    return client.post(url.rstrip("/") + route, json=params, headers=headers or {})


def _accounts(app):
    users = app.state.users
    users.create("admin", "admin long password", role="admin", email="boss@lab.example")
    alice = users.create("alice", "correct horse battery", role="user", full_name="Alice", email="Alice@Lab.example")
    return users, alice


# ------------------------------------------------------------------ OpenAPI tool server
def test_openapi_tool_server(manager, synthetic):
    manager.config.public_url = "http://cryo.lab.example:39500"
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    users, alice = _accounts(app)
    token, _ = users.new_token(alice, "Open WebUI")
    bearer = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url=BASE) as client:
        # The specification is public (Open WebUI reads it without credentials in "session" mode), the tools are not
        r = client.get("/openapi/openapi.json")
        assert r.status_code == 200
        spec = r.json()
        assert spec["openapi"].startswith("3.1") and spec["servers"] == [{"url": "/openapi"}]
        tools = owui_tools(spec)
        names = {t["name"] for t in tools}
        read = toolmod.select_tools("read", local_files=False)
        assert names == {f"cryoplug_{n}" for n in read}
        assert "cryoplug_create_job" not in names and "cryoplug_upload_file" not in names
        assert all(t["description"] for t in tools)
        assert "Read only" in spec["info"]["description"] and "http://cryo.lab.example:39500" in spec["info"]["description"]
        skills = next(t for t in tools if t["name"] == "cryoplug_list_skills")
        assert "cryoplug_read_skill" in skills["description"]  # tools mentioned as the assistant knows them
        get_job = next(t for t in tools if t["name"] == "cryoplug_get_job")
        assert get_job["parameters"]["required"] == ["project", "job"]
        assert set(get_job["parameters"]["properties"]) == {"project", "job", "full_report", "all_params"}
        assert client.post("/openapi/list_projects", json={}).status_code == 401
        assert client.post("/openapi/list_projects", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401

        empty = owui_call(client, BASE + "/openapi", spec, "cryoplug_list_projects", {}, bearer)
        assert empty.status_code == 200 and empty.headers["content-type"].startswith("text/plain")
        assert "No project yet" in empty.text
        assert client.post("/api/projects", json={"title": "Apoferritin"}, headers=bearer).status_code == 200
        listed = owui_call(client, BASE + "/openapi", spec, "cryoplug_list_projects", {}, bearer).text
        assert "P1" in listed and "Apoferritin" in listed and "owner alice" in listed
        project = owui_call(client, BASE + "/openapi", spec, "cryoplug_get_project", {"project": "apoferritin"}, bearer)
        assert project.status_code == 200 and "Empty project" in project.text
        assert project.text.rstrip().endswith("Page: http://cryo.lab.example:39500/#/p/P1")

        # Errors say what to correct, with the tools named as the assistant knows them
        missing = owui_call(client, BASE + "/openapi", spec, "cryoplug_get_project", {"project": "P9"}, bearer)
        assert missing.status_code == 400 and "cryoplug_list_projects" in missing.json()["error"]
        assert client.post("/openapi/create_project", json={"title": "x"}, headers=bearer).status_code == 404
        assert manager.db.get_project("P2") is None
        bad = client.post("/openapi/get_project", content=b"{oops", headers={**bearer, "Content-Type": "application/json"})
        assert bad.status_code == 400 and "JSON object" in bad.json()["error"]
        assert client.post("/openapi/get_project", json=["P1"], headers=bearer).status_code == 400
        # Unknown arguments are reported, not fatal; a browser page of another site cannot call the tools
        extra = client.post("/openapi/list_projects", json={"colour": "red"}, headers=bearer)
        assert extra.status_code == 200 and "Ignored unknown argument: colour" in extra.text
        evil = client.post("/openapi/list_projects", json={}, headers={**bearer, "Origin": "http://evil.example"})
        assert evil.status_code == 403

        # A job with images: data URIs in a JSON answer (Open WebUI shows them and gives the model "[image]")
        p1 = manager.db.get_project("P1")
        job = manager.create_job("P1", "import_maps", params={"half_map_a": str(synthetic["half_a"]),
                                                             "half_map_b": str(synthetic["half_b"]),
                                                             "map_sharp": str(synthetic["map"]), "resolution": 3.0},
                                 created_by="alice")
        manager.queue_job("P1", job["uid"])
        run_until_done(manager, "P1", [job["uid"]])
        assert p1["owner"] == "alice"
        images = owui_call(client, BASE + "/openapi", spec, "cryoplug_get_job_images", {"project": "P1", "job": "J1"}, bearer)
        body = images.json()
        assert images.status_code == 200 and body["images"] and body["images"][0].startswith("data:image/png;base64,")
        assert base64.b64decode(body["images"][0].split(",", 1)[1])[:4] == b"\x89PNG"
        details = owui_call(client, BASE + "/openapi", spec, "cryoplug_get_job", {"project": "P1", "job": "J1"}, bearer).text
        assert "status completed" in details and details.rstrip().endswith("/#/p/P1/J1")
        # Another person's project does not exist for this account
        users.create("bob", "another long password", role="user")
        bob_token, _ = users.new_token(users.get("bob"), "assistant")
        hidden = client.post("/openapi/get_job", json={"project": "P1", "job": "J1"},
                             headers={"Authorization": f"Bearer {bob_token}"})
        assert hidden.status_code == 400 and "not found" in hidden.json()["error"]


def test_openapi_settings(manager):
    """Tool choice, French descriptions, answers cut for small contexts, turned off."""
    manager.config.openapi = OpenApiConfig(tools="all", language="fr", max_answer_chars=400)
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    users, alice = _accounts(app)
    token, _ = users.new_token(alice, "LabChat")
    bearer = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url=BASE) as client:
        spec = client.get("/openapi/openapi.json").json()
        ops = {op["operationId"]: op for methods in spec["paths"].values() for op in methods.values()}
        assert "cryoplug_create_job" in ops and "cryoplug_upload_file" not in ops  # never the agent computer's files
        assert "Lecture seule" not in spec["info"]["description"]
        assert ops["cryoplug_get_project"]["summary"] == "Projet et ses jobs"
        props = ops["cryoplug_get_project"]["requestBody"]["content"]["application/json"]["schema"]["properties"]
        assert props["project"]["description"].startswith("Identifiant du projet")
        assert "cryoplug_describe_job_type" in ops["cryoplug_create_job"]["description"]
        made = client.post("/openapi/create_project", json={"title": "Écrit par l'assistant"}, headers=bearer)
        assert made.status_code == 200 and manager.db.get_project("P1")["owner"] == "alice"
        long = client.post("/openapi/list_job_types", json={}, headers=bearer).text
        assert len(long) < 700 and "réponse coupée à" in long and "caractères sur" in long

    manager.config.openapi = OpenApiConfig(enabled=False)
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url=BASE) as client:
        assert client.get("/openapi/openapi.json").status_code == 401  # not public any more: nothing there
        assert client.get("/openapi/openapi.json", headers=bearer).status_code == 404


def test_french_descriptions_cover_every_tool():
    for name, tool in toolmod.TOOLS.items():
        assert name in FR, f"no French description for {name}"
        title, description, args = FR[name]
        assert title and 40 < len(description) < 400, name
        assert set(args) == set(tool.properties), f"{name}: arguments {set(tool.properties) ^ set(args)}"
    assert set(FR) == set(toolmod.TOOLS)


def test_renamer_and_clip():
    rename = oa.renamer(["get_job", "get_job_log", "map_info"])
    assert rename("get_job then get_job_log; J3/get_job.txt, map_info.") == \
        "cryoplug_get_job then cryoplug_get_job_log; J3/get_job.txt, cryoplug_map_info."
    assert rename("cryoplug_get_job") == "cryoplug_get_job"
    text = "\n".join(f"line {i}" for i in range(200))
    cut = oa.clip(text, 100)
    assert len(cut) < 300 and cut.startswith("line 0\n") and "answer cut at" in cut
    assert oa.clip(text, 0) == text and oa.clip("short", 100) == "short"


def test_assistant_configuration(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[server]\npublic_url = "http://cryo.example:39500/"\ntrusted_proxies = ["172.17.0.1"]\n'
                    '[openapi]\ntools = ["get_job", "list_projects"]\nlanguage = "FR"\n'
                    '[external_login]\nurl = "http://localhost:8080/api/v1/auths/"\ncookie = "token"\nname = "LabChat"\n')
    cfg = load_config(path)
    assert cfg.public_url == "http://cryo.example:39500" and cfg.trusted_proxies == ["172.17.0.1"]
    assert cfg.openapi.tools == "get_job,list_projects" and cfg.openapi.language == "fr"
    assert cfg.external_login.name == "LabChat" and cfg.external_login.roles == ["user", "admin"]
    assert cfg.external_login.match == "email" and cfg.external_login.user_field == "email"
    assert load_config(tmp_path / "missing.toml").external_login is None
    for bad in ('[openapi]\nlanguage = "de"\n', '[openapi]\ntools = "everything"\n', '[openapi]\ntool = "read"\n',
                '[external_login]\ncookie = "token"\n', '[external_login]\nurl = "ftp://x"\n',
                '[external_login]\nurl = "http://x/"\nmatch = "name"\n',
                '[external_login]\nurl = "http://x/"\nbearer = false\n',
                '[external_login]\nurl = "http://x/"\ncookie = "a b"\n', '[server]\npublic_url = "cryo:39500"\n'):
        path.write_text(bad)
        with pytest.raises(ValueError):
            load_config(path)


# ------------------------------------------------------------------ logging in with Open WebUI
SESSIONS = {
    "alice-session-token": {"id": "u1", "email": "alice@lab.example", "name": "Alice", "role": "user"},
    "alice-admin-token": {"id": "u1", "email": "alice@lab.example", "name": "Alice", "role": "admin"},
    "pending-session-token": {"id": "u2", "email": "carol@lab.example", "name": "Carol", "role": "pending"},
    "dave-session-token": {"id": "u3", "email": "dave.smith@lab.example", "name": "Dave Smith", "role": "user"},
}


class FakeOpenWebUI:
    """GET /api/v1/auths/: the account of the session (cookie "token" or Bearer), else 401 — as Open WebUI."""

    def __init__(self):
        self.calls: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                cookie = dict(c.strip().split("=", 1) for c in self.headers.get("Cookie", "").split(";") if "=" in c)
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else cookie.get("token", "")
                outer.calls.append({"path": self.path, "token": token, "via": "bearer" if auth else "cookie",
                                    "cookies": sorted(cookie)})
                if self.path == "/elsewhere":
                    self._send(200, {"email": "alice@lab.example", "role": "admin"})
                elif token == "redirect-session-token":
                    self.send_response(302)
                    self.send_header("Location", "/elsewhere")
                    self.end_headers()
                elif self.path == "/api/v1/auths/" and token in SESSIONS:
                    self._send(200, {**SESSIONS[token], "token": token, "token_type": "Bearer"})
                else:
                    self._send(401, {"detail": "Not authenticated"})

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/api/v1/auths/"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def owui():
    fake = FakeOpenWebUI()
    yield fake
    fake.close()


def test_external_login_with_open_webui(manager, owui):
    manager.config.external_login = ExternalLoginConfig(url=owui.url, name="LabChat", cookie="token",
                                                        login_url="http://lab-server:8081/auth")
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    users, _ = _accounts(app)
    with TestClient(app, base_url=BASE) as client:
        # Not logged in anywhere: the login page links to the application's
        page = client.get("/login").text
        assert 'href="http://lab-server:8081/auth"' in page and "Log in with LabChat" in page

        # Logged in to LabChat: the CryoPlug account with the same e-mail address (case aside), checked once a minute
        client.cookies.set("token", "alice-session-token")
        me = client.get("/api/me")
        assert me.status_code == 200 and me.json()["username"] == "alice" and me.json()["external"] == "LabChat"
        assert client.get("/api/info").json()["user"]["username"] == "alice"
        made = client.post("/api/projects", json={"title": "From LabChat"})
        assert made.status_code == 200 and made.json()["owner"] == "alice"
        assert len(owui.calls) == 1 and owui.calls[0]["cookies"] == ["token"]  # only its own cookie is forwarded
        assert client.post("/api/me/tokens", json={"label": "script"}).status_code == 200  # a login: full rights
        page = client.get("/login").text
        assert "Continue as alice" in page and "Logged in to LabChat as alice@lab.example" in page

        # An LabChat administrator is not a CryoPlug administrator because of it: the account decides
        client.cookies.set("token", "alice-admin-token")
        assert client.get("/api/me").json()["role"] == "user"
        assert client.get("/api/users").status_code == 403

        # Awaiting approval, or without a CryoPlug account: not logged in, and the login page says why
        client.cookies.set("token", "pending-session-token")
        assert client.get("/api/me").status_code == 401
        assert "its role is &#x27;pending&#x27; (accepted: user, admin)" in client.get("/login").text
        client.cookies.set("token", "dave-session-token")
        assert client.get("/api/me").status_code == 401
        assert "no CryoPlug account has this e-mail address" in client.get("/login").text
        client.cookies.set("token", "forged-session-token")
        assert client.get("/api/me").status_code == 401
        before = len(owui.calls)
        assert client.get("/api/me").status_code == 401 and len(owui.calls) == before  # refusals are remembered
        # A redirection is not followed (it would carry the session elsewhere)
        client.cookies.set("token", "redirect-session-token")
        assert client.get("/api/me").status_code == 401
        assert not any(c["path"] == "/elsewhere" for c in owui.calls)
        client.cookies.delete("token")

        # Assistant tools acting for the person (Open WebUI tool servers with "session" authentication)
        session = {"Authorization": "Bearer alice-session-token"}
        tools = client.post("/openapi/list_projects", json={}, headers=session)
        assert tools.status_code == 200 and "From LabChat" in tools.text
        assert client.post("/api/me/tokens", json={"label": "x"}, headers=session).status_code == 403  # as API tokens
        # CryoPlug's own API tokens are never sent to the application
        before = len(owui.calls)
        assert client.get("/api/me", headers={"Authorization": "Bearer cpt_not-a-real-token"}).status_code == 401
        assert len(owui.calls) == before

        # Logging out of CryoPlug: LabChat's session is not used in this browser until "Continue as"
        client.cookies.set("token", "alice-session-token")
        out = client.post("/logout")
        assert out.status_code == 200 and "cryoplug_external_off_" in out.headers.get("set-cookie", "")
        assert client.get("/api/me").status_code == 401
        page = client.get("/login").text
        assert "Continue as alice" in page
        back = client.post("/login", data={"external": "1", "next": "/#/projects"}, follow_redirects=False)
        assert back.status_code == 303 and back.headers["location"] == "/#/projects"
        assert client.get("/api/me").json()["username"] == "alice"
        # A disabled account is refused at once (accounts are read at every request)
        users.update("alice", disabled=True)
        assert client.get("/api/me").status_code == 401


def test_external_login_creates_accounts_and_needs_accounts(manager, owui):
    manager.config.external_login = ExternalLoginConfig(url=owui.url, name="LabChat", cookie="token", create_accounts=True)
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url=BASE) as client:
        client.cookies.set("token", "dave-session-token")
        # Without accounts (here on localhost, without login) the application's session is not looked at
        assert app.state.auth.mode is None
        assert client.get("/api/info").json()["user"]["builtin"] and owui.calls == []
        users, _ = _accounts(app)
        me = client.get("/api/me")
        assert me.status_code == 200 and me.json()["username"] == "dave.smith" and me.json()["role"] == "user"
        made = users.get("dave.smith")
        assert made["email"] == "dave.smith@lab.example" and made["full_name"] == "Dave Smith"
        client.cookies.set("token", "pending-session-token")
        assert client.get("/api/me").status_code == 401 and users.get("carol") is None  # never for pending accounts


def test_external_login_when_the_application_is_down(manager):
    manager.config.external_login = ExternalLoginConfig(url="http://127.0.0.1:9/api/v1/auths/", cookie="token", timeout=1)
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    _accounts(app)
    with TestClient(app, base_url=BASE) as client:
        client.cookies.set("token", "alice-session-token")
        assert client.get("/api/me").status_code == 401
        assert client.get("/api/me", auth=("alice", "correct horse battery")).json()["username"] == "alice"
