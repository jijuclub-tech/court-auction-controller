"""BIDGUARD collector v1.8.0 options and offline upload queue (T-v1.8.0).

An additive adapter for v1.7_4p; not a replacement for that collector.
Options without /lock, /kiosk or /upload leave legacy startup unchanged.
Secrets are supplied by a runtime wallet callback, never persisted by this code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

MAX_UPLOAD = 128 * 1024 * 1024
CHUNK = 1024 * 1024
VID_PICO2 = "VID_2E8A"
_ALLOWED_KINDS = {"appraisal", "sale_notice", "result", "site_evidence", "metadata", "other"}


@dataclass(frozen=True)
class Options:
    lock: bool = False
    upload: bool = False
    kiosk: bool = False
    unlock: bool = False


def split_options(argv):
    """Return (optional features, untouched legacy CLI arguments)."""
    flags = {"/lock": "lock", "/upload": "upload",
             "/kiosk": "kiosk", "/unlock": "unlock"}
    opts = set()
    legacy = []
    for value in argv:
        name = flags.get(str(value).lower())
        if name:
            opts.add(name)
        else:
            legacy.append(value)
    if "unlock" in opts and len(opts) > 1:
        raise ValueError("/unlock cannot be combined with other options")
    return Options(**{k: k in opts for k in ("lock", "upload", "kiosk", "unlock")}), legacy


def url_from_policy(value):
    """Accept only a controller-approved HTTPS endpoint (never arbitrary http)."""
    parsed = urlsplit(str(value or ""))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.fragment or parsed.query or parsed.path not in
            ("", "/", "/api/v1/collector/upload")):
        raise ValueError("controller upload HTTPS URL required")
    if parsed.port is not None and not (1 <= parsed.port <= 65535):
        raise ValueError("invalid port")
    return parsed.geturl().rstrip("/") if parsed.path == "/api/v1/collector/upload" else (
        parsed.geturl().rstrip("/") + "/api/v1/collector/upload"
    )


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


class UploadQueue:
    """Persistent outbox; never deletes source files and never treats send as backup."""
    def __init__(self, db_path):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(path)
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS upload_outbox_T_v1_8_0 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_path TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    document_type TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    UNIQUE(local_path, content_sha256)
                )""")

    def enqueue(self, path, document_type="other"):
        if document_type not in _ALLOWED_KINDS:
            raise ValueError("invalid document type")
        file = Path(path).resolve(strict=True)
        if not file.is_file():
            raise ValueError("regular file required")
        size = file.stat().st_size
        if not (0 < size <= MAX_UPLOAD):
            raise ValueError("file size out of range")
        hx = file_hash(file)
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute("""
                INSERT OR IGNORE INTO upload_outbox_T_v1_8_0
                  (local_path,content_sha256,size_bytes,document_type)
                VALUES (?,?,?,?)
            """, (str(file), hx, size, document_type))
        return hx

    def pending(self, limit=10):
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.row_factory = sqlite3.Row
            return [dict(row) for row in con.execute(
                "SELECT * FROM upload_outbox_T_v1_8_0 WHERE state='PENDING' "
                "ORDER BY id LIMIT ?", (max(1, min(int(limit), 100)),)
            )]

    def _state(self, item_id, state, error=""):
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute(
                "UPDATE upload_outbox_T_v1_8_0 SET state=?, attempts=attempts+1, last_error=? WHERE id=?",
                (state, str(error)[:120], item_id)
            )

    def flush(self, controller_url, collector_name, wallet_token, *, send=None, limit=10):
        """Receipt validated before VERIFIED; failures remain pending for retry."""
        url = url_from_policy(controller_url)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", collector_name):
            raise ValueError("invalid collector name")
        if not isinstance(wallet_token, str) or len(wallet_token) < 16:
            raise ValueError("collector wallet token required")
        sender = send or _send_https
        results = []
        for item in self.pending(limit):
            path = Path(item["local_path"])
            try:
                if (not path.is_file() or path.stat().st_size != item["size_bytes"]
                        or file_hash(path) != item["content_sha256"]):
                    self._state(item["id"], "NEEDS_REVIEW", "local source changed or missing")
                    results.append({"id": item["id"], "status": "NEEDS_REVIEW"})
                    continue
                receipt = sender(url, collector_name, wallet_token, item)
                if (receipt.get("ok") is True
                        and receipt.get("status") == "controller_stored"
                        and receipt.get("sha256") == item["content_sha256"]
                        and receipt.get("size") == item["size_bytes"]):
                    self._state(item["id"], "CONTROLLER_VERIFIED")
                    results.append({"id": item["id"], "status": "CONTROLLER_VERIFIED",
                                    "backup_verified": False})
                else:
                    self._state(item["id"], "PENDING", "missing or invalid controller ACK")
                    results.append({"id": item["id"], "status": "PENDING"})
            except Exception as exc:
                # Prevent endpoint/token/path disclosure in logs; class name only.
                self._state(item["id"], "PENDING", type(exc).__name__)
                results.append({"id": item["id"], "status": "PENDING"})
        return results


def _send_https(url, collector_name, token, item):
    class Body:
        def __init__(self, path):
            self.path = path
        def __iter__(self):
            with open(self.path, "rb") as source:
                for block in iter(lambda: source.read(CHUNK), b""):
                    yield block

    req = urllib.request.Request(
        url, data=Body(item["local_path"]), method="POST",
        headers={
            "Content-Type": "application/octet-stream",
            "Content-Length": str(item["size_bytes"]),
            "X-Content-SHA256": item["content_sha256"],
            "X-Document-Type": item["document_type"],
            "X-Collector-Name": collector_name,
            "X-Collector-Token": token,
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError("unverified upload")
        data = resp.read(4096)
        return json.loads(data.decode("utf-8"))


def allowed_site(url, allowed_domains):
    """Strict HTTPS host matching; adversarial host suffixes are never accepted."""
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower().rstrip(".")
        if p.scheme != "https" or not host or p.username or p.password:
            return False
        return any(host == d.lower() or host.endswith("." + d.lower())
                   for d in allowed_domains if d and "." in d)
    except ValueError:
        return False


class KioskPolicy:
    """Chrome target adapter: caller supplies observed tab URLs and close callback."""
    def __init__(self, allowed_domains):
        self.allowed_domains = tuple(allowed_domains)

    def enforce(self, tabs, close_tab, report):
        closed = 0
        for tab in tabs:
            if tab.get("type") not in ("page", None):
                continue
            if not allowed_site(str(tab.get("url", "")), self.allowed_domains):
                close_tab(tab["id"])
                report("kiosk_closed_disallowed_tab", target_id=str(tab["id"])[:72])
                closed += 1
        return closed


def lock_preflight(*, pico_device_ids, physical_device_ids, watchdog_armed,
                   unlock_channel_tested, remote_session_tested):
    """No OS device is touched unless every precondition for recovery is proven.

    A field-tested independent watchdog and the original v1.7_4p device map
    are needed before a Windows PnP backend may be connected. It would be
    unsafe to disable keyboards based only on a VID guess.
    """
    pico = [str(x).upper() for x in pico_device_ids]
    physical = [str(x).upper() for x in physical_device_ids]
    if not pico or not any(VID_PICO2 in x for x in pico):
        return False, "PICO2_NOT_VERIFIED"
    if not physical or any(VID_PICO2 in x for x in physical):
        return False, "TARGET_DEVICE_SET_INVALID"
    if len(physical) != len(set(physical)):
        return False, "DUPLICATE_DEVICE_ID"
    if not watchdog_armed:
        return False, "RECOVERY_WATCHDOG_REQUIRED"
    if not unlock_channel_tested or not remote_session_tested:
        return False, "REMOTE_UNLOCK_NOT_VERIFIED"
    return True, "READY_FOR_SUPERVISED_PILOT"
