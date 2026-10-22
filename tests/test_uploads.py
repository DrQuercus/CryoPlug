"""Files uploaded from the user's computer: chunks, resume, duplicates, folders, safety, permissions, and the import
jobs that hard-link them."""
from __future__ import annotations

import hashlib
import os

import pytest
from conftest import run_until_done
from fastapi.testclient import TestClient

from cryoplug import uploads as upmod
from cryoplug.agent.client import file_fingerprint, folder_fingerprint, text_hash
from cryoplug.server.app import create_app
from cryoplug.uploads import OffsetMismatch, UploadError, Uploads, clean_folder, clean_name

BASE = "http://localhost:39500"


def send(client, puid, path, data, name=None, folder="", fingerprint="fp", chunk=1000, headers=None):
    """Upload ``data`` as the browser does: announce, then chunks from the offset the server gives."""
    r = client.post(f"/api/projects/{puid}/uploads", headers=headers,
                    json={"name": name or os.path.basename(path), "size": len(data), "folder": folder, "fingerprint": fingerprint})
    assert r.status_code == 200, r.text
    info = r.json()
    offset = info["offset"]
    while not info["done"]:
        r = client.put(f"/api/projects/{puid}/uploads", params={"rel": info["rel"], "offset": offset},
                       content=data[offset:offset + chunk], headers=headers)
        assert r.status_code == 200, r.text
        info = r.json()
        offset = info["offset"]
    return info


def test_clean_names():
    assert clean_name("C:\\Users\\marie\\half map A.mrc") == "half_map_A.mrc"
    assert clean_name("../../etc/passwd") == "passwd"
    assert clean_name(".hidden") == "hidden"
    with pytest.raises(UploadError, match="macOS"):  # would replace the map it describes
        clean_name("._J42_008_volume_map_half_A.mrc")
    assert clean_name("carte_é.mrc") == "carte__.mrc"
    with pytest.raises(UploadError):
        clean_name("...")
    assert clean_folder("CS-x/J245/") == "CS-x/J245"
    with pytest.raises(UploadError):
        clean_folder("a/../../b")


def test_fingerprints_match_the_web_interface(tmp_path):
    """The Python client (cryoplug upload, upload_file) identifies files and folders as upload.js does, so that
    either can resume the other's upload. Reference values computed with upload.js's textHash in Node."""
    assert text_hash("") == "bdcb81aee8d83"
    assert text_hash("J42/J42_008_volume_map_half_A.mrc:2097280") == "16037d75a20493"
    root = tmp_path / "J42"
    for rel, n in (("b map.mrc", 10), ("sub/é.mrc", 5), ("😀.mrc", 3), ("a.mrc", 7), ("\uff21.mrc", 1)):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x" * n)
    # listing sorted by UTF-16 code units, as JavaScript sorts: the emoji (surrogates) before U+FF21
    assert folder_fingerprint(root, sorted(p for p in root.rglob("*") if p.is_file())) == "e8908aeff6a31"
    one = root / "a.mrc"
    os.utime(one, (1_700_000_000.5, 1_700_000_000.5))  # File.lastModified: milliseconds
    assert file_fingerprint(one) == f"a.mrc:7:1700000000500:{hashlib.sha256(b'x' * 7).hexdigest()[:24]}"
    big = root / "big.map"  # 1.5 MB: the first and the last MB overlap, as File.slice gives them
    data = os.urandom(3 << 19)
    big.write_bytes(data)
    assert file_fingerprint(big).endswith(":" + hashlib.sha256(data[: 1 << 20] + data[-(1 << 20):]).hexdigest()[:24])
    empty = root / "empty.txt"
    empty.write_bytes(b"")
    assert file_fingerprint(empty).count(":") == 2  # no hash of an empty file, as in the browser


def test_chunks_resume_duplicates_and_folders(tmp_path, monkeypatch):
    up = Uploads(tmp_path)
    data = os.urandom(10_000)
    first = up.start("map.mrc", len(data), fingerprint="A")
    assert first["offset"] == 0 and not first["done"] and first["rel"] == "map.mrc"
    up.write("map.mrc", 0, data[:4000])
    with pytest.raises(OffsetMismatch) as wrong:
        up.write("map.mrc", 0, data[:4000])  # the same chunk twice
    assert wrong.value.offset == 4000
    # The browser was closed; the same file sent again (another server process) resumes at 4000
    again = Uploads(tmp_path).start("map.mrc", len(data), fingerprint="A")
    assert again["offset"] == 4000
    done = up.write("map.mrc", 4000, data[4000:])
    assert done["done"] and (tmp_path / "uploads" / "map.mrc").read_bytes() == data
    assert not list((tmp_path / "uploads").glob(".map.mrc.part*"))
    # Identical file: not sent again; another file with that name: a new name
    assert up.start("map.mrc", len(data), fingerprint="A")["reused"]
    other = up.start("map.mrc", 5, fingerprint="B")
    assert other["rel"] == "map-2.mrc" and other["offset"] == 0
    up.write("map-2.mrc", 0, b"12345")
    assert up.start("map.mrc", 5, fingerprint="B")["rel"] == "map-2.mrc"  # recognised under its new name
    with pytest.raises(UploadError, match="More data"):
        up.start("x.mrc", 3, fingerprint="C")
        up.write("x.mrc", 0, b"1234")
    up.cancel("x.mrc")
    assert not (tmp_path / "uploads" / ".x.mrc.part").exists()
    assert up.start("empty.txt", 0)["done"] and (tmp_path / "uploads" / "empty.txt").exists()
    # Folders keep their names; the same folder sent again (same fingerprint) is resumed, another one gets "-2"
    assert up.reserve_folder("J42", "folder-1") == "J42"
    assert up.reserve_folder("J42", "folder-1") == "J42"
    assert up.reserve_folder("J42", "folder-2") == "J42-2"
    sub = up.start("J42_008_volume_map_half_A.mrc", 3, folder="J42", fingerprint="H")
    assert sub["rel"] == "J42/J42_008_volume_map_half_A.mrc"
    up.write(sub["rel"], 0, b"abc")
    names = [e["rel"] for e in up.list()]
    assert "J42/J42_008_volume_map_half_A.mrc" in names and all(not n.split("/")[-1].startswith(".") for n in names)
    # Paths leading outside the uploads folder are refused
    for rel in ("../escape.mrc", "/etc/passwd", "J42/../../x", ".index.json"):
        with pytest.raises(UploadError):
            up.write(rel, 0, b"x")
    os.symlink("/tmp", tmp_path / "uploads" / "evil")
    with pytest.raises(UploadError):
        up.start("x.mrc", 1, folder="evil")
    # Not enough room on the disk
    monkeypatch.setattr(upmod.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 10})())
    with pytest.raises(UploadError, match="Not enough space"):
        up.start("big.mrc", 10**9)


def test_upload_and_import_maps(manager, synthetic):
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url=BASE) as client:
        p = client.post("/api/projects", json={"title": "Uploads"}).json()
        paths = {}
        for key, src in (("half_map_a", synthetic["half_a"]), ("half_map_b", synthetic["half_b"]), ("mask", synthetic["mask"])):
            info = send(client, p["uid"], str(src), src.read_bytes(), chunk=200_000, fingerprint=f"{src.name}:1")
            assert info["done"] and info["path"].endswith(f"uploads/{src.name}")
            paths[key] = info["path"]
        listing = client.get(f"/api/projects/{p['uid']}/uploads").json()
        assert {e["rel"] for e in listing["files"]} == {s.name for s in (synthetic["half_a"], synthetic["half_b"], synthetic["mask"])}
        assert listing["chunk_size"] == upmod.CHUNK_SIZE
        # A chunk sent at the wrong place: 409 with the right offset
        start = client.post(f"/api/projects/{p['uid']}/uploads", json={"name": "late.mrc", "size": 10, "fingerprint": "z"}).json()
        r = client.put(f"/api/projects/{p['uid']}/uploads", params={"rel": start["rel"], "offset": 5}, content=b"xxxxx")
        assert r.status_code == 409 and r.json()["offset"] == 0
        assert client.delete(f"/api/projects/{p['uid']}/uploads", params={"rel": start["rel"]}).json()["ok"]
        assert client.post(f"/api/projects/{p['uid']}/uploads", json={"name": "a", "size": "x"}).status_code == 400
        # Import them: the job's copies are hard links of the uploads (no second copy on disk)
        job = client.post(f"/api/projects/{p['uid']}/jobs", json={"type": "import_maps", "params": paths, "queue": True}).json()
        run_until_done(manager, p["uid"], [job["uid"]])
        detail = client.get(f"/api/projects/{p['uid']}/jobs/{job['uid']}").json()
        assert detail["status"] == "completed", detail.get("error")
        out = next(o for o in detail["outputs"] if o["name"] == "half_maps")
        imported = manager.project_dir(p["uid"]) / out["files"][0]
        assert os.path.samefile(imported, paths["half_map_a"]) and imported.stat().st_nlink >= 2
        # The file browser opened from the project reaches the uploads
        fs = client.get("/api/fs", params={"path": str(manager.project_dir(p["uid"]) / "uploads"), "project": p["uid"]}).json()
        assert any(e["name"] == synthetic["half_a"].name for e in fs["entries"])


def test_member_uploads_into_a_shared_project(manager, synthetic):
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    users = app.state.users
    users.create("admin", "admin long password", role="admin")
    alice = users.create("alice", "alice long password")
    bob = users.create("bob", "bob long password")
    alice_token, _ = users.new_token(alice, "test")
    bob_token, _ = users.new_token(bob, "test")
    as_alice = {"Authorization": f"Bearer {alice_token}"}
    as_bob = {"Authorization": f"Bearer {bob_token}"}
    with TestClient(app, base_url=BASE) as client:
        shared = client.post("/api/projects", json={"title": "Shared"}, headers=as_alice).json()
        private = client.post("/api/projects", json={"title": "Private"}, headers=as_alice).json()
        manager.update_project(shared["uid"], members=["bob"])
        assert client.post(f"/api/projects/{private['uid']}/uploads", json={"name": "x.mrc", "size": 1},
                           headers=as_bob).status_code == 404  # not his project
        src = synthetic["map"]
        info = send(client, shared["uid"], str(src), src.read_bytes(), chunk=500_000, headers=as_bob)
        # bob's folders do not contain alice's project, but the uploads of the project he works in are his to use
        job = client.post(f"/api/projects/{shared['uid']}/jobs", headers=as_bob,
                          json={"type": "import_maps", "params": {"map_sharp": info["path"]}})
        assert job.status_code == 200, job.text
        other = send(client, private["uid"], str(src), src.read_bytes(), chunk=500_000, headers=as_alice)
        refused = client.post(f"/api/projects/{shared['uid']}/jobs", headers=as_bob,
                              json={"type": "import_maps", "params": {"map_sharp": other["path"]}})
        assert refused.status_code == 400 and "outside your folders" in refused.json()["detail"]
        fs = client.get("/api/fs", params={"project": shared["uid"], "path": info["path"]}, headers=as_bob)
        assert fs.status_code == 200 and str(manager.project_dir(shared["uid"])) in " ".join(fs.json()["roots"])
        assert client.get("/api/fs", params={"project": private["uid"], "path": other["path"]}, headers=as_bob).status_code == 403


def test_chunk_size_limit(manager, monkeypatch):
    monkeypatch.setattr("cryoplug.server.app.CHUNK_MAX", 100)
    app = create_app(manager.config, start_scheduler=False, manager=manager)
    with TestClient(app, base_url=BASE) as client:
        p = client.post("/api/projects", json={"title": "Limits"}).json()
        start = client.post(f"/api/projects/{p['uid']}/uploads", json={"name": "big.bin", "size": 1000}).json()
        r = client.put(f"/api/projects/{p['uid']}/uploads", params={"rel": start["rel"], "offset": 0}, content=b"x" * 500)
        assert r.status_code == 413
