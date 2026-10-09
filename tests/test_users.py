"""User accounts (as in CryoSPARC): login, sessions, roles, project ownership and sharing, each user's folders."""
from __future__ import annotations

import base64
import hashlib
import os
import time

import pytest
from fastapi.testclient import TestClient

from cryoplug import cli
from cryoplug.server.app import create_app
from cryoplug.users import LoginThrottle, Users, hash_password, path_allowed, verify_password

LOCAL = "http://localhost:39500"
PASSWORD = "correct horse 42"


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch):
    """Failed logins wait 1 s (password guessing): not in the tests."""
    async def instant(_seconds):
        return None
    monkeypatch.setattr("cryoplug.server.auth.asyncio.sleep", instant)


def make_client(manager, base_url=LOCAL) -> TestClient:
    return TestClient(create_app(manager.config, start_scheduler=False, manager=manager), base_url=base_url)


def login(client: TestClient, username: str, password: str = PASSWORD, expect: int = 303):
    r = client.post("/login", data={"username": username, "password": password, "next": "/"}, follow_redirects=False)
    assert r.status_code == expect, r.text
    return r


def setup_accounts(manager, tmp_path, synthetic):
    """An administrator (created from the interface), alice (may read the synthetic data) and bob."""
    admin = make_client(manager)
    admin.__enter__()
    r = admin.post("/api/users", json={"username": "Admin", "password": PASSWORD})
    assert r.status_code == 200, r.text
    alice_dir = tmp_path / "alice_projects"
    r = admin.post("/api/users", json={"username": "alice", "password": PASSWORD, "full_name": "Alice A.",
                                       "projects_dir": str(alice_dir), "allowed_paths": [str(synthetic["model"].parent)]})
    assert r.status_code == 200, r.text
    assert admin.post("/api/users", json={"username": "bob", "password": PASSWORD}).status_code == 200
    return admin, alice_dir


# ------------------------------------------------------------------ building blocks
def test_password_hashing():
    stored = hash_password("épicéa-42")
    assert stored.startswith("scrypt$") and "épicéa" not in stored
    assert verify_password("épicéa-42", stored)
    assert not verify_password("épicéa-43", stored)
    assert hash_password("épicéa-42") != stored  # salted
    assert not verify_password("x", "garbage") and not verify_password("x", "scrypt$1$2")
    salt = os.urandom(16)
    legacy = f"pbkdf2_sha256$1000${base64.b64encode(salt).decode()}${base64.b64encode(hashlib.pbkdf2_hmac('sha256', b'pw', salt, 1000)).decode()}"
    assert verify_password("pw", legacy) and not verify_password("pw2", legacy)


def test_path_allowed(tmp_path):
    mine = tmp_path / "alice"
    (mine / "data").mkdir(parents=True)
    other = tmp_path / "bob"
    other.mkdir()
    (mine / "escape").symlink_to(other)
    roots = [mine]
    assert path_allowed(mine / "data" / "map.mrc", roots) and path_allowed(mine, roots)
    assert not path_allowed(other / "map.mrc", roots)
    assert not path_allowed(f"{mine}/../bob/map.mrc", roots)
    assert not path_allowed(mine / "escape" / "map.mrc", roots)  # symbolic links are followed
    assert not path_allowed(f"{tmp_path}/alice-old/x", roots)  # a prefix is not a parent
    assert path_allowed(f"{mine}/data/frame_*.mrc", roots) and not path_allowed(f"{tmp_path}/*/x.mrc", roots)
    assert not path_allowed(f"{mine}/d*/../../bob/*.mrc", roots)  # '..' after a wildcard would escape when expanded
    assert not path_allowed("relative/map.mrc", roots)
    assert path_allowed("/anything", None) and path_allowed("/anything", ["/"]) and path_allowed("", roots)


def test_login_throttle():
    t = LoginThrottle(per_user=3, per_ip=5, window=60, lockout=30)
    assert t.begin("1.2.3.4", "alice") == 0
    t.success("1.2.3.4", "alice")  # a right password does not count
    for _ in range(2):
        assert t.begin("1.2.3.4", "alice") == 0
    assert t.wait("1.2.3.4", "alice") == 0
    assert t.begin("1.2.3.4", "alice") == 0  # third failure: blocked from now on
    assert t.wait("1.2.3.4", "alice") > 25 and t.begin("1.2.3.4", "alice") > 25
    assert t.wait("5.6.7.8", "alice") == 0  # nobody can lock a colleague out from elsewhere
    t.begin("1.2.3.4", "bob")
    t.begin("1.2.3.4", "carol")
    assert t.wait("1.2.3.4", "dave") > 25  # too many failures from one address, whatever the name
    # Behind an SSH tunnel everyone is 127.0.0.1: only the per-user limit applies there.
    for i in range(30):
        t.begin("127.0.0.1", f"user{i}")
    assert t.wait("127.0.0.1", "erin") == 0


# ------------------------------------------------------------------ accounts in the server
def test_first_administrator_from_the_interface(manager):
    with make_client(manager) as client:
        before = client.post("/api/projects", json={"title": "Before accounts"}).json()
        assert client.get("/api/info").json()["auth"] is None
        r = client.post("/api/users", json={"username": "boss", "password": PASSWORD, "role": "user"})
        assert r.status_code == 400 and "first account must be an administrator" in r.json()["detail"]
        r = client.post("/api/users", json={"username": "boss", "password": "short"})
        assert r.status_code == 400 and "at least 8 characters" in r.json()["detail"]
        r = client.post("/api/users", json={"username": "boss", "password": PASSWORD})
        assert r.status_code == 200 and r.json()["first"] is True and r.json()["user"]["role"] == "admin"
        # The person who turned accounts on stays logged in, as that administrator, and owns the old projects.
        me = client.get("/api/me").json()
        assert me["username"] == "boss" and me["role"] == "admin" and "password_hash" not in me
        info = client.get("/api/info").json()
        assert info["auth"] == "users" and info["user"]["username"] == "boss"
        assert client.get(f"/api/projects/{before['uid']}").json()["owner"] == "boss"

    with make_client(manager) as other:  # now everybody logs in, on localhost too
        refused = other.get("/api/info")
        assert refused.status_code == 401 and refused.headers["x-frame-options"] == "SAMEORIGIN"
        assert refused.headers["x-content-type-options"] == "nosniff"
        page = other.get("/login")
        assert 'name="username"' in page.text and 'name="password"' in page.text
        login(other, "boss", "wrong password", expect=401)
        login(other, "BOSS")  # user names are not case-sensitive
        assert other.get("/api/me").json()["username"] == "boss"
        other.post("/logout")
        assert other.get("/api/info").status_code == 401


def test_projects_and_folders_per_user(manager, synthetic, tmp_path):
    admin, alice_dir = setup_accounts(manager, tmp_path, synthetic)
    with make_client(manager) as alice, make_client(manager) as bob, make_client(manager) as carol:
        admin.post("/api/users", json={"username": "carol", "password": PASSWORD})
        login(alice, "alice")
        login(bob, "bob")
        login(carol, "carol")

        info = alice.get("/api/info").json()
        assert info["projects_root"] == str(alice_dir) and info["config_path"] is None
        assert info["browse_roots"] == [str(alice_dir), str(synthetic["model"].parent)]

        # Projects go to the user's projects folder, and nowhere outside their folders.
        p = alice.post("/api/projects", json={"title": "Alice"}).json()
        assert p["owner"] == "alice" and p["dir"].startswith(str(alice_dir))
        r = alice.post("/api/projects", json={"title": "Elsewhere", "parent": str(tmp_path / "elsewhere")})
        assert r.status_code == 400 and "outside your folders" in r.json()["detail"]
        default_dir = bob.post("/api/projects", json={"title": "Bob"}).json()["dir"]
        assert default_dir.startswith(str(manager.config.projects_root / "bob"))

        # Each user sees their own projects; administrators see everything.
        assert [x["uid"] for x in alice.get("/api/projects").json()] == [p["uid"]]
        assert {x["uid"] for x in admin.get("/api/projects").json()} >= {p["uid"]}
        for url in (f"/api/projects/{p['uid']}", f"/api/projects/{p['uid']}/jobs"):
            assert bob.get(url).status_code == 404, url
        assert bob.post(f"/api/projects/{p['uid']}/jobs", json={"type": "map_fsc"}).status_code == 404

        # Files and folders given to jobs must be in the user's folders.
        outside = alice.post(f"/api/projects/{p['uid']}/jobs", json={"type": "import_model", "params": {"source": "file", "path": "/etc/hosts"}})
        assert outside.status_code == 400 and "outside your folders" in outside.json()["detail"]
        job = alice.post(f"/api/projects/{p['uid']}/jobs", json={"type": "import_model", "params": {"source": "file", "path": str(synthetic["model"])}})
        assert job.status_code == 200 and job.json()["created_by"] == "alice"
        r = alice.patch(f"/api/projects/{p['uid']}/jobs/{job.json()['uid']}", json={"params": {"path": "/etc/hosts"}})
        assert r.status_code == 400
        r = alice.post(f"/api/projects/{p['uid']}/jobs", json={"type": "map_tools", "params": {"output_name": "../../escape"}})
        assert r.status_code == 400 and "simple file name" in r.json()["detail"]

        # The file browser stays in the user's folders.
        assert alice.get("/api/fs", params={"path": str(synthetic["model"].parent)}).status_code == 200
        assert alice.get("/api/fs", params={"path": "/etc"}).status_code == 403
        assert alice.get("/api/fs").json()["path"] in (str(alice_dir.resolve()), str(synthetic["model"].parent.resolve()))
        assert admin.get("/api/fs", params={"path": "/tmp"}).status_code == 200

        # Administration is for administrators.
        for method, url in (("get", "/api/users"), ("get", "/api/settings"), ("post", "/api/tools/check"),
                            ("patch", "/api/users/bob"), ("delete", "/api/users/bob")):
            kwargs = {"json": {}} if method in ("post", "patch") else {}
            assert getattr(alice, method)(url, **kwargs).status_code == 403, url
        assert alice.patch("/api/me", json={"role": "admin"}).status_code == 403
        assert alice.patch("/api/me", json={"full_name": "Alice Martin"}).json()["full_name"] == "Alice Martin"

        # Sharing: members work in the project; only the owner (or an administrator) manages it.
        assert bob.patch(f"/api/projects/{p['uid']}", json={"members": ["bob"]}).status_code == 404
        r = alice.patch(f"/api/projects/{p['uid']}", json={"members": ["bob", "nobody"]})
        assert r.status_code == 400
        assert alice.patch(f"/api/projects/{p['uid']}", json={"members": ["bob"]}).json()["members"] == ["bob"]
        assert bob.get(f"/api/projects/{p['uid']}/jobs").status_code == 200
        assert bob.patch(f"/api/projects/{p['uid']}", json={"title": "Mine now"}).status_code == 403
        assert bob.delete(f"/api/projects/{p['uid']}").status_code == 403
        assert alice.patch(f"/api/projects/{p['uid']}", json={"owner": "bob"}).status_code == 403
        assert [u["username"] for u in bob.get("/api/people").json()] == ["admin", "alice", "bob", "carol"]

        # The queue shows other people's jobs without their project details.
        queued = bob.post(f"/api/projects/{p['uid']}/jobs", json={"type": "import_model", "queue": True,
                                                                "params": {"source": "file", "path": str(synthetic["model"])}})
        assert queued.status_code == 400  # bob may not read the synthetic data folder, even in alice's project
        assert alice.post(f"/api/projects/{p['uid']}/jobs/{job.json()['uid']}/queue").status_code == 200
        seen = {j.get("hidden", False): j for j in carol.get("/api/queue").json()["jobs"]}
        assert True in seen and seen[True]["owner"] == "alice" and seen[True]["project_uid"] == "" and seen[True]["message"] == ""
        assert any(j["uid"] == job.json()["uid"] for j in alice.get("/api/queue").json()["jobs"])
        assert carol.post(f"/api/projects/{p['uid']}/jobs/{job.json()['uid']}/kill").status_code == 404

        # Administrators can give a project to someone else.
        assert admin.patch(f"/api/projects/{p['uid']}", json={"owner": "bob"}).json()["owner"] == "bob"
        assert admin.get(f"/api/projects/{p['uid']}").json()["members"] == []
    admin.__exit__(None, None, None)


def test_password_changes_and_sessions(manager, synthetic, tmp_path):
    admin, _ = setup_accounts(manager, tmp_path, synthetic)
    with make_client(manager) as dave, make_client(manager) as laptop, make_client(manager) as desktop:
        r = admin.post("/api/users", json={"username": "dave", "generate": True, "must_change_password": True})
        temporary = r.json()["password"]
        assert len(temporary) >= 12 and r.json()["user"]["must_change_password"] is True

        # A temporary password must be replaced before anything else.
        login(dave, "dave", temporary)
        assert dave.get("/api/info").json()["user"]["must_change_password"] is True
        r = dave.get("/api/projects")
        assert r.status_code == 403 and r.json()["code"] == "password_change"
        assert dave.post("/api/me/password", json={"current": "nope", "new": PASSWORD}).status_code == 400
        assert dave.post("/api/me/password", json={"current": temporary, "new": temporary}).status_code == 400
        assert dave.post("/api/me/password", json={"current": temporary, "new": PASSWORD}).status_code == 200
        assert dave.get("/api/projects").status_code == 200
        # A new user can browse before having a project: the projects folder is made then.
        r = dave.get("/api/fs")
        assert r.status_code == 200 and r.json()["path"].endswith("dave")

        # Changing one's password logs the other browsers out.
        login(laptop, "alice")
        login(desktop, "alice")
        assert len(laptop.get("/api/me/sessions").json()) == 2
        assert laptop.post("/api/me/password", json={"current": PASSWORD, "new": "another secret 7"}).status_code == 200
        assert laptop.get("/api/me").status_code == 200 and desktop.get("/api/me").status_code == 401
        login(desktop, "alice", "another secret 7")
        assert laptop.post("/api/me/sessions/end-others").status_code == 200
        assert desktop.get("/api/me").status_code == 401 and laptop.get("/api/me").status_code == 200

        # An administrator resets a password (generated, shown once): the user is logged out everywhere.
        r = admin.post("/api/users/alice/password", json={})
        assert r.status_code == 200 and r.json()["password"]
        assert laptop.get("/api/me").status_code == 401
        login(laptop, "alice", r.json()["password"])
        assert laptop.get("/api/me").json()["must_change_password"] is True

        # Expired sessions are refused.
        manager.db._execute("UPDATE sessions SET expires_at = ?", (time.time() - 1,))
        assert dave.get("/api/me").status_code == 401
    admin.__exit__(None, None, None)


def test_disable_delete_and_last_administrator(manager, synthetic, tmp_path):
    admin, _ = setup_accounts(manager, tmp_path, synthetic)
    with make_client(manager) as bob:
        login(bob, "bob")
        bob_project = bob.post("/api/projects", json={"title": "Bob's"}).json()

        assert admin.patch("/api/users/bob", json={"disabled": True}).json()["disabled"] is True
        assert bob.get("/api/me").status_code == 401  # logged out at once
        login(bob, "bob", expect=401)
        admin.patch("/api/users/bob", json={"disabled": False, "role": "admin"})
        login(bob, "bob")
        assert bob.get("/api/users").status_code == 200  # now an administrator
        admin.patch("/api/users/bob", json={"role": "user"})

        # CryoPlug always keeps an active administrator.
        for r in (admin.delete("/api/users/admin"), admin.patch("/api/users/admin", json={"role": "user"}),
                  admin.patch("/api/users/admin", json={"disabled": True})):
            assert r.status_code == 400 and "at least one active administrator" in r.json()["detail"]
        assert admin.patch("/api/users/admin", json={"username": "root"}).status_code == 400

        # Deleting an account: its projects go to someone else (or to nobody: administrators only).
        users = {u["username"]: u for u in admin.get("/api/users").json()}
        assert users["bob"]["num_projects"] == 1 and "password_hash" not in users["bob"]
        assert admin.delete("/api/users/bob", params={"transfer_to": "alice"}).status_code == 200
        assert admin.get(f"/api/projects/{bob_project['uid']}").json()["owner"] == "alice"
        assert bob.get("/api/me").status_code == 401
        assert {u["username"] for u in admin.get("/api/users").json()} == {"admin", "alice"}
    admin.__exit__(None, None, None)


def test_access_settings(manager, synthetic, tmp_path):
    admin, _ = setup_accounts(manager, tmp_path, synthetic)
    s = admin.get("/api/settings").json()
    assert s["values"] == {"session_days": 14, "min_password_length": 8} and s["mode"] == "users" and s["num_users"] == 3
    assert admin.patch("/api/settings", json={"session_days": 0}).status_code == 400
    assert admin.patch("/api/settings", json={"bogus": 1}).status_code == 400
    assert admin.patch("/api/settings", json={"session_days": 3, "min_password_length": 12}).json()["values"]["session_days"] == 3
    r = admin.post("/api/users", json={"username": "erin", "password": PASSWORD[:10]})
    assert r.status_code == 400 and "12 characters" in r.json()["detail"]
    admin.patch("/api/settings", json={"min_password_length": 20})
    r = admin.post("/api/users/bob/password", json={})  # generated passwords follow the minimum
    assert r.status_code == 200 and len(r.json()["password"]) >= 20
    admin.patch("/api/settings", json={"min_password_length": 12})
    with make_client(manager) as erin:
        admin.post("/api/users", json={"username": "erin", "password": "a long enough one"})
        r = login(erin, "erin", "a long enough one")
        assert "max-age=259200" in r.headers["set-cookie"].lower()  # 3 days
    admin.__exit__(None, None, None)


def test_login_throttling(manager, synthetic, tmp_path):
    admin, _ = setup_accounts(manager, tmp_path, synthetic)
    with make_client(manager) as client:
        for _ in range(5):
            login(client, "alice", "guess", expect=401)
        r = login(client, "alice", PASSWORD, expect=429)
        assert "Too many failed attempts" in r.text
        login(client, "bob")  # another account from the same place still works
        basic = base64.b64encode(f"alice:{PASSWORD}".encode()).decode()
        client.cookies.clear()
        assert client.get("/api/me", headers={"authorization": f"Basic {basic}"}).status_code == 401
    admin.__exit__(None, None, None)


def test_scripts_and_token_with_accounts(manager, synthetic, tmp_path):
    manager.config.host = "0.0.0.0"
    lan = "http://192.168.1.42:39500"
    with make_client(manager, lan) as client:
        token = (manager.config.data_dir / "access_token").read_text().strip()
        bearer = {"authorization": f"Bearer {token}"}
        assert client.get("/api/info", headers=bearer).json()["auth"] == "token"
        # The token holder (the server's owner) turns accounts on...
        assert client.post("/api/users", json={"username": "admin", "password": PASSWORD}, headers=bearer).status_code == 200
        client.cookies.clear()
        # ...after which the token no longer opens CryoPlug.
        assert client.get("/api/info", headers=bearer).status_code == 401
        r = client.get(f"/?token={token}", headers={"accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        basic = {"authorization": "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()}
        assert client.get("/api/me", headers=basic).json()["username"] == "admin"
        assert client.get("/api/me", headers=basic).status_code == 200  # remembered: no new password hash
        wrong = base64.b64encode(b"admin:nope").decode()
        assert client.get("/api/me", headers={"authorization": f"Basic {wrong}"}).status_code == 401
        Users(manager.db, manager.config).set_password("admin", "the new one 1234")
        assert client.get("/api/me", headers=basic).status_code == 401  # the remembered password no longer works
        # A page from another site still cannot act with the user's session.
        login(client, "admin", "the new one 1234")
        assert client.post("/api/projects", json={"title": "x"}, headers={"origin": "http://evil.example"}).status_code == 403


def test_cli_user_commands(manager, tmp_path, capsys):
    cfg = tmp_path / "config.toml"
    cfg.write_text(f'[server]\ndata_dir = "{manager.config.data_dir}"\nprojects_root = "{manager.config.projects_root}"\n')
    run = lambda *args: cli.main(["-c", str(cfg), "user", *args])  # noqa: E731
    run("list")
    assert "No accounts" in capsys.readouterr().out
    run("add", "root", "--password", PASSWORD)
    out = capsys.readouterr().out
    assert "Created admin account 'root'" in out and "Accounts are now required" in out
    run("add", "frank", "--generate", "--allow", str(tmp_path), "--projects-dir", str(tmp_path / "frank"))
    generated = capsys.readouterr().out.rsplit(": ", 1)[1].strip()
    users = Users(manager.db, manager.config)
    assert users.authenticate("frank", generated)["allowed_paths"] == [str(tmp_path)]
    run("set", "frank", "--role", "admin", "--allow", "/data/a", "--allow", "/data/b")
    assert users.get("frank")["role"] == "admin" and users.get("frank")["allowed_paths"] == ["/data/a", "/data/b"]
    run("passwd", "frank", "--password", "brand new password")
    run("disable", "frank")
    assert users.authenticate("frank", "brand new password") is None
    run("enable", "frank")
    assert users.authenticate("frank", "brand new password") is not None
    run("delete", "frank")
    with pytest.raises(SystemExit, match="at least one active administrator"):
        run("delete", "root")
    run("list")
    assert "root" in capsys.readouterr().out


def test_code_running_jobs_are_for_administrators(manager, synthetic, tmp_path):
    admin, _ = setup_accounts(manager, tmp_path, synthetic)
    with make_client(manager) as alice:
        login(alice, "alice")
        p = alice.post("/api/projects", json={"title": "Alice"}).json()["uid"]
        jobs = f"/api/projects/{p}/jobs"
        r = alice.post(jobs, json={"type": "custom_command", "params": {"command": "cat /etc/shadow"}})
        assert r.status_code == 400 and "only administrators" in r.json()["detail"]
        r = alice.post(jobs, json={"type": "isolde_session", "params": {"extra_commands": "runscript /tmp/evil.py"}})
        assert r.status_code == 400 and "only administrators" in r.json()["detail"]
        assert alice.post(jobs, json={"type": "isolde_session"}).status_code == 200
        # Extra program arguments could read or write anywhere (-m/home/bob/model.pdb): administrators only.
        r = alice.post(jobs, json={"type": "servalcat_refine", "params": {"extra_args": "-m/home/bob/model.pdb"}})
        assert r.status_code == 400 and "only administrators" in r.json()["detail"]
        types = {t["name"]: t for t in alice.get("/api/jobtypes").json()}
        assert types["custom_command"]["admin_only"] is True
        assert next(x for x in types["isolde_session"]["params"] if x["name"] == "extra_commands")["admin_only"] is True
        assert next(x for x in types["servalcat_refine"]["params"] if x["name"] == "extra_args")["admin_only"] is True

        # What an administrator set stays usable by the members: they edit the rest, clone, queue.
        refine = admin.post(jobs, json={"type": "phenix_real_space_refine", "params": {"extra_args": "nproc=8"}})
        assert refine.status_code == 200
        url = f"{jobs}/{refine.json()['uid']}"
        assert alice.patch(url, json={"params": {"macro_cycles": 3}}).status_code == 200
        assert alice.patch(url, json={"params": {"extra_args": "nproc=16"}}).status_code == 400
        assert alice.post(f"{url}/clone").status_code == 200

        # An administrator may; a member may queue that job but not change it.
        admin.patch(f"/api/projects/{p}", json={"members": ["admin"]})
        custom = admin.post(jobs, json={"type": "custom_command", "params": {"command": "echo hi > out.txt"}})
        assert custom.status_code == 200
        r = alice.patch(f"{jobs}/{custom.json()['uid']}", json={"params": {"command": "id"}})
        assert r.status_code == 400 and "only administrators" in r.json()["detail"]
        assert alice.post(f"{jobs}/{custom.json()['uid']}/clone").status_code == 400
        assert alice.post(f"{jobs}/{custom.json()['uid']}/queue").status_code == 200
    admin.__exit__(None, None, None)


def test_project_gate_cannot_be_sidestepped(manager, synthetic, tmp_path):
    admin, _ = setup_accounts(manager, tmp_path, synthetic)
    secret = admin.post("/api/projects", json={"title": "Facility"}).json()["uid"]
    with make_client(manager) as bob:
        login(bob, "bob")
        for url in (f"/api/projects/{secret}", f"/api/projects/{secret}/", f"/api/projects//{secret}/jobs",
                    f"/api/projects/{secret}/jobs/../jobs", f"/api/projects/%50{secret[1:]}/jobs",
                    f"/api/projects/{secret}/file?path=cryoplug_project.json", f"/api/projects/{secret}/workflows/x"):
            r = bob.get(url, follow_redirects=True)
            assert r.status_code in (404, 405), (url, r.status_code)
            assert "Facility" not in r.text
        assert bob.get("/api/projects").json() == []
    admin.__exit__(None, None, None)
