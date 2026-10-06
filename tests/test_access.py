"""Access control: network exposure, login page, access token, password, CSRF / DNS-rebinding checks."""
from __future__ import annotations

import base64
import stat

import pytest
from fastapi.testclient import TestClient

from cryoplug.server.app import create_app
from cryoplug.server.auth import Auth, access_urls, is_loopback

LAN = "http://192.168.1.42:39500"


def client_for(manager, base_url=LAN) -> TestClient:
    return TestClient(create_app(manager.config, start_scheduler=False, manager=manager), base_url=base_url)


def test_network_token_login(manager):
    manager.config.host = "0.0.0.0"
    with client_for(manager) as client:
        token_file = manager.config.data_dir / "access_token"
        token = token_file.read_text().strip()
        assert len(token) >= 30 and stat.S_IMODE(token_file.stat().st_mode) == 0o600

        # Nothing is reachable without logging in.
        assert client.get("/api/info").status_code == 401
        r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login?next=%2F"
        r = client.get("/viewer.html?path=a.mrc&token=bad", headers={"accept": "text/html"}, follow_redirects=False)
        assert r.headers["location"] == "/login?next=%2Fviewer.html%3Fpath%3Da.mrc"  # bad token dropped, rest kept
        page = client.get("/login?next=/x%22%3E")
        assert page.status_code == 200 and "Access token" in page.text and 'value="/x&quot;&gt;"' in page.text
        assert client.get("/css/app.css").status_code == 200

        r = client.post("/login", data={"password": "wrong", "next": "/", "hash": "#/p/P1"}, follow_redirects=False)
        assert r.status_code == 401 and "Wrong access token" in r.text
        assert 'name="hash" id="hash" value="#/p/P1"' in r.text  # the page to return to survives a typo
        assert client.get("/api/info").status_code == 401

        r = client.post("/login", data={"password": token, "next": "/", "hash": "#/p/P1"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/#/p/P1"
        assert "httponly" in r.headers["set-cookie"].lower() and "samesite=lax" in r.headers["set-cookie"].lower()
        info = client.get("/api/info").json()
        assert info["auth"] == "token" and info["listen"]["network"] is True
        assert client.get("/").status_code == 200

        # Changes must come from the CryoPlug page itself.
        assert client.post("/api/projects", json={"title": "x"}, headers={"origin": "http://evil.example"}).status_code == 403
        assert client.post("/api/projects", json={"title": "ok"}, headers={"origin": LAN}).status_code == 200

        client.post("/logout", headers={"origin": LAN})
        assert client.get("/api/info").status_code == 401

        # Links printed at start-up log in directly; other query parameters are kept.
        r = client.get(f"/?token={token}&a=1", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/?a=1"
        assert client.get("/api/info").status_code == 200
        client.cookies.clear()

        # Scripts: bearer token.
        assert client.get("/api/info", headers={"authorization": f"Bearer {token}"}).status_code == 200
        assert client.get("/api/info", headers={"authorization": "Bearer nope"}).status_code == 401

        # No open redirect after login.
        r = client.post("/login", data={"password": token, "next": "//evil.example/"}, follow_redirects=False)
        assert r.headers["location"] == "/"


def test_session_cookie_is_signed(manager):
    manager.config.host = "0.0.0.0"
    with client_for(manager) as client:
        auth: Auth = client.app.state.auth
        name = auth.cookie_name
        for forged in ("9999999999.deadbeef", "abc", auth.new_session(lifetime=-10)):
            client.cookies.set(name, forged)
            assert client.get("/api/info").status_code == 401, forged
        client.cookies.set(name, auth.new_session())
        assert client.get("/api/info").status_code == 200


def test_password_login(manager):
    manager.config.password = "épicéa-42"
    with client_for(manager, "http://localhost:39500") as client:
        assert not (manager.config.data_dir / "access_token").exists()
        assert client.get("/api/info").status_code == 401  # a password always requires the login
        assert "Password" in client.get("/login").text
        r = client.post("/login", data={"password": "épicéa-42"}, follow_redirects=False)
        assert r.status_code == 303
        assert client.get("/api/info").json()["auth"] == "password"
        old_cookie = client.cookies.get(client.app.state.auth.cookie_name)

    basic = base64.b64encode("me:épicéa-42".encode()).decode()
    with client_for(manager, "http://localhost:39500") as client:
        assert client.get("/api/info", headers={"authorization": f"Basic {basic}"}).status_code == 200

    # Changing the password logs every browser out.
    manager.config.password = "new-password"
    with client_for(manager, "http://localhost:39500") as client:
        client.cookies.set(client.app.state.auth.cookie_name, old_cookie)
        assert client.get("/api/info").status_code == 401


def test_localhost_without_login(manager):
    with client_for(manager, "http://localhost:39500") as client:
        info = client.get("/api/info").json()
        assert info["auth"] is None and info["listen"]["network"] is False
        assert client.get("/login", follow_redirects=False).status_code == 303
        # DNS rebinding: another host name pointing at 127.0.0.1 is refused.
        assert client.get("/api/info", headers={"host": "evil.example:39500"}).status_code == 403
        assert client.get("/api/info", headers={"host": "127.0.0.1:39500"}).status_code == 200
        # CSRF: a page from another site cannot create jobs.
        assert client.post("/api/projects", json={"title": "x"}, headers={"origin": "http://evil.example"}).status_code == 403


@pytest.mark.parametrize("host, expected", [
    ("127.0.0.1", True), ("localhost", True), ("::1", True), ("[::1]", True), ("app.localhost", True),
    ("0.0.0.0", False), ("192.168.1.42", False), ("cryo-ws1", False),
])
def test_is_loopback(host, expected):
    assert is_loopback(host) is expected


def test_access_urls(manager):
    cfg = manager.config
    assert access_urls(cfg) == ["http://localhost:39500/"]
    cfg.host, cfg.port = "0.0.0.0", 40000
    urls = access_urls(cfg, "tok")
    assert urls and all(u.startswith("http://") and u.endswith(":40000/?token=tok") for u in urls)
    assert not any("127.0.0.1" in u or "0.0.0.0" in u for u in urls)
    cfg.host, cfg.ssl_certfile = "cryo-ws1.lab.org", "/etc/cert.pem"
    assert access_urls(cfg) == ["https://cryo-ws1.lab.org:40000/"]
