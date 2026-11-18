"""BIDGUARD 관제기 성과 원장 / 저장된 일별 통계 (2026-11-18).

Standard library only. This module never walks the source file folders and never
exports collected records to GitHub, Actions, or Vercel. Event identifiers make
retries idempotent. Times/days sent by collectors are explicitly KST work dates.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9가-힣 _./()-]{1,80}$")
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
KINDS = {"감정평가서", "매각물건명세서", "현황조사서", "매각결과", "기타"}
OUTCOMES = {"success", "failure", "duplicate"}
ALERTS = {
    "POWER_DISCONNECTED", "BATTERY_BELOW_20", "BATTERY_CRITICAL", "POWER_RESTORED",
    "LOGIN_LOST", "CAPTCHA", "ROBOT_CHECK", "UNEXPECTED_VIEWER",
    "NETWORK_SLOW", "NETWORK_LOST", "BREAK_STARTED", "BREAK_ENDED",
}
URGENT = {"POWER_DISCONNECTED", "BATTERY_CRITICAL", "LOGIN_LOST",
          "CAPTCHA", "ROBOT_CHECK", "UNEXPECTED_VIEWER", "NETWORK_LOST"}
QUESTIONS = {"daily_count", "court_count", "document_count", "battery_status",
             "urgent_appraisal", "login_incident", "collect_speed",
             "unexpected_viewer", "other"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS performance_events (
  collector TEXT NOT NULL, event_id TEXT NOT NULL, work_date TEXT NOT NULL,
  court TEXT NOT NULL, document_type TEXT NOT NULL, computer TEXT NOT NULL,
  outcome TEXT NOT NULL, duration_seconds REAL NOT NULL DEFAULT 0,
  case_no TEXT NOT NULL DEFAULT '', source_sha256 TEXT NOT NULL DEFAULT '',
  payload_hash TEXT NOT NULL, received_at TEXT NOT NULL,
  late_after_close INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(collector, event_id));
CREATE INDEX IF NOT EXISTS perf_events_work_date ON performance_events(work_date);
CREATE TABLE IF NOT EXISTS performance_daily (
  work_date TEXT NOT NULL, court TEXT NOT NULL, document_type TEXT NOT NULL,
  collector TEXT NOT NULL, computer TEXT NOT NULL,
  successes INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
  duplicates INTEGER NOT NULL DEFAULT 0, duration_seconds REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(work_date, court, document_type, collector, computer));
CREATE TABLE IF NOT EXISTS performance_closures (
  work_date TEXT PRIMARY KEY, closed_at TEXT NOT NULL,
  successes INTEGER NOT NULL, failures INTEGER NOT NULL, duplicates INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS performance_reconciliations (
  id INTEGER PRIMARY KEY AUTOINCREMENT, work_date TEXT NOT NULL,
  court TEXT NOT NULL, document_type TEXT NOT NULL, collector TEXT NOT NULL,
  computer TEXT NOT NULL, stored_count INTEGER NOT NULL, external_count INTEGER NOT NULL,
  difference INTEGER NOT NULL, checked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS performance_incidents (
  collector TEXT NOT NULL, event_id TEXT NOT NULL, at TEXT NOT NULL,
  code TEXT NOT NULL, computer TEXT NOT NULL DEFAULT '',
  battery_percent INTEGER, case_no TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(collector, event_id));
CREATE TABLE IF NOT EXISTS performance_questions (
  topic TEXT PRIMARY KEY, count INTEGER NOT NULL, last_asked_at TEXT NOT NULL);
"""

def init(con):
    con.executescript(SCHEMA)

def _today():
    return datetime.now(KST).date()

def _date(s):
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        raise ValueError("work_date must be YYYY-MM-DD (Asia/Seoul)")
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise ValueError("invalid work_date") from None
    if d > _today() + timedelta(days=1) or d < date(2000, 1, 1):
        raise ValueError("work_date outside supported range")
    return d.isoformat()

def _field(b, key, pattern, required=True):
    v = b.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or not pattern.fullmatch(v):
        raise ValueError("invalid " + key)
    return v

def _checked_collector(h, con, b):
    name = _field(b, "name", SAFE_ID)
    row = h._collector_ok(con, name)
    # Strong authentication is mandatory for the new write endpoints,
    # including when legacy endpoints still allow tokenless collectors.
    if row is None or not row["token_hash"]:
        return None
    return name

def record(con, name, b):
    event_id = _field(b, "event_id", SAFE_ID)
    work_date = _date(b.get("work_date"))
    court = _field(b, "court", SAFE_NAME)
    doc = str(b.get("document_type") or "")
    if doc not in KINDS:
        raise ValueError("unsupported document_type")
    computer = _field(b, "computer", SAFE_NAME)
    outcome = str(b.get("outcome") or "")
    if outcome not in OUTCOMES:
        raise ValueError("outcome must be success/failure/duplicate")
    duration = b.get("duration_seconds", 0)
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 <= duration <= 86400:
        raise ValueError("invalid duration_seconds")
    case_no, source_sha = "", ""
    if outcome == "success":
        if b.get("verification_status") != "VERIFIED":
            raise ValueError("success requires VERIFIED document")
        case_no = _field(b, "case_no", SAFE_NAME)
        source_sha = _field(b, "source_sha256", SHA256).lower()
    clean = (event_id, work_date, court, doc, computer, outcome, float(duration), case_no, source_sha)
    digest = hashlib.sha256(json.dumps(clean, ensure_ascii=False).encode()).hexdigest()
    found = con.execute("SELECT payload_hash FROM performance_events WHERE collector=? AND event_id=?",
                        (name, event_id)).fetchone()
    if found:
        if found["payload_hash"] != digest:
            raise ValueError("event_id reused with different content")
        return {"ok": True, "duplicate_delivery": True, "recorded": False}
    late = bool(con.execute("SELECT 1 FROM performance_closures WHERE work_date=?", (work_date,)).fetchone())
    con.execute("INSERT INTO performance_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, event_id, work_date, court, doc, computer, outcome, duration,
                 case_no, source_sha, digest, datetime.now(timezone.utc).isoformat(), int(late)))
    counts = (int(outcome == "success"), int(outcome == "failure"), int(outcome == "duplicate"))
    con.execute(
        "INSERT INTO performance_daily(work_date,court,document_type,collector,computer,"
        "successes,failures,duplicates,duration_seconds) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(work_date,court,document_type,collector,computer) DO UPDATE SET "
        "successes=successes+excluded.successes, failures=failures+excluded.failures, "
        "duplicates=duplicates+excluded.duplicates, duration_seconds=duration_seconds+excluded.duration_seconds",
        (work_date, court, doc, name, computer, *counts, duration))
    return {"ok": True, "recorded": True, "duplicate_delivery": False, "late_after_close": late}

def incident(con, name, b):
    eid = _field(b, "event_id", SAFE_ID)
    code = str(b.get("code") or "")
    if code not in ALERTS:
        raise ValueError("unsupported incident code")
    computer = _field(b, "computer", SAFE_NAME, required=False)
    case_no = _field(b, "case_no", SAFE_NAME, required=False)
    pct = b.get("battery_percent")
    if pct is not None and (isinstance(pct, bool) or not isinstance(pct, int) or not 0 <= pct <= 100):
        raise ValueError("invalid battery_percent")
    # Do not write free-form error text, screenshots, credentials or key material.
    stamp = datetime.now(timezone.utc).isoformat()
    cur = con.execute("INSERT OR IGNORE INTO performance_incidents VALUES(?,?,?,?,?,?,?)",
                      (name, eid, stamp, code, computer, pct, case_no))
    return {"ok": True, "recorded": cur.rowcount == 1, "notify": cur.rowcount == 1 and code in URGENT}

def _where(q):
    period = q.get("period", "day")
    end = _date(q.get("date") or _today().isoformat())
    d = date.fromisoformat(end)
    if period == "day":
        start = end
    elif period == "week":
        start = (d - timedelta(days=6)).isoformat()
    elif period == "month":
        start = d.replace(day=1).isoformat()
    elif period == "year":
        start = d.replace(month=1, day=1).isoformat()
    else:
        raise ValueError("period must be day/week/month/year")
    w, args = ["work_date BETWEEN ? AND ?"], [start, end]
    for key in ("court", "document_type", "collector", "computer"):
        if q.get(key):
            if key == "document_type" and q[key] not in KINDS:
                raise ValueError("unsupported document_type")
            if key != "document_type" and not SAFE_NAME.fullmatch(q[key]):
                raise ValueError("invalid " + key)
            w.append(key + "=?")
            args.append(q[key])
    return start, end, " WHERE " + " AND ".join(w), args

def summary(con, q):
    start, end, where, args = _where(q)
    sums = "COALESCE(SUM(successes),0) successes, COALESCE(SUM(failures),0) failures, " \
           "COALESCE(SUM(duplicates),0) duplicates, COALESCE(SUM(duration_seconds),0) duration_seconds"
    totals = dict(con.execute("SELECT " + sums + " FROM performance_daily" + where, args).fetchone())
    def group(group_by):
        sql = "SELECT " + group_by + " AS label, " + sums + " FROM performance_daily" + where + \
              " GROUP BY " + group_by + " ORDER BY " + group_by
        return [dict(r) for r in con.execute(sql, args)]
    count = con.execute("SELECT COUNT(*) FROM performance_daily" + where, args).fetchone()[0]
    close = con.execute("SELECT * FROM performance_closures WHERE work_date=?", (end,)).fetchone()
    late = con.execute("SELECT COUNT(*) FROM performance_events WHERE work_date=? AND late_after_close=1",
                       (end,)).fetchone()[0]
    return {"ok": True, "start": start, "end": end, "status": "reported" if count else "no_reports",
            "totals": totals, "daily": group("work_date"), "by_court": group("court"),
            "by_document_type": group("document_type"), "by_collector": group("collector"),
            "by_computer": group("computer"), "closure": dict(close) if close else None,
            "late_events": late}

def close_day(con, b):
    day = _date(b.get("work_date"))
    if day >= _today():
        raise ValueError("close only completed KST days")
    old = con.execute("SELECT * FROM performance_closures WHERE work_date=?", (day,)).fetchone()
    if old:
        return {"ok": True, "already_closed": True, "closure": dict(old)}
    s = con.execute("SELECT COALESCE(SUM(successes),0) a, COALESCE(SUM(failures),0) b, "
                    "COALESCE(SUM(duplicates),0) c FROM performance_daily WHERE work_date=?",
                    (day,)).fetchone()
    when = datetime.now(timezone.utc).isoformat()
    con.execute("INSERT INTO performance_closures VALUES(?,?,?,?,?)", (day, when, s["a"], s["b"], s["c"]))
    return {"ok": True, "already_closed": False, "closure": {"work_date": day, "closed_at": when,
             "successes": s["a"], "failures": s["b"], "duplicates": s["c"]}}

def reconcile(con, b):
    # Caller supplies an *independently checked* file count. This is not a file-scan API.
    day = _date(b.get("work_date"))
    court = _field(b, "court", SAFE_NAME)
    doc = str(b.get("document_type") or "")
    if doc not in KINDS:
        raise ValueError("unsupported document_type")
    collector = _field(b, "collector", SAFE_ID)
    computer = _field(b, "computer", SAFE_NAME)
    external = b.get("external_count")
    if isinstance(external, bool) or not isinstance(external, int) or not 0 <= external <= 10000000:
        raise ValueError("invalid external_count")
    r = con.execute("SELECT successes FROM performance_daily WHERE work_date=? AND court=? "
                    "AND document_type=? AND collector=? AND computer=?",
                    (day, court, doc, collector, computer)).fetchone()
    stored = r["successes"] if r else 0
    diff = external - stored
    con.execute("INSERT INTO performance_reconciliations(work_date,court,document_type,collector,"
                "computer,stored_count,external_count,difference,checked_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (day, court, doc, collector, computer, stored, external, diff,
                 datetime.now(timezone.utc).isoformat()))
    return {"ok": True, "matched": diff == 0, "stored": stored, "external": external, "difference": diff}

def route(h, method, path, q):
    if path not in {"/api/collector/performance", "/api/collector/incident",
                    "/api/admin/performance", "/api/admin/performance/close",
                    "/api/admin/performance/reconcile", "/api/admin/performance/question",
                    "/api/admin/performance/incidents", "/api/admin/performance/questions"}:
        return False
    is_collector = path.startswith("/api/collector/")
    if is_collector:
        if method != "POST":
            h._json(405, {"ok": False})
            return True
        b = h._body()
        with h.db_module() as con:
            name = _checked_collector(h, con, b)
            if name is None:
                h._json(403, {"ok": False, "error": "approved collector token required"})
                return True
            if path.endswith("/incident"):
                result = incident(con, name, b)
            else:
                result = record(con, name, b)
        if result.pop("notify", False):
            h.notify_module({"type": "collector_incident", "collector": name,
                             "code": b["code"], "case_no": result.get("case_no", "")})
        h._json(200, result)
        return True
    if not h._admin():
        h._json(403, {"ok": False, "error": "admin token"})
        return True
    if method == "GET" and path in {"/api/admin/performance", "/api/admin/performance/incidents",
                                    "/api/admin/performance/questions"}:
        with h.db_module() as con:
            if path.endswith("incidents"):
                rows = [dict(r) for r in con.execute("SELECT * FROM performance_incidents "
                        "ORDER BY at DESC LIMIT 50")]
                result = {"ok": True, "incidents": rows}
            elif path.endswith("questions"):
                rows = [dict(r) for r in con.execute("SELECT * FROM performance_questions "
                        "ORDER BY count DESC, topic LIMIT 50")]
                result = {"ok": True, "questions": rows}
            else:
                result = summary(con, q)
        h._json(200, result)
        return True
    if method == "POST":
        b = h._body()
        with h.db_module() as con:
            if path.endswith("/close"):
                result = close_day(con, b)
            elif path.endswith("/reconcile"):
                result = reconcile(con, b)
            elif path.endswith("/question"):
                topic = str(b.get("topic") or "")
                if topic not in QUESTIONS:
                    raise ValueError("unsupported question topic")
                con.execute("INSERT INTO performance_questions VALUES(?,1,?) "
                            "ON CONFLICT(topic) DO UPDATE SET count=count+1,last_asked_at=excluded.last_asked_at",
                            (topic, datetime.now(timezone.utc).isoformat()))
                result = {"ok": True, "topic": topic}
            else:
                h._json(405, {"ok": False})
                return True
        h._json(200, result)
        return True
    h._json(405, {"ok": False})
    return True
