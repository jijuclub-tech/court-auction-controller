"""S-1.0 site-screen map ingest. Data never goes to GitHub.

Stdlib-only, isolated from existing collector/report/performance tables.
Wire protocol is a provisional *adapter*, not an assertion about the author's
unseen /workspace/map-data/batches files. See docs/SITE_MAP_INGEST_S1.md.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import ssl
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qsl, urlsplit

ROOT = None
LOCK = threading.RLock()
IDENT = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
SECRET_KEYS = {"password", "passwd", "secret", "token", "apikey", "api_key",
               "authorization", "cookie", "set-cookie", "session", "credential",
               "private_key", "client_secret"}
PROTOCOL = "bidguard.site-map-batch.v1"
MAX_MAP = 1024 * 1024
MAX_IMAGES = 8
MAX_IMAGE = 512 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def validate_id(value):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValueError("invalid site_id/batch_id/map_version")
    return value


def safe_path(value):
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ValueError("invalid evidence path")
    if "\\" in value or ":" in value or value.startswith("/"):
        raise ValueError("unsafe evidence path")
    p = PurePosixPath(value)
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError("unsafe evidence path")
    if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError("unsupported evidence type")
    return str(p)


def secrets_in_keys(value, depth=0):
    if depth > 20:
        raise ValueError("nested payload too deep")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("non-string payload key")
            lower = key.lower().replace("-", "_")
            if lower in SECRET_KEYS or lower.endswith("_token") or lower.endswith("_password"):
                raise ValueError("sensitive field in map payload")
            secrets_in_keys(child, depth + 1)
    elif isinstance(value, list):
        for item in value:
            secrets_in_keys(item, depth + 1)


def validate(envelope):
    if not isinstance(envelope, dict) or envelope.get("protocol") != PROTOCOL:
        raise ValueError("unsupported site map protocol")
    site = validate_id(envelope.get("site_id"))
    batch = validate_id(envelope.get("batch_id"))
    version = validate_id(envelope.get("map_version"))
    captured = envelope.get("captured_at")
    if not isinstance(captured, str) or len(captured) > 50:
        raise ValueError("captured_at required")
    try:
        stamp = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("timezone required")
    except (ValueError, TypeError):
        raise ValueError("captured_at must contain timezone")
    url = envelope.get("source_url")
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("invalid source_url")
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("invalid source_url")
    if any(k.lower() in SECRET_KEYS for k, _ in parse_qsl(parsed.query)):
        raise ValueError("source_url contains credential")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload must be a map object")
    secrets_in_keys(payload)
    payload_raw = canonical(payload)
    if len(payload_raw) > MAX_MAP:
        raise ValueError("payload too large")
    actual = digest(payload_raw)
    if not isinstance(envelope.get("payload_sha256"), str) or not SHA.fullmatch(envelope["payload_sha256"]):
        raise ValueError("invalid payload_sha256")
    if not hmac.compare_digest(actual, envelope["payload_sha256"]):
        raise ValueError("payload hash mismatch")

    imgs = envelope.get("evidence", [])
    if not isinstance(imgs, list) or len(imgs) > MAX_IMAGES:
        raise ValueError("too many evidence files")
    files, metas = [], []
    seen = set()
    for image in imgs:
        if not isinstance(image, dict):
            raise ValueError("invalid evidence object")
        path = safe_path(image.get("path"))
        if path in seen:
            raise ValueError("duplicate evidence path")
        seen.add(path)
        claimed = image.get("sha256")
        if not isinstance(claimed, str) or not SHA.fullmatch(claimed):
            raise ValueError("invalid evidence hash")
        encoded = image.get("content_b64")
        if not isinstance(encoded, str) or len(encoded) > 4 * MAX_IMAGE // 3 + 64:
            raise ValueError("invalid evidence content")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("evidence must be base64")
        if len(raw) == 0 or len(raw) > MAX_IMAGE:
            raise ValueError("evidence size limit")
        if not (raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")
                or (raw.startswith(b"RIFF") and raw[8:12] == b"WEBP")):
            raise ValueError("evidence must be JPEG/PNG/WEBP")
        if not hmac.compare_digest(digest(raw), claimed):
            raise ValueError("evidence hash mismatch")
        files.append((path, raw))
        metas.append({"path": path, "sha256": claimed, "bytes": len(raw)})
    manifest = {
        "protocol": PROTOCOL, "site_id": site, "batch_id": batch,
        "map_version": version, "captured_at": captured, "source_url": url,
        "payload_sha256": actual, "payload": payload, "evidence": metas
    }
    # Full manifest digest is stable across retries and includes evidence hashes.
    manifest_hash = digest(canonical(manifest))
    return manifest, manifest_hash, files


def init(con, root):
    global ROOT
    ROOT = Path(os.environ.get("CONTROLLER_SITE_MAP_DIR", str(Path(root) / "site-map-batches")))
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(ROOT, 0o700)
    con.execute("""CREATE TABLE IF NOT EXISTS site_map_batches (
        site_id TEXT NOT NULL, batch_id TEXT NOT NULL, map_version TEXT NOT NULL,
        content_sha256 TEXT NOT NULL, captured_at TEXT NOT NULL,
        received_at TEXT NOT NULL, backup_status TEXT NOT NULL DEFAULT 'pending',
        PRIMARY KEY(site_id,batch_id))""")
    con.execute("""CREATE INDEX IF NOT EXISTS idx_sitemap_latest
        ON site_map_batches(site_id,captured_at DESC)""")


def ssl_ok(handler):
    if isinstance(handler.connection, ssl.SSLSocket):
        return True
    peer = handler.client_address[0]
    if peer in ("127.0.0.1", "::1"):
        if handler.headers.get("X-Forwarded-Proto", "") == "https":
            return True
        if os.environ.get("CONTROLLER_MAP_ALLOW_HTTP_LOOPBACK") == "1":
            return True
    return False


def write_durable(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path, "xb") as f:
        os.chmod(path, 0o600)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def receive(handler, b):
    manifest, content_hash, files = validate(b)
    site, batch = manifest["site_id"], manifest["batch_id"]
    with LOCK:
        with handler_db(handler) as con:
            previous = con.execute(
                "SELECT content_sha256 FROM site_map_batches WHERE site_id=? AND batch_id=?",
                (site, batch)).fetchone()
            if previous:
                if hmac.compare_digest(previous["content_sha256"], content_hash):
                    stored = ROOT / site / batch / "manifest.json"
                    if not stored.is_file() or digest(stored.read_bytes()) != content_hash:
                        return 503, {"ok": False, "error": "stored batch hash mismatch"}
                    return 200, {"ok": True, "status": "stored", "duplicate": True,
                                 "site_id": site, "batch_id": batch, "sha256": content_hash}
                return 409, {"ok": False, "error": "batch_id conflict"}
        final = ROOT / site / batch
        final.parent.mkdir(exist_ok=True, mode=0o700)
        if final.exists():
            existing = final / "manifest.json"
            # Recovery if durable files landed immediately before a DB crash.
            if not existing.is_file() or digest(existing.read_bytes()) != content_hash:
                return 409, {"ok": False, "error": "existing batch differs; review required"}
        else:
            stage = Path(tempfile.mkdtemp(prefix=".staging-", dir=ROOT))
            try:
                write_durable(stage / "manifest.json", canonical(manifest))
                for rel, raw in files:
                    write_durable(stage / "evidence" / rel, raw)
                os.rename(stage, final)  # same filesystem; commit after fsync
                fd = os.open(final.parent, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        with handler_db(handler) as con:
            con.execute("""INSERT INTO site_map_batches
                (site_id,batch_id,map_version,content_sha256,captured_at,received_at)
                VALUES (?,?,?,?,?,?)""",
                (site, batch, manifest["map_version"], content_hash,
                 manifest["captured_at"], datetime.now(timezone.utc).isoformat()))
        return 201, {"ok": True, "status": "stored", "duplicate": False,
                     "site_id": site, "batch_id": batch, "sha256": content_hash,
                     "backup_status": "pending"}


def handler_db(handler):
    # Existing controller opens its SQLite connection through module db().
    return handler._site_map_db()


def route(handler, method, path, query):
    if path not in ("/api/v1/site-maps/batches", "/api/v1/site-maps/latest",
                    "/api/admin/site-maps/state"):
        return False
    if path == "/api/admin/site-maps/state":
        if method != "GET":
            handler._json(405, {"ok": False})
        elif not handler._admin():
            handler._json(403, {"ok": False})
        else:
            with handler_db(handler) as con:
                totals = con.execute("""SELECT backup_status,COUNT(*) n FROM
                    site_map_batches GROUP BY backup_status""").fetchall()
                latest = con.execute("""SELECT site_id,batch_id,map_version,captured_at,
                    received_at,backup_status FROM site_map_batches
                    ORDER BY received_at DESC LIMIT 30""").fetchall()
                handler._json(200, {"ok": True, "counts": {x["backup_status"]: x["n"] for x in totals},
                                    "recent": [dict(x) for x in latest]})
        return True
    if not ssl_ok(handler):
        handler._json(403, {"ok": False, "error": "HTTPS via trusted proxy required"})
        return True
    if path == "/api/v1/site-maps/batches":
        if method != "POST":
            handler._json(405, {"ok": False})
            return True
        secret = os.environ.get("CONTROLLER_MAP_INGEST_TOKEN", "")
        offered = handler.headers.get("X-Map-Token", "")
        if len(secret) < 24 or not hmac.compare_digest(offered, secret):
            handler._json(403, {"ok": False, "error": "map sender not authorized"})
            return True
        size = handler.headers.get("Content-Length", "")
        if not size.isdigit() or int(size) == 0:
            handler._json(411, {"ok": False, "error": "Content-Length required"})
        elif int(size) > 6 * 1024 * 1024:
            handler._json(413, {"ok": False, "error": "batch too large"})
        else:
            try:
                status, obj = receive(handler, handler._body())
            except (ValueError, TypeError, OverflowError):
                status, obj = 422, {"ok": False, "error": "invalid map batch"}
            handler._json(status, obj)
        return True
    # Existing approved collector authentication, but DO NOT accept tokenless
    # legacy collector entries for map download.
    if method != "GET":
        handler._json(405, {"ok": False})
        return True
    name, site = query.get("name", ""), query.get("site_id", "")
    if not IDENT.fullmatch(name) or not IDENT.fullmatch(site):
        handler._json(400, {"ok": False, "error": "name and site_id required"})
        return True
    with handler_db(handler) as con:
        node = con.execute("SELECT * FROM collectors WHERE name=?", (name,)).fetchone()
        token = handler.headers.get("X-Collector-Token", "")
        if (node is None or node["status"] != "approved"
                or not node["token_hash"] or not token
                or not hmac.compare_digest(node["token_hash"], hashlib.sha256(token.encode()).hexdigest())):
            handler._json(403, {"ok": False})
            return True
        latest = con.execute("""SELECT batch_id,content_sha256 FROM site_map_batches
            WHERE site_id=? ORDER BY captured_at DESC, received_at DESC LIMIT 1""",
            (site,)).fetchone()
    if latest is None:
        handler._json(404, {"ok": False, "error": "no validated map"})
        return True
    file = ROOT / site / latest["batch_id"] / "manifest.json"
    if not file.is_file() or digest(file.read_bytes()) != latest["content_sha256"]:
        handler._json(503, {"ok": False, "error": "stored map requires verification"})
        return True
    manifest = json.loads(file.read_text(encoding="utf-8"))
    if query.get("with_evidence") == "1":
        for meta in manifest.get("evidence", []):
            rel = safe_path(meta["path"])
            raw = (file.parent / "evidence" / rel).read_bytes()
            if digest(raw) != meta["sha256"]:
                handler._json(503, {"ok": False, "error": "evidence hash mismatch"})
                return True
            meta["content_b64"] = base64.b64encode(raw).decode("ascii")
    handler._json(200, {"ok": True, "sha256": latest["content_sha256"], "batch": manifest})
    return True
