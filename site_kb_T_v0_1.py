"""BIDGUARD shared, append-only site knowledge API (T-v0.1, review branch).

Only code lives in GitHub. Actual KB documents/reports live in the controller's
existing private SQLite DB. Does not change collector/HID/legacy routes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import sqlite3
from datetime import datetime, timezone

import site_map_ingest

SITE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
DOC = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_./-]{0,159}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
BAD_KEYS = {"password", "passwd", "passphrase", "username", "user_id",
            "login_id", "personal_id", "resident_id", "phone", "phone_number",
            "email", "address", "secret", "private_key", "api_key", "apikey",
            "cookie", "set-cookie", "authorization", "access_token", "token",
            "credential", "session", "session_id", "account", "bank_account"}
BAD_VALUE = re.compile(
    r"(?i)(bearer\s+\S+|(?:password|passwd|api[-_ ]?key|authorization|"
    r"cookie|secret|token)\s*[:=]\s*\S+|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"(?<!\d)01[016789][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)|"
    r"(?<!\d)\d{6}[-\s]?[1-4]\d{6}(?!\d))"
)
MAX_REPORT = 8192
MAX_DOC = 32768


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def safe_scope(scope, site):
    if scope not in ("common", "site"):
        raise ValueError("scope must be common or site")
    if scope == "common":
        if site not in ("", "-"):
            raise ValueError("common scope cannot have a site")
        return scope, "-"
    if not isinstance(site, str) or not SITE.fullmatch(site):
        raise ValueError("site must be a lowercase DNS hostname")
    return scope, site


def safe_doc(value):
    if not isinstance(value, str) or not DOC.fullmatch(value):
        raise ValueError("invalid document ID")
    if any(x in ("", ".", "..") for x in value.split("/")) or "\\" in value:
        raise ValueError("unsafe document ID")
    return value


def scrub_check(value):
    """Deny sensitive fields and recognizable secret/PII text; do not log input."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str) or k.lower().replace("-", "_") in BAD_KEYS:
                raise ValueError("sensitive field rejected")
            scrub_check(v)
    elif isinstance(value, list):
        for v in value:
            scrub_check(v)
    elif isinstance(value, str) and BAD_VALUE.search(value):
        raise ValueError("sensitive content rejected")


def init(con, unused_data=None):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS kb_reports (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_id TEXT NOT NULL UNIQUE, scope TEXT NOT NULL, site TEXT NOT NULL,
      node TEXT NOT NULL, kind TEXT NOT NULL, symptom TEXT NOT NULL,
      body TEXT NOT NULL, payload_sha256 TEXT NOT NULL, observed_at TEXT NOT NULL,
      received_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
    );
    CREATE INDEX IF NOT EXISTS idx_kb_reports_site ON kb_reports(site,status,id);
    CREATE TABLE IF NOT EXISTS kb_docs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      scope TEXT NOT NULL, site TEXT NOT NULL, doc_id TEXT NOT NULL,
      revision INTEGER NOT NULL, body TEXT NOT NULL, sha256 TEXT NOT NULL,
      source_event TEXT NOT NULL, approved_by TEXT NOT NULL,
      approved_at TEXT NOT NULL,
      UNIQUE(scope,site,doc_id,revision)
    );
    CREATE TABLE IF NOT EXISTS kb_acks (
      event_id TEXT PRIMARY KEY, node TEXT NOT NULL, scope TEXT NOT NULL,
      site TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
      outcome TEXT NOT NULL, observed_at TEXT NOT NULL,
      received_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_kb_acks_site ON kb_acks(site,node,received_at);
    """)


def report(con, node, body):
    if not isinstance(body, dict):
        raise ValueError("object required")
    event = body.get("event_id")
    if not isinstance(event, str) or not IDENT.fullmatch(event):
        raise ValueError("invalid event_id")
    scope, site = safe_scope(body.get("scope"), body.get("site", ""))
    kind = body.get("kind")
    if kind not in ("success", "failure"):
        raise ValueError("kind must be success or failure")
    symptom = body.get("symptom")
    if not isinstance(symptom, str) or not symptom.strip() or len(symptom) > 160:
        raise ValueError("symptom required, max 160 chars")
    observed = body.get("observed_at")
    try:
        dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
    except (AttributeError, TypeError, ValueError):
        raise ValueError("observed_at must be ISO datetime with timezone")
    allowed = {"event_id", "scope", "site", "kind", "symptom", "cause",
               "recovery", "result", "evidence_sha256", "citation", "observed_at"}
    if set(body) - allowed:
        raise ValueError("unknown report field")
    evidence = body.get("evidence_sha256", "")
    if evidence and (not isinstance(evidence, str) or not SHA.fullmatch(evidence)):
        raise ValueError("invalid evidence hash")
    citation = body.get("citation", "")
    if citation and (not isinstance(citation, str) or not citation.startswith("kb:") or len(citation) > 300):
        raise ValueError("invalid citation")
    scrub_check(body)
    raw = canonical(body)
    if len(raw) > MAX_REPORT:
        raise ValueError("report too large")
    hx = digest(raw)
    old = con.execute("SELECT payload_sha256 FROM kb_reports WHERE event_id=?", (event,)).fetchone()
    if old:
        return (200, {"ok": True, "duplicate": True, "event_id": event}) if hmac.compare_digest(old[0], hx) else (
            409, {"ok": False, "error": "event_id conflict"})
    con.execute("""INSERT INTO kb_reports
      (event_id,scope,site,node,kind,symptom,body,payload_sha256,observed_at,received_at)
      VALUES (?,?,?,?,?,?,?,?,?,?)""",
      (event, scope, site, node, kind, symptom, raw.decode(), hx, observed, stamp()))
    return 201, {"ok": True, "event_id": event, "status": "pending_review", "sha256": hx}


def manifest(con, scope, site):
    scope, site = safe_scope(scope, site)
    rows = con.execute("""SELECT d.doc_id,d.revision,d.sha256,d.approved_at
      FROM kb_docs d WHERE d.scope=? AND d.site=?
      AND d.revision=(SELECT MAX(n.revision) FROM kb_docs n
       WHERE n.scope=d.scope AND n.site=d.site AND n.doc_id=d.doc_id)
      ORDER BY d.doc_id""", (scope, site)).fetchall()
    files = [{"path": r[0], "rev": r[1], "sha256": r[2], "approved_at": r[3],
              "knowledge_id": ("kb:common/" if scope == "common" else "kb:site/" + site + "/")
                              + r[0] + "@sha256:" + r[2]} for r in rows]
    version = digest(canonical([{"path": r["path"], "sha256": r["sha256"]} for r in files]))
    return {"ok": True, "scope": scope, "site": site if scope == "site" else None,
            "ready": bool(files), "version": version, "files": files,
            "warning": None if files else "NO_APPROVED_KNOWLEDGE"}


def approved_file(con, scope, site, doc_id, required_sha):
    scope, site = safe_scope(scope, site)
    doc_id = safe_doc(doc_id)
    if not isinstance(required_sha, str) or not SHA.fullmatch(required_sha):
        raise ValueError("sha256 pin required")
    row = con.execute("""SELECT revision,body,sha256 FROM kb_docs
      WHERE scope=? AND site=? AND doc_id=? AND sha256=?
      ORDER BY revision DESC LIMIT 1""",
      (scope, site, doc_id, required_sha)).fetchone()
    if not row:
        return 404, {"ok": False, "error": "approved document not found"}
    return 200, {"ok": True, "scope": scope, "site": site, "path": doc_id,
                 "revision": row[0], "sha256": row[2], "content": row[1]}


def approve(con, body):
    scope, site = safe_scope(body.get("scope"), body.get("site", ""))
    doc_id = safe_doc(body.get("path"))
    content = body.get("content")
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > MAX_DOC:
        raise ValueError("approved content must be 1..32768 bytes")
    source = body.get("source_event", "")
    if not isinstance(source, str) or not IDENT.fullmatch(source):
        raise ValueError("source_event required")
    scrub_check(content)
    if source.startswith("report:"):
        report_event = source.split(":", 1)[1]
        rec = con.execute("SELECT scope,site FROM kb_reports WHERE event_id=?", (report_event,)).fetchone()
        if not rec or (rec[0], rec[1]) != (scope, site):
            raise ValueError("report missing or from another scope/site")
    hx = digest(content.encode())
    current = con.execute("""SELECT revision,sha256 FROM kb_docs
      WHERE scope=? AND site=? AND doc_id=? ORDER BY revision DESC LIMIT 1""",
      (scope, site, doc_id)).fetchone()
    if current and hmac.compare_digest(current[1], hx):
        return 200, {"ok": True, "unchanged": True, "revision": current[0], "sha256": hx}
    revision = 1 if not current else current[0] + 1
    con.execute("""INSERT INTO kb_docs
      (scope,site,doc_id,revision,body,sha256,source_event,approved_by,approved_at)
      VALUES (?,?,?,?,?,?,?,?,?)""",
      (scope, site, doc_id, revision, content, hx, source, "authenticated_admin", stamp()))
    # Approval evidence is the new immutable kb_docs row; reports remain unchanged.
    return 201, {"ok": True, "path": doc_id, "revision": revision, "sha256": hx}


def acknowledgement(con, node, body):
    event = body.get("event_id")
    if not isinstance(event, str) or not IDENT.fullmatch(event):
        raise ValueError("invalid event_id")
    scope, site = safe_scope(body.get("scope"), body.get("site", ""))
    version = body.get("version")
    if not isinstance(version, str) or not SHA.fullmatch(version):
        raise ValueError("version hash required")
    outcome = body.get("outcome")
    if outcome not in ("received", "applied", "failed"):
        raise ValueError("invalid outcome")
    observed = body.get("observed_at")
    try:
        dt = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError("timezone required")
    except (AttributeError, TypeError, ValueError):
        raise ValueError("observed_at must be ISO datetime with timezone")
    scrub_check(body)
    old = con.execute("SELECT node,scope,site,manifest_sha256,outcome FROM kb_acks WHERE event_id=?",
                      (event,)).fetchone()
    target = (node, scope, site, version, outcome)
    if old:
        return (200, {"ok": True, "duplicate": True}) if tuple(old) == target else (
            409, {"ok": False, "error": "ack event_id conflict"})
    con.execute("""INSERT INTO kb_acks VALUES (?,?,?,?,?,?,?,?)""",
                (event, node, scope, site, version, outcome, observed, stamp()))
    return 201, {"ok": True, "outcome": outcome}


def status(con, site=""):
    if site and not SITE.fullmatch(site):
        raise ValueError("invalid site")
    where = " WHERE site=?" if site else ""
    args = (site,) if site else ()
    # Calculate approval from immutable versions; no UPDATE to historical reports.
    review = ("CASE WHEN EXISTS (SELECT 1 FROM kb_docs d "
              "WHERE d.source_event='report:'||r.event_id AND d.scope=r.scope "
              "AND d.site=r.site) THEN 'approved' ELSE 'pending' END")
    report_where = " WHERE r.site=?" if site else ""
    groups = [dict(r) for r in con.execute(
        "SELECT r.scope,r.site," + review + " AS status,COUNT(*) AS n "
        "FROM kb_reports r" + report_where +
        " GROUP BY r.scope,r.site,status ORDER BY r.site,status", args).fetchall()]
    recent = [dict(r) for r in con.execute(
        "SELECT r.event_id,r.scope,r.site,r.node,r.kind,r.symptom," + review +
        " AS status,r.observed_at,r.received_at FROM kb_reports r" + report_where +
        " ORDER BY r.id DESC LIMIT 50", args).fetchall()]
    acks = [dict(r) for r in con.execute(
        "SELECT node,scope,site,manifest_sha256,outcome,received_at "
        "FROM kb_acks" + where + " ORDER BY received_at DESC LIMIT 50", args).fetchall()]
    return {"ok": True, "groups": groups, "recent": recent, "acks": acks,
            "note": "knowledge is pending until human-approved; ACK is not application proof"}


def route(handler, method, path, query):
    known = {"/kb/manifest", "/kb/file", "/kb/report", "/kb/ack",
             "/kb/admin/status", "/kb/admin/approve"}
    if path not in known:
        return False
    if not site_map_ingest.ssl_ok(handler):
        handler._json(403, {"ok": False, "error": "HTTPS required"})
        return True
    if path.startswith("/kb/admin/"):
        if not handler._admin():
            handler._json(403, {"ok": False, "error": "admin required"})
            return True
    else:
        # A temporary legacy test secret must not survive the December 1 cutover.
        if (handler.headers.get("X-Collector-Token", "") == "test"
                and datetime.now(timezone.utc) >= datetime(2026, 11, 30, 15, 0, tzinfo=timezone.utc)):
            handler._json(403, {"ok": False, "error": "test key expired"})
            return True
        node = handler.headers.get("X-Collector-Name", "")
        with handler._site_map_db() as con:
            allowed = bool(node and handler._collector_ok(con, node))
        if not allowed:
            handler._json(403, {"ok": False, "error": "registered node and individual key required"})
            return True
    allowed_method = "POST" if path in ("/kb/report", "/kb/ack", "/kb/admin/approve") else "GET"
    if method != allowed_method:
        handler._json(405, {"ok": False, "error": "method not allowed"})
        return True
    if method == "POST":
        size = handler.headers.get("Content-Length", "")
        if not size.isdigit() or int(size) < 2:
            handler._json(411, {"ok": False, "error": "Content-Length required"})
            return True
        if int(size) > MAX_DOC * 2:
            handler._json(413, {"ok": False, "error": "body too large"})
            return True
    try:
        with handler._site_map_db() as con:
            if path == "/kb/manifest":
                result = (200, manifest(con, query.get("scope"), query.get("site", "")))
            elif path == "/kb/file":
                result = approved_file(con, query.get("scope"), query.get("site", ""),
                                       query.get("path"), query.get("sha256"))
            elif path == "/kb/admin/status":
                result = (200, status(con, query.get("site", "")))
            else:
                body = handler._body()
                if not isinstance(body, dict):
                    raise ValueError("object required")
                if path == "/kb/report":
                    result = report(con, node, body)
                elif path == "/kb/ack":
                    result = acknowledgement(con, node, body)
                else:
                    result = approve(con, body)
    except (ValueError, TypeError, UnicodeError, sqlite3.IntegrityError):
        result = (422, {"ok": False, "error": "invalid or sensitive KB request"})
    handler._json(*result)
    return True
