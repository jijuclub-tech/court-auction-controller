#!/usr/bin/env python3
"""Explicit S-1.0 map backups: NAS must be a REAL mounted filesystem.

Google Drive verification is optional through an already-configured rclone
remote. Never marks a backup complete based on a successful copy alone.
No original source files are deleted or overwritten.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for part in iter(lambda: f.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def inspect(local, expected_sha):
    mf = local / "manifest.json"
    if not mf.is_file() or sha(mf) != expected_sha:
        raise RuntimeError("source manifest hash mismatch")
    data = json.loads(mf.read_text(encoding="utf-8"))
    for meta in data["evidence"]:
        from site_map_ingest import safe_path
        item = local / "evidence" / safe_path(meta["path"])
        if not item.is_file() or sha(item) != meta["sha256"]:
            raise RuntimeError("source evidence hash mismatch")
    return data


def manifest_equal(source, other):
    files = [x for x in source.rglob("*") if x.is_file()]
    names = {x.relative_to(source) for x in files}
    copies = {x.relative_to(other) for x in other.rglob("*") if x.is_file()}
    if names != copies or any(sha(source / f) != sha(other / f) for f in names):
        raise RuntimeError("NAS backup differs from source")


def to_nas(local, mount, site, batch):
    if not mount.is_dir() or not os.path.ismount(mount):
        raise RuntimeError("NAS mount missing: refuse to write into local shadow folder")
    parent = mount / "site-map-batches" / site
    parent.mkdir(parents=True, exist_ok=True)
    dest = parent / batch
    if dest.exists():
        manifest_equal(local, dest)
        return
    stage = Path(tempfile.mkdtemp(prefix=".backup-", dir=parent))
    try:
        for item in local.rglob("*"):
            rel = item.relative_to(local)
            target = stage / rel
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target)
        manifest_equal(local, stage)
        os.rename(stage, dest)
        manifest_equal(local, dest)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def to_drive(local, remote, site, batch):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+:[A-Za-z0-9/_-]*", remote):
        raise RuntimeError("invalid preconfigured rclone remote")
    if not shutil.which("rclone"):
        raise RuntimeError("rclone not installed/configured")
    dest = remote.rstrip("/") + "/site-map-batches/" + site + "/" + batch
    for command in (["rclone", "copy", str(local), dest, "--immutable"],
                    ["rclone", "check", str(local), dest, "--download"]):
        result = subprocess.run(command, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=180)
        if result.returncode:
            raise RuntimeError("Drive rclone upload/download verification failed")


def main(argv=None):
    p = argparse.ArgumentParser(description="Verify S-1.0 maps on a mounted NAS and/or Drive")
    p.add_argument("--data", default=os.environ.get("CONTROLLER_DATA", "/var/lib/court-auction-controller"))
    p.add_argument("--nas-mount", default=os.environ.get("CONTROLLER_NAS_MOUNT", ""))
    p.add_argument("--drive-remote", default=os.environ.get("CONTROLLER_MAP_DRIVE_REMOTE", ""))
    a = p.parse_args(argv)
    if not (a.nas_mount or a.drive_remote):
        print("No verified backup target configured", file=sys.stderr)
        return 2
    data = Path(a.data)
    db = data / "controller.db"
    if not db.is_file():
        print("controller.db unavailable; no backup attempted", file=sys.stderr)
        return 2
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    root = Path(os.environ.get("CONTROLLER_SITE_MAP_DIR", str(data / "site-map-batches")))
    errors = 0
    for row in con.execute("SELECT site_id,batch_id,content_sha256,backup_status FROM site_map_batches"):
        site, batch = row["site_id"], row["batch_id"]
        local = root / site / batch
        try:
            inspect(local, row["content_sha256"])
            nas_ok = "nas" in row["backup_status"] or row["backup_status"] == "both_verified"
            drive_ok = "drive" in row["backup_status"] or row["backup_status"] == "both_verified"
            if a.nas_mount:
                to_nas(local, Path(a.nas_mount), site, batch)
                nas_ok = True
            if a.drive_remote:
                to_drive(local, a.drive_remote, site, batch)
                drive_ok = True
            status = ("both_verified" if nas_ok and drive_ok else
                      "nas_verified" if nas_ok else "drive_verified" if drive_ok else "pending")
            con.execute("""UPDATE site_map_batches SET backup_status=?
                           WHERE site_id=? AND batch_id=?""", (status, site, batch))
            con.commit()
            print(site, batch, status)
        except (RuntimeError, OSError, ValueError, sqlite3.Error,
                subprocess.TimeoutExpired) as exc:
            errors += 1
            print(site, batch, "BACKUP_PENDING:", str(exc)[:120], file=sys.stderr)
    con.close()
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
