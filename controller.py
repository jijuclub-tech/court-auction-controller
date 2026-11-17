#!/usr/bin/env python3
"""법원경매 관제기 (court-auction-controller) S-0.1.0-CONTROL

Python 3 standard library only (no packages to install). Runs on the Ubuntu
controller server:

    CONTROLLER_ADMIN_TOKEN=... python3 controller.py --host 0.0.0.0 --port 8770

What it does
  - Collectors (수집기) connect, get instructions, report results
    (same addresses the existing standalone collector already calls).
  - Between collection steps collectors send a heartbeat and ask for updates:
    site menu (자주가는 사이트 / 기타 사이트), fixed routes, AI ladder settings,
    approved program packages.
  - AI ladder rung 3 (관제기): POST /api/ladder/ask. The controller answers with
    its own AI when one is configured, otherwise the request waits for Grok's
    bot or the manager page; no answer -> the collector goes to rung 4.
  - Rung 4 (관리인, final): POST /api/escalations. Remote call (phone option)
    through NOTIFY_URL (BIDGUARD call channel); the manager answers on /admin:
    click on the screenshot, type ID/password/captcha text, skip, stop, retry.

Secrets: typed text from the manager is kept in memory only until the collector
fetches it; it is never written to the DB or logs. Tokens come from environment
variables. The manager page needs CONTROLLER_ADMIN_TOKEN.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

VERSION = "S-0.1.0-CONTROL"
DATA = Path(os.environ.get("CONTROLLER_DATA", "controller_data"))
DB_PATH = DATA / "controller.db"
SHOTS = DATA / "shots"
UPDATES = DATA / "updates"
ADMIN_TOKEN = os.environ.get("CONTROLLER_ADMIN_TOKEN", "")
BOT_TOKEN = os.environ.get("CONTROLLER_BOT_TOKEN", "")        # Grok bot: may answer rung-3 asks only
NOTIFY_URL = os.environ.get("NOTIFY_URL", "")                  # BIDGUARD call channel (phone)
PUBLIC_URL = os.environ.get("CONTROLLER_PUBLIC_URL", "")       # link put into phone notices
CONTROLLER_AI = os.environ.get("CONTROLLER_AI", "")            # e.g. gemini:gemini-2.5-pro
MAX_BODY = 6 * 1024 * 1024
CAP = 100                                                      # rows kept per topic (t451u)
SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
SECURITY = ("captcha", "recaptcha", "puzzle", "조각그림", "로봇", "robot", "보안문자", "자동입력방지")
DECISIONS = {"action", "type", "skip", "stop", "retry", "human", "escalate"}

_secret_text: dict = {}       # escalation/ask id -> typed text (memory only)
_lock = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS collectors (
  name TEXT PRIMARY KEY, type TEXT, status TEXT NOT NULL, token_hash TEXT NOT NULL DEFAULT '',
  first_seen TEXT, last_seen TEXT, last_state TEXT NOT NULL DEFAULT '{}', version TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS heartbeats (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, at TEXT NOT NULL, state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS instructions (
  instruction_id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
  body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, at TEXT NOT NULL, instruction_id TEXT,
  case_no TEXT, task_type TEXT, success INTEGER, needs_human INTEGER, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('ask','escalation')), name TEXT NOT NULL,
  level INTEGER NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
  goal TEXT NOT NULL, previous TEXT NOT NULL, input_mode TEXT NOT NULL, map_w INTEGER, map_h INTEGER,
  shot TEXT NOT NULL DEFAULT '', phone INTEGER NOT NULL DEFAULT 0,
  decision TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  answered_by TEXT NOT NULL DEFAULT '', answered_at TEXT, fetched_at TEXT);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS programs (
  version TEXT PRIMARY KEY, file TEXT NOT NULL, sha256 TEXT NOT NULL, approved INTEGER NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, who TEXT NOT NULL, what TEXT NOT NULL);
"""

DEFAULT_POLICY = {"goal": "법원경매 자료를 천천히·꾸준히 수집", "collector_nodes": []}


def db():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    SHOTS.mkdir(exist_ok=True)
    UPDATES.mkdir(exist_ok=True)
    with db() as con:
        con.executescript(SCHEMA)
        if con.execute("SELECT 1 FROM kv WHERE k='policy'").fetchone() is None:
            con.execute("INSERT INTO kv VALUES('policy',?)", (json.dumps(DEFAULT_POLICY, ensure_ascii=False),))
        if con.execute("SELECT 1 FROM kv WHERE k='rev'").fetchone() is None:
            con.execute("INSERT INTO kv VALUES('rev','0')")


def kv_get(con, k, default=None):
    r = con.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return json.loads(r["v"]) if r else default


def kv_set(con, k, v):
    con.execute("INSERT INTO kv VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v, ensure_ascii=False)))


def audit(con, who, what):
    con.execute("INSERT INTO audit(at,who,what) VALUES(?,?,?)", (now(), who, what[:400]))
    con.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 1000)")


def cap(con):
    con.execute("DELETE FROM heartbeats WHERE id NOT IN (SELECT id FROM heartbeats ORDER BY id DESC LIMIT ?)", (CAP * 10,))
    con.execute("DELETE FROM reports WHERE id NOT IN (SELECT id FROM reports ORDER BY id DESC LIMIT ?)", (CAP * 50,))
    old = con.execute("SELECT id, shot FROM calls WHERE id NOT IN (SELECT id FROM calls ORDER BY created_at DESC LIMIT ?)",
                      (CAP,)).fetchall()
    for r in old:
        _drop_shot(r["shot"])
        con.execute("DELETE FROM calls WHERE id=?", (r["id"],))


def _drop_shot(name):
    if name and SAFE_FILE.match(name):
        try:
            (SHOTS / name).unlink()
        except OSError:
            pass


def th(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest() if token else ""


def is_security(text):
    t = (text or "").lower()
    return any(m in t for m in SECURITY)


def notify(payload):
    """BIDGUARD call channel (same channel as member verification, per manager)."""
    if not NOTIFY_URL:
        return
    def send():
        try:
            req = Request(NOTIFY_URL, data=json.dumps(payload, ensure_ascii=False).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
            urlopen(req, timeout=10).read()
        except Exception:
            pass
    threading.Thread(target=send, daemon=True).start()


def controller_ai(call_body):
    """Optional own AI for rung 3 (CONTROLLER_AI=gemini:<model> and GEMINI_API_KEY)."""
    if not CONTROLLER_AI.startswith("gemini:") or not os.environ.get("GEMINI_API_KEY"):
        return None
    model = CONTROLLER_AI.split(":", 1)[1]
    mw, mh = (call_body.get("map_size") or [0, 0])[:2]
    prompt = ("You are the control center (rung 3) of a collector helper ladder. Lower AI rungs could not "
              "solve this step. Return ONE JSON object {action: MOVE|CLICK|TYPE|SHORTCUT|WAIT|ASK_USER|FINISH, "
              "x, y, text, shortcut, seconds, reason}. Map grid 10:1, x 0..%s, y 0..%s. If unsure, or captcha, "
              "login, payment or anything irreversible: ASK_USER. Never solve captchas. INPUT MODE %s. "
              "STEP: %s PREVIOUS: %s PROBLEM: %s" % (mw - 1, mh - 1, call_body.get("input_mode"),
                                                    call_body.get("goal", "")[:800], call_body.get("previous", "")[:400],
                                                    call_body.get("reason", "")[:300]))
    parts = [{"text": prompt}]
    if call_body.get("image_jpeg_b64"):
        parts.insert(0, {"inlineData": {"mimeType": "image/jpeg", "data": call_body["image_jpeg_b64"]}})
    req = Request("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
                  data=json.dumps({"contents": [{"parts": parts}],
                                   "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}).encode(),
                  headers={"Content-Type": "application/json", "x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                  method="POST")
    try:
        d = json.loads(urlopen(req, timeout=40).read())
        text = "".join(p.get("text", "") for p in ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        act = json.loads(text)
        if not isinstance(act, dict) or str(act.get("action", "")).upper() in ("", "ASK_USER"):
            return {"decision": "escalate", "note": "관제기 AI: " + str((act or {}).get("reason", ""))[:200]}
        return {"decision": "action", "action": act, "note": "관제기 AI " + model}
    except Exception as exc:
        return {"decision": "escalate", "note": "관제기 AI 오류 " + type(exc).__name__}


# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "court-auction-controller/" + VERSION

    def log_message(self, fmt, *args):  # no request bodies in logs
        print("%s %s" % (now(), fmt % args))

    # ---- helpers ----
    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        obj = json.loads(raw or b"{}")
        if not isinstance(obj, dict):
            raise ValueError("JSON object required")
        return obj

    def _admin(self):
        tok = self.headers.get("X-Admin-Token", "")
        return bool(ADMIN_TOKEN) and hmac.compare_digest(tok, ADMIN_TOKEN)

    def _bot(self):
        tok = self.headers.get("X-Bot-Token", "")
        return bool(BOT_TOKEN) and hmac.compare_digest(tok, BOT_TOKEN)

    def _collector_ok(self, con, name, need_approved=True):
        r = con.execute("SELECT * FROM collectors WHERE name=?", (name,)).fetchone()
        if r is None:
            return None
        if r["token_hash"] and not hmac.compare_digest(r["token_hash"], th(self.headers.get("X-Collector-Token", ""))):
            return None
        if need_approved and r["status"] != "approved":
            return None
        return r

    # ---- routing ----
    def do_GET(self):
        try:
            self._route("GET")
        except Exception as exc:
            self._json(500, {"ok": False, "error": type(exc).__name__})

    def do_POST(self):
        try:
            self._route("POST")
        except ValueError as exc:
            self._json(400, {"ok": False, "error": str(exc)[:200]})
        except Exception as exc:
            self._json(500, {"ok": False, "error": type(exc).__name__})

    def _route(self, method):
        u = urlparse(self.path)
        p, q = u.path.rstrip("/") or "/", {k: v[0] for k, v in parse_qs(u.query).items()}
        if method == "GET" and p == "/api/health":
            return self._json(200, {"ok": True, "version": VERSION, "time": now()})
        if method == "GET" and p in ("/admin", "/"):
            return self._page()
        if p.startswith("/api/admin/"):
            if not self._admin():
                return self._json(403, {"ok": False, "error": "admin token"})
            return self._admin_route(method, p, q)
        if p == "/api/bot/asks" and method == "GET":
            if not self._bot():
                return self._json(403, {"ok": False})
            with db() as con:
                rows = [self._call_row(r, with_shot=True) for r in
                        con.execute("SELECT * FROM calls WHERE kind='ask' AND status='pending' ORDER BY created_at")]
            return self._json(200, {"ok": True, "asks": rows})
        if p.startswith("/api/bot/answer/") and method == "POST":
            if not self._bot():
                return self._json(403, {"ok": False})
            return self._answer(p.rsplit("/", 1)[1], self._body(), who="grok_bot", only_kind="ask")
        if p == "/api/collector/connect" and method == "POST":
            return self._connect(self._body())
        if p == "/api/policy" and method == "GET":
            with db() as con:
                return self._json(200, kv_get(con, "policy", DEFAULT_POLICY))
        if p.startswith("/api/collector/instruction/") and method == "GET":
            return self._instruction(p.rsplit("/", 1)[1])
        if p == "/api/collector/report" and method == "POST":
            return self._report(self._body())
        if p == "/api/collector/heartbeat" and method == "POST":
            return self._heartbeat(self._body())
        if p == "/api/collector/updates" and method == "GET":
            return self._updates(q)
        if p.startswith("/api/collector/update_file/") and method == "GET":
            return self._update_file(p.rsplit("/", 1)[1], q)
        if p in ("/api/ladder/ask", "/api/escalations") and method == "POST":
            return self._new_call("ask" if p == "/api/ladder/ask" else "escalation", self._body())
        m = re.fullmatch(r"/api/(ladder/ask|escalations)/([A-Za-z0-9]+)", p)
        if m and method == "GET":
            return self._poll_call(m.group(2), q)
        return self._json(404, {"ok": False, "error": "not found"})

    # ---- existing collector API ----
    def _connect(self, b):
        name = str(b.get("name") or "").strip()[:60]
        if not name:
            raise ValueError("name required")
        with db() as con:
            r = con.execute("SELECT * FROM collectors WHERE name=?", (name,)).fetchone()
            if r is None:
                con.execute("INSERT INTO collectors(name,type,status,first_seen,last_seen) VALUES(?,?,?,?,?)",
                            (name, str(b.get("type") or "")[:20], "pending", now(), now()))
                audit(con, name, "connect: new collector waiting for approval")
                return self._json(200, {"ok": True, "result": {"registered": False, "status": "pending"}})
            if self._collector_ok(con, name, need_approved=False) is None:
                return self._json(403, {"ok": False, "result": {"registered": False}})
            con.execute("UPDATE collectors SET last_seen=?, type=? WHERE name=?", (now(), str(b.get("type") or r["type"])[:20], name))
            n = con.execute("SELECT COUNT(*) FROM instructions WHERE name=? AND status='pending'", (name,)).fetchone()[0]
            return self._json(200, {"ok": True, "result": {"registered": r["status"] == "approved",
                                                           "status": r["status"], "pending_instructions": n}})

    def _instruction(self, name):
        with db() as con:
            if self._collector_ok(con, name) is None:
                return self._json(403, {"ok": False, "instruction": None})
            r = con.execute("SELECT * FROM instructions WHERE name=? AND status='pending' ORDER BY created_at LIMIT 1",
                            (name,)).fetchone()
            if r is None:
                return self._json(200, {"ok": True, "instruction": None})
            con.execute("UPDATE instructions SET status='sent' WHERE instruction_id=?", (r["instruction_id"],))
            inst = json.loads(r["body"])
            inst["instruction_id"] = r["instruction_id"]
            return self._json(200, {"ok": True, "instruction": inst})

    def _report(self, b):
        name = str(b.get("name") or "")
        if not name or "case_no" not in b:
            raise ValueError("name and case_no required")
        with db() as con:
            if self._collector_ok(con, name) is None:
                return self._json(403, {"ok": False})
            con.execute("INSERT INTO reports(name,at,instruction_id,case_no,task_type,success,needs_human,body) "
                        "VALUES(?,?,?,?,?,?,?,?)", (name, now(), str(b.get("instruction_id") or ""), str(b["case_no"])[:90],
                                                    str(b.get("task_type") or "")[:40], 1 if b.get("success") else 0,
                                                    1 if b.get("needs_human") else 0, json.dumps(b, ensure_ascii=False)[:20000]))
            status = "done" if b.get("success") else ("needs_human" if b.get("needs_human") else "failed")
            if b.get("needs_human"):
                notify({"type": "collector_needs_human", "collector": name, "case_no": str(b["case_no"])[:90],
                        "error": str(b.get("error") or "")[:200]})
            cap(con)
        return self._json(200, {"ok": True, "result": {"task_status": status}})

    # ---- new: heartbeat / updates ----
    def _heartbeat(self, b):
        name = str(b.get("name") or "")
        with db() as con:
            if self._collector_ok(con, name) is None:
                return self._json(403, {"ok": False})
            st = json.dumps(b, ensure_ascii=False)[:4000]
            con.execute("UPDATE collectors SET last_seen=?, last_state=?, version=? WHERE name=?",
                        (now(), st, str(b.get("version") or "")[:40], name))
            con.execute("INSERT INTO heartbeats(name,at,state) VALUES(?,?,?)", (name, now(), st))
            cap(con)
        return self._json(200, {"ok": True})

    def _updates(self, q):
        name = q.get("name", "")
        with db() as con:
            r = self._collector_ok(con, name)
            if r is None:
                return self._json(403, {"ok": False})
            rev = str(kv_get(con, "rev", "0"))
            out = {"ok": True, "rev": rev}
            if q.get("rev", "") == rev:
                out["unchanged"] = True
            else:
                for k in ("sites", "routes", "ladder"):
                    v = kv_get(con, k)
                    if v is not None:
                        out[k] = v
                if r["type"] == "supply" and isinstance(out.get("ladder"), dict):
                    # 보급형에는 관제기 단계를 보내지 않음
                    lv = out["ladder"].get("levels", {})
                    lv["3"] = [x for x in lv.get("3", []) if x.get("kind") != "controller"]
            pr = con.execute("SELECT * FROM programs WHERE approved=1 ORDER BY created_at DESC LIMIT 1").fetchone()
            if pr:
                out["program"] = {"version": pr["version"], "file": pr["file"], "sha256": pr["sha256"], "approved": True}
        return self._json(200, out)

    def _update_file(self, fname, q):
        with db() as con:
            if self._collector_ok(con, q.get("name", "")) is None:
                return self._json(403, {"ok": False})
            ok = con.execute("SELECT 1 FROM programs WHERE file=? AND approved=1", (fname,)).fetchone()
        if not ok or not SAFE_FILE.match(fname) or not (UPDATES / fname).is_file():
            return self._json(404, {"ok": False})
        data = (UPDATES / fname).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- ladder rung 3 / rung 4 calls ----
    def _new_call(self, kind, b):
        name = str(b.get("name") or "")
        with db() as con:
            if self._collector_ok(con, name) is None:
                return self._json(403, {"ok": False})
        cid = uuid.uuid4().hex[:16]
        shot = ""
        img = b.get("image_jpeg_b64") or ""
        if img:
            raw = base64.b64decode(img, validate=True)
            if raw[:3] != b"\xff\xd8\xff":
                raise ValueError("screenshot must be JPEG")
            shot = cid + ".jpg"
            (SHOTS / shot).write_bytes(raw)
        ms = b.get("map_size") or [0, 0]
        row = (cid, kind, name, 3 if kind == "ask" else 4, now(), "pending", str(b.get("reason") or "")[:500],
               str(b.get("goal") or "")[:600], str(b.get("previous") or "")[:500], str(b.get("input_mode") or "")[:20],
               int(ms[0] or 0), int(ms[1] or 0), shot, 1 if b.get("phone") else 0)
        with db() as con:
            con.execute("INSERT INTO calls(id,kind,name,level,created_at,status,reason,goal,previous,input_mode,"
                        "map_w,map_h,shot,phone) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            audit(con, name, "%s %s: %s" % (kind, cid, row[6][:120]))
            cap(con)
        if kind == "ask":
            # security screens are the manager's: no controller AI
            if is_security(b.get("reason", "")):
                ans = {"decision": "escalate", "note": "보안 화면 — 관리인"}
            else:
                ans = controller_ai(b)
            if ans:
                self._store_answer(cid, ans, "controller_ai")
                return self._json(200, {"ok": True, "id": cid, "status": "answered", **ans})
            return self._json(200, {"ok": True, "id": cid, "status": "pending"})
        link = (PUBLIC_URL.rstrip("/") + "/admin#" + cid) if PUBLIC_URL else ""
        notify({"type": "collector_manager_call", "collector": name, "id": cid, "phone": bool(b.get("phone")),
                "reason": row[6][:200], "goal": row[7][:200], "link": link})
        return self._json(200, {"ok": True, "id": cid, "status": "pending"})

    def _poll_call(self, cid, q):
        with db() as con:
            r = con.execute("SELECT * FROM calls WHERE id=?", (cid,)).fetchone()
            if r is None or r["name"] != q.get("name", "") or self._collector_ok(con, r["name"]) is None:
                return self._json(404, {"ok": False})
            if r["status"] != "answered":
                return self._json(200, {"ok": True, "status": r["status"]})
            out = {"ok": True, "status": "answered", "decision": r["decision"], "note": r["note"]}
            if r["action"]:
                out["action"] = json.loads(r["action"])
            if r["decision"] == "type":
                with _lock:
                    text = _secret_text.pop(cid, None)
                if text is None:
                    out = {"ok": True, "status": "answered", "decision": "human",
                           "note": "입력값이 이미 전달됐거나 관제기가 재시작됨 — 현장 처리"}
                else:
                    out["text"] = text
            con.execute("UPDATE calls SET fetched_at=? WHERE id=?", (now(), cid))
            _drop_shot(r["shot"])
        return self._json(200, out)

    def _store_answer(self, cid, ans, who):
        d = str(ans.get("decision") or "")
        if d not in DECISIONS:
            raise ValueError("unknown decision")
        action = ans.get("action")
        if d == "action" and not isinstance(action, dict):
            raise ValueError("action object required")
        if d == "type":
            text = ans.get("text")
            if not isinstance(text, str) or not 1 <= len(text) <= 64:
                raise ValueError("text 1..64 required")
            with _lock:
                _secret_text[cid] = text          # memory only
        with db() as con:
            r = con.execute("SELECT status, kind FROM calls WHERE id=?", (cid,)).fetchone()
            if r is None:
                raise ValueError("unknown call")
            if r["status"] == "answered":
                raise ValueError("already answered")
            con.execute("UPDATE calls SET status='answered', decision=?, action=?, note=?, answered_by=?, answered_at=? "
                        "WHERE id=?", (d, json.dumps(action, ensure_ascii=False) if d == "action" else "",
                                       str(ans.get("note") or "")[:300], who, now(), cid))
            audit(con, who, "answer %s: %s" % (cid, d))   # never the typed text

    def _answer(self, cid, b, who, only_kind=None):
        with db() as con:
            r = con.execute("SELECT kind FROM calls WHERE id=?", (cid,)).fetchone()
        if r is None:
            return self._json(404, {"ok": False})
        if only_kind and r["kind"] != only_kind:
            return self._json(403, {"ok": False, "error": "rung 4 is the manager's"})
        self._store_answer(cid, b, who)
        return self._json(200, {"ok": True})

    @staticmethod
    def _call_row(r, with_shot=False):
        d = {k: r[k] for k in ("id", "kind", "name", "level", "created_at", "status", "reason", "goal", "previous",
                                "input_mode", "map_w", "map_h", "phone", "decision", "note", "answered_by")}
        if with_shot and r["shot"] and (SHOTS / r["shot"]).is_file():
            d["image_jpeg_b64"] = base64.b64encode((SHOTS / r["shot"]).read_bytes()).decode()
        return d

    # ---- manager (admin) API ----
    def _admin_route(self, method, p, q):
        if method == "GET" and p == "/api/admin/state":
            with db() as con:
                return self._json(200, {
                    "ok": True, "version": VERSION, "rev": kv_get(con, "rev", "0"),
                    "collectors": [dict(r) for r in con.execute("SELECT name,type,status,first_seen,last_seen,last_state,version FROM collectors ORDER BY name")],
                    "calls": [self._call_row(r, with_shot=r["status"] == "pending") for r in
                              con.execute("SELECT * FROM calls ORDER BY created_at DESC LIMIT 40")],
                    "instructions": [dict(r) for r in con.execute("SELECT * FROM instructions ORDER BY created_at DESC LIMIT 30")],
                    "reports": [dict(r) for r in con.execute("SELECT id,name,at,case_no,task_type,success,needs_human FROM reports ORDER BY id DESC LIMIT 30")],
                    "sites": kv_get(con, "sites"), "routes": kv_get(con, "routes"), "ladder": kv_get(con, "ladder"),
                    "policy": kv_get(con, "policy"),
                    "programs": [dict(r) for r in con.execute("SELECT * FROM programs ORDER BY created_at DESC")]})
        if method != "POST":
            return self._json(404, {"ok": False})
        b = self._body()
        if p == "/api/admin/collector":
            name = str(b.get("name") or "")[:60]
            status = b.get("status", "approved")
            if status not in ("approved", "pending", "blocked") or not name:
                raise ValueError("name and status")
            with db() as con:
                con.execute("INSERT INTO collectors(name,type,status,first_seen) VALUES(?,?,?,?) "
                            "ON CONFLICT(name) DO UPDATE SET status=excluded.status",
                            (name, str(b.get("type") or "control")[:20], status, now()))
                if "token" in b:
                    con.execute("UPDATE collectors SET token_hash=? WHERE name=?", (th(str(b["token"])), name))
                audit(con, "manager", "collector %s -> %s" % (name, status))
            return self._json(200, {"ok": True})
        if p.startswith("/api/admin/answer/"):
            return self._answer(p.rsplit("/", 1)[1], b, who="manager")
        if p == "/api/admin/bundle":
            with db() as con:
                for k in ("sites", "routes", "ladder"):
                    if k in b:
                        if k in ("sites", "routes") and not isinstance(b[k], list):
                            raise ValueError(k + " must be a list")
                        if k == "ladder" and not isinstance(b[k], dict):
                            raise ValueError("ladder must be an object")
                        kv_set(con, k, b[k])
                rev = int(kv_get(con, "rev", "0")) + 1
                kv_set(con, "rev", str(rev))
                audit(con, "manager", "bundle rev %s" % rev)
            return self._json(200, {"ok": True, "rev": str(rev)})
        if p == "/api/admin/policy":
            with db() as con:
                kv_set(con, "policy", b)
            return self._json(200, {"ok": True})
        if p == "/api/admin/instruction":
            name = str(b.get("name") or "")
            if not name:
                raise ValueError("name required")
            iid = uuid.uuid4().hex
            body = {k: b[k] for k in ("task_type", "cases", "save_path", "must_wait_seconds", "menu_steps") if k in b}
            with db() as con:
                con.execute("INSERT INTO instructions VALUES(?,?,?,?,?)", (iid, name, now(), "pending",
                                                                         json.dumps(body, ensure_ascii=False)))
                audit(con, "manager", "instruction %s for %s" % (iid[:8], name))
            return self._json(200, {"ok": True, "instruction_id": iid})
        if p == "/api/admin/program":
            fname, ver = str(b.get("file") or ""), str(b.get("version") or "")
            if not SAFE_FILE.match(fname) or not ver or not (UPDATES / fname).is_file():
                raise ValueError("put the package in controller_data/updates first; file and version required")
            digest = hashlib.sha256((UPDATES / fname).read_bytes()).hexdigest()
            with db() as con:
                con.execute("INSERT INTO programs VALUES(?,?,?,?,?) ON CONFLICT(version) DO UPDATE SET "
                            "file=excluded.file, sha256=excluded.sha256, approved=excluded.approved",
                            (ver, fname, digest, 1 if b.get("approved") else 0, now()))
                audit(con, "manager", "program %s approved=%s" % (ver, bool(b.get("approved"))))
            return self._json(200, {"ok": True, "sha256": digest})
        return self._json(404, {"ok": False})

    def _page(self):
        data = ADMIN_PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


ADMIN_PAGE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>관제기</title>
<style>
body{font-family:system-ui,'Malgun Gothic',sans-serif;margin:0;background:#f8fafc;color:#0f172a}
header{background:#0f172a;color:#fff;padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
main{padding:10px;max-width:1100px;margin:auto} section{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:10px;margin:10px 0}
h2{font-size:16px;margin:4px 0 8px} table{border-collapse:collapse;width:100%;font-size:13px} td,th{border-bottom:1px solid #eee;padding:4px;text-align:left}
.call{border:1px solid #fca5a5;background:#fff7f7;border-radius:8px;padding:8px;margin:8px 0}
.call img{max-width:100%;cursor:crosshair;border:1px solid #ccc} button{padding:6px 10px;margin:2px;border-radius:6px;border:1px solid #94a3b8;background:#fff}
button.main{background:#166534;color:#fff;border-color:#166534} textarea{width:100%;font-family:monospace;font-size:12px}
.muted{color:#64748b;font-size:12px} input{padding:5px}
</style></head><body>
<header><b>법원경매 관제기</b><span id="ver" class="muted"></span>
<input id="tok" type="password" placeholder="관리인 토큰" size="18"><button onclick="saveTok()">열기</button>
<span id="msg"></span></header>
<main>
<section><h2>관리인 호출 · 관제기 질문 (대기 중)</h2>
<p class="muted">화면을 누르면 그 자리를 클릭하라는 답이 됩니다. 아이디·암호·보안문자 답은 '글자 입력'으로 보냅니다(관제기에 저장되지 않음).</p>
<div id="calls"></div></section>
<section><h2>수집기</h2><table id="cols"></table>
<p><input id="newname" placeholder="수집기 이름"><select id="newtype"><option value="control">관제형</option><option value="supply">보급형</option></select>
<button onclick="approve(document.getElementById('newname').value,'approved',document.getElementById('newtype').value)">등록 승인</button></p></section>
<section><h2>지시서 보내기 (기존 관제형 지시)</h2>
<input id="iname" placeholder="수집기"><input id="itype" placeholder="작업 유형 (예: 감정평가서)">
<input id="icases" placeholder="사건번호, 쉼표로" size="40"><button onclick="sendInst()">보내기</button></section>
<section><h2>메뉴 · 정해진 경로 · AI 단계 (판 <span id="rev"></span>)</h2>
<p class="muted">sites: [{site_id,name,url,group:"frequent"|"other",note}] · routes: [{route_id,site_id,name,priority,steps:[{goal,start_level}]}] · ladder: {levels:{"1":[...],...}}. 저장하면 판이 올라가고, 수집기는 다음 점검 때 받습니다.</p>
<b>sites</b><textarea id="sites" rows="6"></textarea><b>routes</b><textarea id="routes" rows="8"></textarea>
<b>ladder</b><textarea id="ladder" rows="8"></textarea><button class="main" onclick="saveBundle()">저장 (새 판)</button></section>
<section><h2>최근 결과 보고</h2><table id="reps"></table></section>
</main>
<script>
let T=sessionStorage.getItem('ctok')||'';document.getElementById('tok').value=T;
function saveTok(){T=document.getElementById('tok').value;sessionStorage.setItem('ctok',T);load();}
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json','X-Admin-Token':T},body:body?JSON.stringify(body):undefined});
 const j=await r.json().catch(()=>({}));if(!r.ok){msg('오류: '+(j.error||r.status));throw new Error('x')}return j;}
function msg(t){document.getElementById('msg').textContent=t;setTimeout(()=>document.getElementById('msg').textContent='',4000)}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function load(){if(!T)return;const s=await api('/api/admin/state');
 document.getElementById('ver').textContent=s.version;document.getElementById('rev').textContent=s.rev;
 const pend=s.calls.filter(c=>c.status==='pending');
 document.getElementById('calls').innerHTML=pend.length?pend.map(callHtml).join(''):'<p class="muted">대기 중인 호출 없음</p>';
 document.getElementById('cols').innerHTML='<tr><th>이름</th><th>유형</th><th>상태</th><th>마지막</th><th>판</th><th>지금</th><th></th></tr>'+
  s.collectors.map(c=>{let st={};try{st=JSON.parse(c.last_state)}catch(e){}
  return `<tr><td>${esc(c.name)}</td><td>${c.type==='supply'?'보급형':'관제형'}</td><td>${esc(c.status)}</td><td>${esc((c.last_seen||'').slice(5,19))}</td><td>${esc(c.version)}</td><td>${esc(st.state||'')} ${esc(st.route||'')} ${st.step?esc(st.step)+'단계':''}</td>
  <td>${c.status!=='approved'?`<button onclick="approve('${esc(c.name)}','approved')">승인</button>`:`<button onclick="approve('${esc(c.name)}','blocked')">차단</button>`}</td></tr>`}).join('');
 document.getElementById('reps').innerHTML=s.reports.map(r=>`<tr><td>${esc(r.at.slice(5,19))}</td><td>${esc(r.name)}</td><td>${esc(r.case_no)}</td><td>${esc(r.task_type)}</td><td>${r.success?'성공':(r.needs_human?'사람 필요':'실패')}</td></tr>`).join('');
 for(const k of ['sites','routes','ladder']){const el=document.getElementById(k);if(document.activeElement!==el)el.value=s[k]?JSON.stringify(s[k],null,1):'';}
}
function callHtml(c){const who=c.kind==='ask'?'3단계 관제기 질문':'4단계 관리인 호출'+(c.phone?' (휴대폰)':'');
 return `<div class="call" id="c_${c.id}"><b>${who}</b> · ${esc(c.name)} · ${esc(c.created_at.slice(5,19))}<br>
 <b>문제:</b> ${esc(c.reason)}<br><b>단계:</b> ${esc(c.goal)}<br>
 ${c.image_jpeg_b64?`<img src="data:image/jpeg;base64,${c.image_jpeg_b64}" onclick="clickAt(event,'${c.id}',${c.map_w},${c.map_h})"><br>`:''}
 <span class="muted">클릭 위치: <span id="xy_${c.id}">-</span></span><br>
 <button class="main" onclick="sendClick('${c.id}')">이 자리 클릭</button>
 <input id="tx_${c.id}" type="password" placeholder="아이디·암호·보안문자 답" maxlength="64"><button onclick="sendType('${c.id}')">글자 입력</button>
 <button onclick="answer('${c.id}',{decision:'retry'})">다시 1단계부터</button><button onclick="answer('${c.id}',{decision:'skip'})">이 단계 건너뛰기</button>
 <button onclick="answer('${c.id}',{decision:'human'})">현장에서 처리</button><button onclick="answer('${c.id}',{decision:'stop'})">정지</button>
 ${c.kind==='ask'?`<button onclick="answer('${c.id}',{decision:'escalate'})">관리인에게 넘김</button>`:''}</div>`}
const picks={};
function clickAt(e,id,mw,mh){const im=e.target,r=im.getBoundingClientRect();
 const x=Math.floor((e.clientX-r.left)/r.width*mw),y=Math.floor((e.clientY-r.top)/r.height*mh);picks[id]={x,y};
 document.getElementById('xy_'+id).textContent=x+', '+y+' (10:1 격자)';}
async function sendClick(id){if(!picks[id])return msg('먼저 화면을 누르십시오');
 await answer(id,{decision:'action',action:{action:'CLICK',x:picks[id].x,y:picks[id].y,reason:'관리인 지정'}});}
async function sendType(id){const el=document.getElementById('tx_'+id);if(!el.value)return;const t=el.value;el.value='';
 await answer(id,{decision:'type',text:t});}
async function answer(id,b){await api('/api/admin/answer/'+id,b);msg('보냄');load();}
async function approve(name,status,type){if(!name)return;await api('/api/admin/collector',{name,status,type});load();}
async function sendInst(){const cases=document.getElementById('icases').value.split(',').map(s=>s.trim()).filter(Boolean);
 await api('/api/admin/instruction',{name:document.getElementById('iname').value,task_type:document.getElementById('itype').value,cases});msg('지시서 보냄');}
async function saveBundle(){const b={};for(const k of ['sites','routes','ladder']){const v=document.getElementById(k).value.trim();if(v){try{b[k]=JSON.parse(v)}catch(e){return msg(k+' JSON 오류')}}}
 const r=await api('/api/admin/bundle',b);msg('새 판 '+r.rev);load();}
load();setInterval(load,5000);
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="법원경매 관제기")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8770)
    a = ap.parse_args()
    init()
    if not ADMIN_TOKEN:
        print("경고: CONTROLLER_ADMIN_TOKEN 이 없어 관리인 화면(/admin)을 쓸 수 없습니다.")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("관제기 %s  http://%s:%s/admin" % (VERSION, a.host, a.port))
    srv.serve_forever()


if __name__ == "__main__":
    main()
