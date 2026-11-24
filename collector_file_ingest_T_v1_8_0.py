"""BIDGUARD issue #50: authenticated collector upload intake (T-v1.8.0).

This endpoint accepts one validated file at a time. A successful ACK proves
controller disk write + SHA256 readback, NOT NAS/Drive backup.
No collected bytes or credentials belong in GitHub.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import tempfile
from pathlib import Path

import site_map_ingest

PATH = "/api/v1/collector/upload"
NODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
DOC_TYPES = {"appraisal", "sale_notice", "result", "site_evidence", "metadata", "other"}
MAX_BYTES = 128 * 1024 * 1024
CHUNK = 1024 * 1024


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def init(con, unused_data=None):
    con.execute("""
        CREATE TABLE IF NOT EXISTS collector_uploads_T_v1_8_0 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collector TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            document_type TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            backup_status TEXT NOT NULL DEFAULT 'PENDING_BACKUP',
            UNIQUE(collector, content_sha256)
        )
    """)


def _root():
    value = os.environ.get("BIDGUARD_COLLECTOR_UPLOAD_DIR", "").strip()
    if not value:
        raise RuntimeError("upload destination not configured")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError("upload destination must be absolute")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeError("upload root symlink is forbidden")
    return path


def store_stream(con, source, *, collector, size, expected_hash, document_type, root):
    """Write to a unique temporary file, verify twice, and commit an idempotent receipt."""
    if not NODE.fullmatch(collector):
        raise ValueError("invalid collector")
    if not HASH.fullmatch(expected_hash):
        raise ValueError("invalid sha256")
    if document_type not in DOC_TYPES:
        raise ValueError("invalid document_type")
    if not isinstance(size, int) or not (0 < size <= MAX_BYTES):
        raise ValueError("invalid size")
    root = Path(root)
    folder = root / collector / expected_hash[:2]
    folder.mkdir(parents=True, exist_ok=True)
    if (root / collector).is_symlink() or folder.is_symlink():
        raise ValueError("untrusted collector folder")
    target = folder / expected_hash
    fd, temp_name = tempfile.mkstemp(prefix=".part-", dir=str(folder))
    temp = Path(temp_name)
    try:
        h = hashlib.sha256()
        left = size
        with os.fdopen(fd, "wb") as out:
            while left:
                block = source.read(min(left, CHUNK))
                if not block:
                    raise ValueError("incomplete upload")
                out.write(block)
                h.update(block)
                left -= len(block)
            out.flush()
            os.fsync(out.fileno())
        if not hmac.compare_digest(h.hexdigest(), expected_hash):
            raise ValueError("upload sha256 mismatch")
        if not hmac.compare_digest(sha_file(temp), expected_hash):
            raise ValueError("disk readback sha256 mismatch")
        duplicate = target.exists()
        if duplicate:
            if target.is_symlink() or not target.is_file() or target.stat().st_size != size or sha_file(target) != expected_hash:
                raise ValueError("concurrent target conflict")
        else:
            os.replace(temp, target)
        con.execute("""
            INSERT OR IGNORE INTO collector_uploads_T_v1_8_0
            (collector,content_sha256,size_bytes,document_type,stored_path)
            VALUES (?,?,?,?,?)
        """, (collector, expected_hash, size, document_type, str(target)))
        return {"ok": True, "duplicate": duplicate, "sha256": expected_hash,
                "size": size, "status": "controller_stored", "backup_verified": False}
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def route(handler, method, path, query):
    """Controller handler hook; never consumes/accepts body before authentication."""
    if path != PATH:
        return False
    if method != "POST":
        handler._json(405, {"ok": False, "error": "method not allowed"})
        return True
    if not site_map_ingest.ssl_ok(handler):
        handler.close_connection = True
        handler._json(403, {"ok": False, "error": "HTTPS required"})
        return True
    node = handler.headers.get("X-Collector-Name", "")
    with handler._site_map_db() as con:
        authenticated = NODE.fullmatch(node) and handler._collector_ok(con, node) is not None
    if not authenticated:
        handler.close_connection = True
        handler._json(403, {"ok": False, "error": "registered collector required"})
        return True
    provided_hash = handler.headers.get("X-Content-SHA256", "")
    document_type = handler.headers.get("X-Document-Type", "other")
    size_text = handler.headers.get("Content-Length", "")
    if not size_text.isdecimal():
        handler.close_connection = True
        handler._json(411, {"ok": False, "error": "Content-Length required"})
        return True
    size = int(size_text)
    if not (0 < size <= MAX_BYTES):
        handler.close_connection = True
        handler._json(413, {"ok": False, "error": "upload size not permitted"})
        return True
    if not HASH.fullmatch(provided_hash) or document_type not in DOC_TYPES:
        handler.close_connection = True
        handler._json(422, {"ok": False, "error": "invalid upload metadata"})
        return True
    try:
        root = _root()
        with handler._site_map_db() as con:
            init(con)
            receipt = store_stream(
                con, handler.rfile, collector=node, size=size,
                expected_hash=provided_hash, document_type=document_type, root=root,
            )
        handler._json(200 if receipt["duplicate"] else 201, receipt)
    except (ValueError, OSError, sqlite3.Error) as exc:
        handler.close_connection = True
        # Do not disclose file paths, file contents, or credentials in responses.
        handler._json(422, {"ok": False, "error": type(exc).__name__})
    except RuntimeError:
        handler.close_connection = True
        handler._json(503, {"ok": False, "error": "intake unavailable"})
    return True
