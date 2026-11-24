"""BIDGUARD #50: durable controller SSD -> NAS replication, T-v1.8.1.

The collector must NEVER address NAS directly.  This module is invoked by
the controller background worker.  A controller ACK is not a NAS backup ACK.
When NAS is not mounted, leave source and pending DB rows untouched.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import tempfile
from pathlib import Path

NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
CHUNK = 1024 * 1024


def hash_file(file):
    sha = hashlib.sha256()
    with open(file, "rb") as stream:
        for block in iter(lambda: stream.read(CHUNK), b""):
            sha.update(block)
    return sha.hexdigest()


def _mounted(mount, checker):
    mount = Path(mount)
    if (not mount.is_absolute() or mount.is_symlink()
            or not mount.is_dir() or not checker(str(mount))):
        return False
    return True


def mirror_pending(con, *, staging_root, nas_mount, limit=20, mount_checker=os.path.ismount):
    """Copy & re-hash, then mark NAS_VERIFIED. Never drop original or invent a mount.

    Pure dependency injection for tests: mount_checker is replaced by a fake.
    """
    stage = Path(staging_root)
    nas = Path(nas_mount)
    if not stage.is_absolute() or not stage.is_dir() or stage.is_symlink():
        raise ValueError("verified local staging root required")
    if nas == stage or stage == nas or nas in stage.parents or stage in nas.parents:
        raise ValueError("NAS and staging trees must not overlap")
    if not _mounted(nas, mount_checker):
        return {"nas_online": False, "copied": 0, "pending": None, "errors": 0}
    records = con.execute(
        "SELECT id,collector,content_sha256,size_bytes,stored_path FROM "
        "collector_uploads_T_v1_8_0 WHERE backup_status != 'NAS_VERIFIED' "
        "ORDER BY id LIMIT ?", (max(1, min(int(limit), 100)),)
    ).fetchall()
    copied = 0
    errors = 0
    for record in records:
        ident, node, digest, size, recorded = tuple(record)
        if not NODE.fullmatch(node) or not DIGEST.fullmatch(digest) or not isinstance(size, int):
            errors += 1
            continue
        source = stage / node / digest[:2] / digest
        try:
            # Prevent the stored_path field or collector directory from redirecting
            # copying arbitrary server content.  Check local original twice.
            if (not source.is_file() or source.is_symlink() or
                    source.resolve(strict=True) != Path(recorded).resolve(strict=True) or
                    not source.resolve(strict=True).is_relative_to(stage.resolve(strict=True)) or
                    source.stat().st_size != size or hash_file(source) != digest):
                raise ValueError("staged source invalid")
            if not _mounted(nas, mount_checker):
                break
            folder = nas / node / digest[:2]
            if (nas / node).is_symlink() or folder.is_symlink():
                raise ValueError("NAS path symlink")
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            if (nas / node).is_symlink() or folder.is_symlink():
                raise ValueError("NAS folder symlink")
            destination = folder / digest
            if destination.exists() and (
                    destination.is_symlink() or not destination.is_file() or
                    destination.stat().st_size != size or hash_file(destination) != digest):
                raise ValueError("NAS hash conflict; no overwrite")
            if not destination.exists():
                fd, temporary = tempfile.mkstemp(prefix=".nas-part-", dir=str(folder))
                temp = Path(temporary)
                try:
                    with os.fdopen(fd, "wb") as out, open(source, "rb") as inp:
                        while block := inp.read(CHUNK):
                            out.write(block)
                        out.flush()
                        os.fsync(out.fileno())
                    if (temp.stat().st_size != size or hash_file(temp) != digest
                            or not _mounted(nas, mount_checker)):
                        raise ValueError("NAS offline or copy hash invalid")
                    if destination.exists():
                        raise ValueError("concurrent NAS file; retry")
                    os.replace(temp, destination)
                finally:
                    temp.unlink(missing_ok=True)
            if (not _mounted(nas, mount_checker) or
                    destination.stat().st_size != size or hash_file(destination) != digest or
                    hash_file(source) != digest):
                raise ValueError("NAS readback or mount verification failed")
            con.execute(
                "UPDATE collector_uploads_T_v1_8_0 SET backup_status='NAS_VERIFIED' "
                "WHERE id=? AND content_sha256=? AND backup_status != 'NAS_VERIFIED'",
                (ident, digest),
            )
            con.commit()
            copied += 1
        except (OSError, ValueError, sqlite3.Error):
            # Retry later; never delete the source/partially verified NAS target.
            errors += 1
    outstanding = con.execute(
        "SELECT COUNT(*) FROM collector_uploads_T_v1_8_0 "
        "WHERE backup_status != 'NAS_VERIFIED'"
    ).fetchone()[0]
    return {"nas_online": bool(_mounted(nas, mount_checker)), "copied": copied,
            "pending": outstanding, "errors": errors}


def service_loop(*, db_path, staging_root, nas_mount, stop_event, interval=60,
                 report=lambda result: None):
    """Run only when an administrator has configured a real NAS mountpoint."""
    while not stop_event.is_set():
        try:
            with sqlite3.connect(str(db_path), timeout=10) as con:
                result = mirror_pending(con, staging_root=staging_root, nas_mount=nas_mount)
            report(result)
        except (sqlite3.Error, OSError, ValueError):
            report({"nas_online": False, "copied": 0, "errors": 1})
        if stop_event.wait(max(5, int(interval))):
            break
