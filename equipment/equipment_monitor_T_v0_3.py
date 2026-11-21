"""BIDGUARD equipment watchdog T-v0.3: tablet-facing status + durable alerts.

Sidecar only. Does not change collector jobs, HID, existing DB schema or files.
Controller integration belongs to the dedicated reviewed PR. Python stdlib only.
"""
from __future__ import annotations
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import ssl
import threading
import time
from urllib.request import Request, urlopen

DB = None
LOCK = threading.RLock()
IDENT = re.compile(r"^[A-Z][A-Z0-9_-]{3,55}$")
FIELDS = {"os", "app_version", "cpu_percent", "ram_percent", "disk_free_bytes",
          "battery_percent", "service_state", "job_state", "nas_state",
          "drive_state", "last_backup_at", "local_pending_count",
          "local_pending_bytes", "last_job_at"}
KNOWN = (
    ("BG-PC-001", "중앙 관제기", "controller"),
    ("BG-PC-002", "분석·관리 PC", "pc"),
    ("BG-PC-003", "글록 수집기 3호", "collector"),
    ("BG-PC-004", "게임 서버", "pc"),
    ("BG-PC-005", "법원 수집기 1호", "collector"),
    ("BG-PC-006", "원격 수집기 2호", "collector"),
    ("BG-ST-001", "NAS", "nas"),
)


def connect():
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init(db_path):
    global DB
    DB = str(db_path)
    with LOCK, connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS equipment_nodes_T (
          node_id TEXT PRIMARY KEY, label TEXT NOT NULL, kind TEXT NOT NULL,
          token_sha TEXT NOT NULL DEFAULT '', first_seen REAL,
          last_seen REAL, status TEXT NOT NULL DEFAULT 'UNKNOWN',
          detail TEXT NOT NULL DEFAULT '', metrics TEXT NOT NULL DEFAULT '{}',
          timeout_sec INTEGER NOT NULL DEFAULT 180,
          failures INTEGER NOT NULL DEFAULT 0, updated_at REAL
        );
        CREATE TABLE IF NOT EXISTS equipment_events_T (
          id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL,
          at REAL NOT NULL, old_state TEXT NOT NULL, state TEXT NOT NULL,
          detail TEXT NOT NULL, severity TEXT NOT NULL,
          acknowledged_at REAL, delivery TEXT NOT NULL DEFAULT 'PENDING',
          last_attempt REAL, attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS equipment_events_recent_T
          ON equipment_events_T(at DESC);
        """)
        for node_id, label, kind in KNOWN:
            con.execute(
                "INSERT OR IGNORE INTO equipment_nodes_T(node_id,label,kind,updated_at)"
                " VALUES(?,?,?,?)", (node_id, label, kind, time.time()))


def _change(con, node, status, detail):
    current = con.execute("SELECT status FROM equipment_nodes_T WHERE node_id=?",
                          (node,)).fetchone()
    if current is None:
        return
    old = current["status"]
    if old == status:
        con.execute("UPDATE equipment_nodes_T SET detail=?,updated_at=? WHERE node_id=?",
                    (detail[:180], time.time(), node))
        return
    con.execute("UPDATE equipment_nodes_T SET status=?,detail=?,updated_at=?"
                " WHERE node_id=?", (status, detail[:180], time.time(), node))
    # UNKNOWN and configuration-pending states do not produce false alarms.
    # A configured target failing twice must alarm even on its very first check.
    if status in ("OFFLINE", "DEGRADED"):
        severity = "critical" if status == "OFFLINE" else "warning"
    elif old in ("OFFLINE", "DEGRADED") and status == "ONLINE":
        severity = "recovered"
    else:
        return
    con.execute("INSERT INTO equipment_events_T(node_id,at,old_state,state,detail,severity)"
                " VALUES(?,?,?,?,?,?)",
                (node, time.time(), old, status, detail[:180], severity))


def _clean(metrics):
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be object")
    data = {}
    for k, v in metrics.items():
        if k in FIELDS and isinstance(v, (str, int, float, bool, type(None))):
            if len(str(v)) <= 120:
                data[k] = v
    if len(json.dumps(data)) > 2400:
        raise ValueError("metrics too long")
    return data


def _tls_ok(handler):
    try:
        loopback = ipaddress.ip_address(handler.client_address[0]).is_loopback
    except ValueError:
        loopback = False
    is_tls = isinstance(handler.connection, ssl.SSLSocket)
    trusted = (os.environ.get("CONTROLLER_TRUSTED_HTTPS_PROXY") == "1"
               and loopback and handler.headers.get("X-Forwarded-Proto", "").lower() == "https")
    local = loopback and not handler.headers.get("X-Forwarded-For") and not handler.headers.get("Forwarded")
    return is_tls or trusted or local


def heartbeat(node, token, metrics):
    data = _clean(metrics)
    if not IDENT.fullmatch(node) or not token:
        return False
    with LOCK, connect() as con:
        row = con.execute("SELECT token_sha,first_seen FROM equipment_nodes_T WHERE node_id=?",
                          (node,)).fetchone()
        if not row or not row["token_sha"] or not hmac.compare_digest(
                row["token_sha"], hashlib.sha256(token.encode()).hexdigest()):
            return False
        if not row["first_seen"]:
            con.execute("UPDATE equipment_nodes_T SET first_seen=? WHERE node_id=?",
                        (time.time(), node))
        state = "ONLINE"
        detail = "인증된 장비 상태 보고"
        if str(data.get("service_state", "")).upper() in ("DOWN", "FAILED", "STOPPED"):
            state, detail = "DEGRADED", "업무 서비스 정지 보고"
        elif str(data.get("nas_state", "")).upper() in ("DOWN", "FAILED"):
            state, detail = "DEGRADED", "NAS 접근 실패 보고"
        elif data.get("disk_free_bytes") is not None and int(data["disk_free_bytes"]) < 2 * 1024**3:
            state, detail = "DEGRADED", "장비 디스크 여유 2GB 미만"
        con.execute("UPDATE equipment_nodes_T SET last_seen=?,metrics=?,failures=0"
                    " WHERE node_id=?", (time.time(), json.dumps(data, ensure_ascii=False), node))
        _change(con, node, state, detail)
    return True


def register(node, label, kind, rotate=False, timeout_sec=180):
    if not IDENT.fullmatch(node) or kind not in ("controller", "pc", "collector", "nas", "network"):
        raise ValueError("invalid device ID/type")
    secret = secrets.token_urlsafe(32)
    with LOCK, connect() as con:
        existing = con.execute("SELECT token_sha FROM equipment_nodes_T WHERE node_id=?",
                               (node,)).fetchone()
        if existing and existing["token_sha"] and not rotate:
            raise ValueError("registered: rotate=true is required to change token")
        con.execute(
            "INSERT INTO equipment_nodes_T(node_id,label,kind,token_sha,status,timeout_sec,updated_at)"
            " VALUES(?,?,?,?,?,?,?) ON CONFLICT(node_id) DO UPDATE SET "
            "label=excluded.label,kind=excluded.kind,token_sha=excluded.token_sha,"
            "status='UNKNOWN',first_seen=NULL,last_seen=NULL,metrics='{}',"
            "timeout_sec=excluded.timeout_sec,updated_at=excluded.updated_at",
            (node, str(label)[:100], kind, hashlib.sha256(secret.encode()).hexdigest(),
             "UNKNOWN", min(900, max(90, int(timeout_sec))), time.time()))
    return secret  # once; never stored in plaintext


def _remote_mount_ok(path):
    """Linux: the resolved path must lie on a real remote SMB/NFS mount, not a local folder."""
    try:
        full = Path(path).resolve(strict=True)
        data = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        candidates = []
        for line in data:
            if " - " not in line:
                continue
            left, right = line.split(" - ", 1)
            left_parts, right_parts = left.split(), right.split()
            if len(left_parts) < 6 or not right_parts:
                continue
            mountpoint = Path(left_parts[4].replace("\\040", " "))
            if full == mountpoint or full.is_relative_to(mountpoint):
                candidates.append((len(str(mountpoint)), right_parts[0], left_parts[5]))
        if not candidates:
            return False
        _, fs_type, options = max(candidates)
        return fs_type in ("cifs", "smb3", "nfs", "nfs4") and "ro" not in options.split(",")
    except (OSError, ValueError):
        return False


def _probe(config):
    """Only operator-owned local JSON configuration controls targets; never user-supplied URLs."""
    host = str(config["host"])
    port = int(config.get("port", 445))
    if not (1 <= port <= 65535):
        return False, "포트 설정 오류"
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError:
        return False, "TCP 연결 실패"
    mount = config.get("mount_path")
    if config.get("kind") == "nas":
        if not mount:
            return None, "네트워크 접속 확인, NAS 실제 저장소 검증 미설정"
        if not _remote_mount_ok(mount):
            return False, "NAS 실제 원격 마운트 미확인"
        try:
            # Never create a test file if not a verified remote filesystem.
            mark = Path(mount) / (".bidguard_monitor_" + secrets.token_hex(6))
            with mark.open("xb") as f:
                f.write(b"BG-NAS-PROBE")
                f.flush()
                os.fsync(f.fileno())
            try:
                if mark.read_bytes() != b"BG-NAS-PROBE":
                    return False, "NAS 기록/읽기 불일치"
            finally:
                mark.unlink(missing_ok=True)
        except OSError:
            return False, "NAS 읽기/쓰기 확인 실패"
    return True, "실제 접속 및 저장 검사 통과" if mount else "TCP 서비스 접속 확인"


def check_once():
    targets_path = os.environ.get("CONTROLLER_MONITOR_TARGETS_FILE", "")
    targets = []
    if targets_path:
        try:
            targets = json.loads(Path(targets_path).read_text(encoding="utf-8"))
            if not isinstance(targets, list):
                targets = []
        except (OSError, ValueError):
            targets = []
    configured = set()
    with LOCK, connect() as con:
        # Actual live controller process, not merely the power state.
        row = con.execute("SELECT 1 FROM equipment_nodes_T WHERE node_id='BG-PC-001'").fetchone()
        if row:
            con.execute("UPDATE equipment_nodes_T SET first_seen=COALESCE(first_seen,?),"
                        "last_seen=?,metrics=? WHERE node_id='BG-PC-001'",
                        (time.time(), time.time(), '{"service_state":"RUNNING"}'))
            _change(con, "BG-PC-001", "ONLINE", "관제기 감시 루프 실행 중")
        for c in targets:
            node = c.get("node_id", "")
            if not IDENT.fullmatch(node):
                continue
            configured.add(node)
            row = con.execute("SELECT failures FROM equipment_nodes_T WHERE node_id=?",
                              (node,)).fetchone()
            if row is None:
                continue
            try:
                result, reason = _probe(c)
            except (OSError, ValueError, KeyError, TypeError):
                result, reason = False, "감시 대상 설정/점검 오류"
            if result is None:
                _change(con, node, "NOT_CONFIGURED", reason)
                continue
            failures = row["failures"] + 1 if result is False else 0
            con.execute("UPDATE equipment_nodes_T SET failures=?,last_seen=CASE WHEN ?"
                        " THEN ? ELSE last_seen END WHERE node_id=?",
                        (failures, int(result is True), time.time(), node))
            if result:
                _change(con, node, "ONLINE", reason)
            elif failures >= 2:
                _change(con, node, "OFFLINE", reason)
        # No direct target config means an unknown NAS, not an offline NAS.
        for row in con.execute("SELECT node_id,last_seen,timeout_sec,status FROM equipment_nodes_T"):
            if row["node_id"] in configured or row["node_id"] == "BG-PC-001":
                continue
            if row["last_seen"] and time.time() - row["last_seen"] > row["timeout_sec"]:
                _change(con, row["node_id"], "OFFLINE", "인증된 상태 보고 시간 초과")
    deliver_pending()


def deliver_pending():
    url = os.environ.get("NOTIFY_URL", "")
    with LOCK, connect() as con:
        evts = [dict(r) for r in con.execute(
            "SELECT id,node_id,state,detail,severity,attempts FROM equipment_events_T "
            "WHERE acknowledged_at IS NULL AND delivery!='DELIVERED' "
            "AND (last_attempt IS NULL OR last_attempt<?) "
            "ORDER BY id LIMIT 8", (time.time()-300,))]
    for e in evts:
        outcome = "NOT_CONFIGURED"
        if url:
            msg = {"type": "equipment_alert", "node_id": e["node_id"],
                   "state": e["state"], "severity": e["severity"],
                   "detail": e["detail"], "event_id": e["id"]}
            try:
                req = Request(url, data=json.dumps(msg, ensure_ascii=False).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(req, timeout=6) as response:
                    outcome = "DELIVERED" if 200 <= response.status < 300 else "FAILED"
            except Exception:
                outcome = "FAILED"
        with LOCK, connect() as con:
            con.execute("UPDATE equipment_events_T SET delivery=?,last_attempt=?,attempts=attempts+1"
                        " WHERE id=? AND delivery!='DELIVERED'",
                        (outcome, time.time(), e["id"]))


def snapshot():
    with LOCK, connect() as con:
        nodes = []
        for row in con.execute("SELECT node_id,label,kind,last_seen,status,detail,metrics,"
                               "timeout_sec,token_sha FROM equipment_nodes_T ORDER BY node_id"):
            d = dict(row)
            d["paired"] = bool(d.pop("token_sha"))
            d["metrics"] = json.loads(d["metrics"] or "{}")
            nodes.append(d)
        incidents = [dict(r) for r in con.execute(
            "SELECT id,node_id,at,old_state,state,detail,severity,acknowledged_at,"
            "delivery,attempts FROM equipment_events_T ORDER BY id DESC LIMIT 70")]
    return {"ok": True, "nodes": nodes, "events": incidents,
            "push_configured": bool(os.environ.get("NOTIFY_URL")),
            "checked_at": time.time()}


def acknowledge(event_id):
    with LOCK, connect() as con:
        con.execute("UPDATE equipment_events_T SET acknowledged_at=COALESCE(acknowledged_at,?)"
                    " WHERE id=?", (time.time(), int(event_id)))


def route(handler, method, path, query):
    if path == "/api/equipment/heartbeat" and method == "POST":
        if not _tls_ok(handler):
            handler._json(403, {"ok": False, "error": "TLS_REQUIRED"})
            return True
        b = handler._body()
        node = str(b.get("node_id") or "")
        if heartbeat(node, handler.headers.get("X-Equipment-Token", ""),
                     b.get("metrics") or {}):
            handler._json(200, {"ok": True})
        else:
            handler._json(403, {"ok": False, "error": "equipment identity"})
        return True
    if not path.startswith("/api/admin/equipment"):
        return False
    if not handler._admin():
        handler._json(403, {"ok": False, "error": "admin token"})
        return True
    if method == "GET" and path == "/api/admin/equipment/status":
        handler._json(200, snapshot())
        return True
    if method == "POST" and path == "/api/admin/equipment/register":
        if not _tls_ok(handler):
            handler._json(403, {"ok": False, "error": "TLS_REQUIRED"})
            return True
        b = handler._body()
        secret = register(str(b.get("node_id", "")), str(b.get("label", "")),
                          str(b.get("kind", "pc")), bool(b.get("rotate", False)))
        handler._json(200, {"ok": True, "node_id": b["node_id"], "token_once": secret})
        return True
    if method == "POST" and path == "/api/admin/equipment/ack":
        acknowledge(handler._body().get("event_id"))
        handler._json(200, {"ok": True})
        return True
    if method == "POST" and path == "/api/admin/equipment/check":
        check_once()
        handler._json(200, snapshot())
        return True
    handler._json(404, {"ok": False, "error": "not found"})
    return True


def start_background():
    def loop():
        while True:
            try:
                check_once()
            except Exception as e:
                # A failing monitor must remain observable to service logs.
                print("equipment monitor error:", type(e).__name__, flush=True)
            time.sleep(30)
    thread = threading.Thread(target=loop, name="equipment-watchdog", daemon=True)
    thread.start()
    return thread
