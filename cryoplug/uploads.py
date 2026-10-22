"""Files sent from the user's computer into a project: ``<project>/uploads/``.

Maps weigh up to a few GB, so the browser sends each file in chunks (``CHUNK_SIZE``) with the offset where each
one starts: a chunk lost on the way is sent again, and an interrupted transfer resumes where it stopped (also
after a restart of the server) when the same file is sent again — recognised by its fingerprint (name, size,
modification time and, when the browser can compute it, a hash of its first and last MB).  Until its last
chunk arrives, a file is written as the hidden ``.<name>.part`` next to its final place, with its size and
fingerprint in ``.<name>.part.json``.

A file identical to one already uploaded is not sent again; another file with the same name gets a new name
(``map-2.mrc``).  A folder (a CryoSPARC job folder, a series of volumes) first reserves its own subfolder, so
that its files keep their names and relative paths.  Import jobs hard-link the uploaded files instead of copying
them (:meth:`cryoplug.jobs.base.JobContext.copy_in`): no second copy on disk.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

UPLOADS_DIR = "uploads"
CHUNK_SIZE = 16 * 1024 * 1024       # what the browser sends per request
CHUNK_MAX = 64 * 1024 * 1024        # largest chunk accepted
DISK_MARGIN = 512 * 1024 * 1024     # free space left on the disk after an upload
INDEX = ".index.json"               # uploaded files: size, fingerprint, date, who
FOLDER_MARK = ".cryoplug_upload.json"
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class UploadError(ValueError):
    """A refused upload (shown to the user)."""


class OffsetMismatch(UploadError):
    """The chunk does not start where the file stands: the client resumes from ``offset``."""

    def __init__(self, offset: int):
        super().__init__(f"The upload stands at byte {offset}")
        self.offset = offset


def clean_name(name: Any) -> str:
    """A safe file name: the last path component, spaces and unusual characters as '_'."""
    base = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if base.startswith("._"):  # would become the name of the file it describes
        raise UploadError(f"{base} is a macOS metadata file, not data: not uploaded")
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^A-Za-z0-9._+-]", "_", base).lstrip("._-+")
    if not base or base in (".", ".."):
        raise UploadError(f"Unusable file name: {name!r}")
    if len(base) > 200:
        stem, dot, suffix = base.rpartition(".")
        base = (stem[: 200 - len(suffix) - 1] + dot + suffix) if dot and len(suffix) < 20 else base[:200]
    return base


def clean_folder(folder: Any) -> str:
    """A safe relative folder (each component cleaned; '' for none)."""
    parts = [p for p in str(folder or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise UploadError("A folder cannot go up ('..')")
    if len(parts) > 12:
        raise UploadError("Folder too deep")
    return "/".join(clean_name(p) for p in parts)


def _lock(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


class Uploads:
    """The uploads of one project."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.root = self.project_dir / UPLOADS_DIR

    # ------------------------------------------------------------ paths
    def _dest(self, rel: str) -> Path:
        rel = str(rel or "")
        clean = "/".join(p for p in (clean_folder(PurePosixPath(rel).parent.as_posix()), clean_name(PurePosixPath(rel).name)) if p)
        if clean != rel:
            raise UploadError(f"Invalid upload path: {rel!r}")
        dest = self.root / rel
        if self.root.resolve() not in dest.resolve().parents:  # a symbolic link leading elsewhere
            raise UploadError(f"Invalid upload path: {rel!r}")
        return dest

    @staticmethod
    def _part(dest: Path) -> tuple[Path, Path]:
        return dest.with_name(f".{dest.name}.part"), dest.with_name(f".{dest.name}.part.json")

    def _rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def describe(self, rel: str, offset: int, size: int, done: bool, reused: bool = False) -> dict[str, Any]:
        dest = self.root / rel
        return {"rel": rel, "path": str(dest), "project_path": f"{UPLOADS_DIR}/{rel}", "offset": offset, "size": size,
                "done": done, "reused": reused}

    # ------------------------------------------------------------ index
    def _index(self) -> dict[str, Any]:
        try:
            return json.loads((self.root / INDEX).read_text())
        except (OSError, ValueError):
            return {}

    def _remember(self, rel: str, size: int, fingerprint: str, user: str) -> None:
        with _lock(f"{self.root}:index"):
            index = self._index()
            index[rel] = {"size": size, "fingerprint": fingerprint, "uploaded_at": time.time(), "by": user}
            tmp = self.root / f"{INDEX}.tmp"
            tmp.write_text(json.dumps(index, indent=1))
            os.replace(tmp, self.root / INDEX)

    def _same(self, rel: str, size: int, fingerprint: str) -> bool:
        dest = self.root / rel
        if not dest.is_file() or dest.stat().st_size != size:
            return False
        known = self._index().get(rel) or {}
        return not fingerprint or known.get("fingerprint") == fingerprint

    # ------------------------------------------------------------ folders
    def reserve_folder(self, name: Any, fingerprint: str = "", parent: Any = "") -> str:
        """A subfolder of its own for a folder being uploaded: ``name``, else ``name-2``…; the same folder again
        when it was reserved by an upload with the same fingerprint (to resume it)."""
        base = clean_name(name)
        parent = clean_folder(parent)
        self.root.mkdir(parents=True, exist_ok=True)
        with _lock(f"{self.root}:folders"):
            for n in range(1, 1000):
                rel = "/".join(p for p in (parent, base if n == 1 else f"{base}-{n}") if p)
                folder = self.root / rel
                mark = folder / FOLDER_MARK
                if not folder.exists():
                    folder.mkdir(parents=True)
                    mark.write_text(json.dumps({"fingerprint": fingerprint, "created_at": time.time()}))
                    return rel
                try:
                    known = json.loads(mark.read_text()).get("fingerprint")
                except (OSError, ValueError):
                    known = None
                if fingerprint and known == fingerprint:
                    return rel
        raise UploadError(f"Too many folders named {base}")

    # ------------------------------------------------------------ transfer
    def start(self, name: Any, size: Any, folder: Any = "", fingerprint: Any = "", user: str = "") -> dict[str, Any]:
        """Announce a file: where it goes and from which byte to send it (0, or where an interrupted upload of the
        same file stopped; ``done`` when an identical file is already there)."""
        try:
            size = int(size)
        except (TypeError, ValueError):
            raise UploadError("The file size is missing") from None
        if size < 0:
            raise UploadError("Invalid file size")
        fingerprint = str(fingerprint or "")[:300]
        folder = clean_folder(folder)
        rel = "/".join(p for p in (folder, clean_name(name)) if p)
        self.root.mkdir(parents=True, exist_ok=True)
        with _lock(f"{self.root}:{rel}"):
            if self._same(rel, size, fingerprint):
                return self.describe(rel, size, size, True, reused=True)
            if (self.root / rel).exists():
                if folder and (self.root / folder / FOLDER_MARK).exists():
                    pass  # a file of a folder upload sent again: replaced below when it completes
                else:
                    rel = self._free_name(rel, size, fingerprint)
                    if self._same(rel, size, fingerprint):
                        return self.describe(rel, size, size, True, reused=True)
            dest = self._dest(rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            part, meta = self._part(dest)
            offset = 0
            try:
                known = json.loads(meta.read_text())
                if known.get("size") == size and known.get("fingerprint") == fingerprint and part.is_file():
                    offset = min(part.stat().st_size, size)
            except (OSError, ValueError):
                pass
            free = shutil.disk_usage(dest.parent).free
            if size - offset + DISK_MARGIN > free:
                raise UploadError(f"Not enough space on the server for {clean_name(name)}: {_gb(size - offset)} needed, "
                                  f"{_gb(free)} free in {self.root}")
            if offset == 0:
                part.write_bytes(b"")
                meta.write_text(json.dumps({"size": size, "fingerprint": fingerprint, "started_at": time.time(), "by": user}))
            if offset >= size:
                return self._finish(rel, dest, part, meta, size, fingerprint, user)
            return self.describe(rel, offset, size, False)

    def _free_name(self, rel: str, size: int, fingerprint: str) -> str:
        path = PurePosixPath(rel)
        suffix = "".join(path.suffixes[-2:]) if path.suffix == ".gz" else path.suffix
        stem = path.name[: len(path.name) - len(suffix)] if suffix else path.name
        for n in range(2, 1000):
            candidate = (path.parent / f"{stem}-{n}{suffix}").as_posix().lstrip("./")
            dest = self.root / candidate
            part, _ = self._part(dest)
            if not dest.exists() or self._same(candidate, size, fingerprint):
                if not part.exists() or self._part_matches(dest, size, fingerprint):
                    return candidate
        raise UploadError(f"Too many files named {path.name}")

    def _part_matches(self, dest: Path, size: int, fingerprint: str) -> bool:
        _, meta = self._part(dest)
        try:
            known = json.loads(meta.read_text())
        except (OSError, ValueError):
            return False
        return known.get("size") == size and known.get("fingerprint") == fingerprint

    def write(self, rel: str, offset: Any, data: bytes, user: str = "") -> dict[str, Any]:
        """Append a chunk that starts at ``offset``."""
        dest = self._dest(rel)
        part, meta = self._part(dest)
        if len(data) > CHUNK_MAX:
            raise UploadError(f"Chunk too large (at most {CHUNK_MAX // 2**20} MB)")
        try:
            offset = int(offset)
        except (TypeError, ValueError):
            raise UploadError("The chunk offset is missing") from None
        with _lock(f"{self.root}:{rel}"):
            try:
                known = json.loads(meta.read_text())
            except (OSError, ValueError):
                if dest.is_file():
                    size = dest.stat().st_size
                    return self.describe(rel, size, size, True)
                raise UploadError(f"No upload of {rel} in progress: send the file again") from None
            size, fingerprint = int(known["size"]), str(known.get("fingerprint") or "")
            current = part.stat().st_size if part.is_file() else 0
            if offset != current:
                raise OffsetMismatch(current)
            if current + len(data) > size:
                raise UploadError(f"More data than announced for {rel} ({size} bytes)")
            with open(part, "ab") as fh:
                fh.write(data)
            current += len(data)
            if current == size:
                return self._finish(rel, dest, part, meta, size, fingerprint, user or known.get("by", ""))
            return self.describe(rel, current, size, False)

    def _finish(self, rel: str, dest: Path, part: Path, meta: Path, size: int, fingerprint: str, user: str) -> dict[str, Any]:
        os.replace(part, dest)
        meta.unlink(missing_ok=True)
        self._remember(rel, size, fingerprint, user)
        return self.describe(rel, size, size, True)

    def cancel(self, rel: str) -> None:
        """Forget an unfinished upload (the files already complete stay)."""
        dest = self._dest(rel)
        part, meta = self._part(dest)
        with _lock(f"{self.root}:{rel}"):
            part.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)

    def list(self) -> list[dict[str, Any]]:
        """The files uploaded so far, the latest first."""
        if not self.root.is_dir():
            return []
        index = self._index()
        out = []
        for path in self.root.rglob("*"):
            if not path.is_file() or any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            rel = self._rel(path)
            st = path.stat()
            out.append({"rel": rel, "path": str(path), "size": st.st_size, "mtime": st.st_mtime,
                        "by": (index.get(rel) or {}).get("by", "")})
        return sorted(out, key=lambda e: -e["mtime"])


def _gb(n: float) -> str:
    return f"{n / 1e9:.1f} GB" if n >= 1e8 else f"{n / 1e6:.0f} MB"
