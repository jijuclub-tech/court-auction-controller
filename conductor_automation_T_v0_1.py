"""BIDGUARD 지휘자 반복 업무 자동화 (T-v0.1, 검수 브랜치, 이슈 #43).

규칙 (정한영 2026-11-23 04:21 KST):
  - KST 날짜의 '일'이 홀수면 지휘자 봇(conductor), 짝수면 관제기 자동화(controller)가 한다.
  - 정한영 지시는 짝수일에도 지휘자가 바로 처리한다 (exemption).
  - 관제기 자동화가 실제로 동작하는 것이 확인(verified)되기 전까지는 짝수일에도 지휘자가
    대신 하고 conductor_fallback 으로 기록한다. 이때 관제기는 같은 일을 '그림자(shadow)'로
    돌려 결과만 따로 남기고, 봇에게 지시를 보내지 않는다 (검증 자료).
  - 홀수·짝수 성과(성공률, 시간, AI 단계, 놓친 일)를 비교 리포트로 만든다.

GitHub 에는 코드만 둔다. 봇 명단·보고 JSON·뉴스 제출물·낙찰가 자료는 관제기 서버의
비공개 운영 폴더(CONDUCTOR_OPS_DIR, 기본 <CONTROLLER_DATA>/ops)에만 둔다.
파이썬 표준 라이브러리만 쓰며 유료 서비스·외부 AI 를 부르지 않는다.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import statistics
import threading
import time
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

VERSION = "T-v0.1-CONDUCTOR-AUTO"
KST = timezone(timedelta(hours=9))  # 한국은 서머타임이 없어 고정 +09:00
ACTORS = ("conductor", "controller", "conductor_fallback")
MODES = ("live", "shadow", "meta")
TEST_KEY_CUTOFF = datetime(2026, 12, 1, 0, 0, tzinfo=KST)  # KEY-UPGRADE-AT-2026-12-01T00:00+09:00
GRACE_HOURS = 6          # 이보다 오래 지난 일정은 '놓친 일'로만 남고 뒤늦게 돌리지 않는다
BACKOFF_MIN = (5, 15, 45)
STUCK_MIN = 30           # 시작만 하고 끝 기록이 없는 실행(서버 재시작 등)은 이 뒤 실패로 본다
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
TASK = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
MAX_ORDER = 16 * 1024

# ---------------------------------------------------------------------------
# 작업 목록. schedule 시각은 모두 KST.
#   daily  : {"kind":"daily","at":"18:00"} (+선택 "from"/"to" 날짜 범위)
#   every  : {"kind":"every","hours":2,"minute":5}
#   weekly : {"kind":"weekly","weekday":6,"at":"09:00"}  (월=0 … 일=6)
DEFAULT_TASKS = [
    {"task": "ai_usage_compare", "title": "18:00 AI 사용 줄이기 비교",
     "schedule": {"kind": "daily", "at": "18:00"}, "retries": 2},
    {"task": "bot_health_check", "title": "2시간마다 봇 점검",
     "schedule": {"kind": "every", "hours": 2, "minute": 5}, "retries": 1},
    {"task": "regional_news_assign", "title": "일요일 지역 뉴스 배정",
     "schedule": {"kind": "weekly", "weekday": 6, "at": "09:00"}, "retries": 2},
    {"task": "regional_news_score", "title": "일요일 지역 뉴스 채점·재작업·요약",
     "schedule": {"kind": "weekly", "weekday": 6, "at": "20:00"}, "retries": 2},
    {"task": "world_news_assign", "title": "월요일 세계 부동산 뉴스·언론사 배정 (#42)",
     "schedule": {"kind": "weekly", "weekday": 0, "at": "09:00"}, "retries": 2},
    {"task": "world_news_score", "title": "월요일 세계 부동산 뉴스 채점·재작업·요약 (#42)",
     "schedule": {"kind": "weekly", "weekday": 0, "at": "20:00"}, "retries": 2},
    {"task": "auction_price_fill", "title": "매각물건 낙찰가 채우기 (11/23~27)",
     "schedule": {"kind": "daily", "at": "10:00", "from": "2026-11-23", "to": "2026-11-27"},
     "retries": 2},
    {"task": "auction_price_fill_pm", "title": "매각물건 낙찰가 채우기 오후 (11/23~27)",
     "schedule": {"kind": "daily", "at": "16:00", "from": "2026-11-23", "to": "2026-11-27"},
     "retries": 2, "handler": "auction_price_fill"},
    {"task": "issue_reply_digest", "title": "GitHub 이슈 답변 확인·봇 전달 목록",
     "schedule": {"kind": "every", "hours": 1, "minute": 20}, "retries": 1},
    {"task": "parity_compare_report", "title": "홀짝 성과 비교 리포트",
     "schedule": {"kind": "daily", "at": "23:40"}, "retries": 1, "meta": True},
]


def now_kst() -> datetime:
    return datetime.now(KST)


def iso(dt: datetime) -> str:
    return dt.astimezone(KST).isoformat(timespec="seconds")


def parse_dt(text) -> datetime:
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone required")
    return dt.astimezone(KST)


def parity(day: date) -> str:
    return "odd" if day.day % 2 else "even"


def slot_key(dt: datetime) -> str:
    return dt.astimezone(KST).strftime("%Y-%m-%dT%H:%M")


def canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


# ---------------------------------------------------------------------------
# DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS auto_tasks (
  task TEXT PRIMARY KEY, title TEXT NOT NULL, schedule TEXT NOT NULL,
  handler TEXT NOT NULL, retries INTEGER NOT NULL DEFAULT 1,
  enabled INTEGER NOT NULL DEFAULT 1, verified INTEGER NOT NULL DEFAULT 0,
  meta INTEGER NOT NULL DEFAULT 0, verified_at TEXT, verified_note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS auto_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  date TEXT NOT NULL, parity TEXT NOT NULL, actor TEXT NOT NULL, mode TEXT NOT NULL,
  task TEXT NOT NULL, slot TEXT NOT NULL, attempt INTEGER NOT NULL,
  started TEXT NOT NULL, finished TEXT, ok INTEGER,
  items INTEGER NOT NULL DEFAULT 0, errors TEXT NOT NULL DEFAULT '[]',
  ai_steps INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
  expected_actor TEXT NOT NULL DEFAULT '', rule_ok INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'scheduler',
  UNIQUE(task, slot, mode, actor, attempt));
CREATE INDEX IF NOT EXISTS idx_auto_runs_date ON auto_runs(date, task);
CREATE TABLE IF NOT EXISTS auto_exemptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, task TEXT NOT NULL,
  note TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_auto_exempt ON auto_exemptions(date, task);
CREATE TABLE IF NOT EXISTS auto_state (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bot_orders (
  order_id TEXT PRIMARY KEY, bot TEXT NOT NULL, task TEXT NOT NULL, slot TEXT NOT NULL,
  kind TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  body TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  pulled_at TEXT, acked_at TEXT, outcome TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
  UNIQUE(bot, task, slot, kind));
CREATE INDEX IF NOT EXISTS idx_bot_orders_bot ON bot_orders(bot, status, created_at);
"""


def init(con, data_dir=None):
    con.executescript(SCHEMA)
    for t in DEFAULT_TASKS:
        con.execute("""INSERT OR IGNORE INTO auto_tasks(task,title,schedule,handler,retries,meta)
                       VALUES(?,?,?,?,?,?)""",
                    (t["task"], t["title"], json.dumps(t["schedule"]), t.get("handler", t["task"]),
                     int(t.get("retries", 1)), 1 if t.get("meta") else 0))
    if state_get(con, "installed_on") is None:
        state_set(con, "installed_on", now_kst().date().isoformat())


def tasks(con):
    cur = con.cursor()
    cur.row_factory = sqlite3.Row
    out = []
    for r in cur.execute("SELECT * FROM auto_tasks ORDER BY task"):
        d = dict(r)
        d["schedule"] = json.loads(d["schedule"])
        out.append(d)
    return out


def task_row(con, task):
    for t in tasks(con):
        if t["task"] == task:
            return t
    return None


def state_get(con, k, default=None):
    r = con.execute("SELECT v FROM auto_state WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def state_set(con, k, v):
    con.execute("INSERT INTO auto_state VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v, ensure_ascii=False)))


# ---------------------------------------------------------------------------
# 일정 계산
def _hm(text):
    h, m = str(text).split(":")
    h, m = int(h), int(m)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("bad time")
    return h, m


def slots_between(spec, start: datetime, end: datetime):
    """[start, end] 안의 KST 일정 시각 목록."""
    start, end = start.astimezone(KST), end.astimezone(KST)
    kind = spec.get("kind")
    lo = date.fromisoformat(spec["from"]) if spec.get("from") else None
    hi = date.fromisoformat(spec["to"]) if spec.get("to") else None
    out = []
    day = start.date()
    while day <= end.date():
        if (lo is None or day >= lo) and (hi is None or day <= hi):
            cands = []
            if kind == "daily":
                h, m = _hm(spec["at"])
                cands = [(h, m)]
            elif kind == "weekly":
                if day.weekday() == int(spec["weekday"]):
                    cands = [_hm(spec["at"])]
            elif kind == "every":
                step = int(spec["hours"])
                if not 1 <= step <= 24:
                    raise ValueError("every.hours must be 1..24")
                cands = [(h, int(spec.get("minute", 0))) for h in range(0, 24, step)]
            else:
                raise ValueError("unknown schedule kind")
            for h, m in cands:
                dt = datetime(day.year, day.month, day.day, h, m, tzinfo=KST)
                if start <= dt <= end:
                    out.append(dt)
        day += timedelta(days=1)
    return out


def exemption(con, day: date, task: str):
    r = con.execute("""SELECT note FROM auto_exemptions WHERE date=? AND task IN (?, '*')
                       ORDER BY id DESC LIMIT 1""", (day.isoformat(), task)).fetchone()
    return r[0] if r else None


def decide(con, task: dict, day: date):
    """그날 그 작업을 누가 하는가 -> (expected_actor, controller_mode or None, reason)."""
    if task["meta"]:
        return "controller", "meta", "meta task (홀짝 무관)"
    if parity(day) == "odd":
        return "conductor", None, "홀수일: 지휘자 봇"
    note = exemption(con, day, task["task"])
    if note is not None:
        return "conductor", None, "정한영 지시 예외: " + note[:120]
    if not task["verified"]:
        return "conductor_fallback", "shadow", "관제기 자동화 실기 확인 전: 지휘자 대행, 관제기는 그림자 실행"
    return "controller", "live", "짝수일: 관제기 자동화"


# ---------------------------------------------------------------------------
# 운영 폴더(비공개) 입출력
def ops_dir(data_dir=None) -> Path:
    env = os.environ.get("CONDUCTOR_OPS_DIR")
    if env:
        return Path(env)
    base = Path(data_dir) if data_dir else Path(os.environ.get("CONTROLLER_DATA", "controller_data"))
    return base / "ops"


def read_json(path: Path, what: str):
    if not path.exists():
        raise FileNotFoundError(what + " 없음: " + str(path.name))
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def roster(ops: Path):
    data = read_json(ops / "bots.json", "봇 명단 bots.json")
    bots = data.get("bots") if isinstance(data, dict) else data
    if not isinstance(bots, list) or not bots:
        raise ValueError("bots.json 에 bots 목록이 없습니다")
    names = []
    for b in bots:
        n = b.get("name") if isinstance(b, dict) else b
        if not isinstance(n, str) or not NAME.fullmatch(n):
            raise ValueError("봇 이름 형식 오류")
        if n not in names:
            names.append(n)
    return names


class Ctx:
    """처리기에 넘기는 실행 문맥. shadow 면 지시를 큐에 넣지 않고 미리보기로만 남긴다."""

    def __init__(self, con, ops: Path, task: str, slot: datetime, mode: str, now: datetime,
                 fetch=None):
        self.con, self.ops, self.task, self.slot, self.mode, self.now = con, ops, task, slot, mode, now
        self.day = slot.date()
        sub = "shadow" if mode == "shadow" else "live"
        self.out = ops / "out" / sub / task / slot_key(slot).replace(":", "")
        self.orders_preview = []
        self.ai_steps = 0          # 이 처리기들은 AI 를 쓰지 않는다. 쓰게 되면 여기서 센다.
        self.fetch = fetch or http_get_json

    def state_key(self, k):
        return ("shadow:" if self.mode == "shadow" else "") + k

    def emit(self, bot, kind, body, hours=24):
        order = {"bot": bot, "task": self.task, "slot": slot_key(self.slot), "kind": kind,
                 "body": body}
        if self.mode != "live":
            self.orders_preview.append(order)
            return None
        return enqueue_order(self.con, bot, self.task, slot_key(self.slot), kind, body,
                             self.now + timedelta(hours=hours), self.now)


# ---------------------------------------------------------------------------
# 봇 지시 큐 (관제기는 Grok 채팅에 직접 못 보내므로, 봇이 시작할 때 pull)
def enqueue_order(con, bot, task, slot, kind, body, expires: datetime, at: datetime):
    if not NAME.fullmatch(bot):
        raise ValueError("bad bot name")
    raw = canonical(body)
    if len(raw) > MAX_ORDER:
        raise ValueError("order too large")
    sha = hashlib.sha256(raw).hexdigest()
    oid = "ord-" + hashlib.sha256(("%s|%s|%s|%s" % (bot, task, slot, kind)).encode()).hexdigest()[:24]
    old = con.execute("SELECT sha256 FROM bot_orders WHERE order_id=?", (oid,)).fetchone()
    if old:
        if not hmac.compare_digest(old[0], sha):
            # 재시도에서 내용이 바뀌면 아직 안 가져간 지시만 갱신
            con.execute("""UPDATE bot_orders SET body=?, sha256=?, expires_at=?
                           WHERE order_id=? AND status='queued'""",
                        (raw.decode(), sha, iso(expires), oid))
        return oid
    con.execute("""INSERT INTO bot_orders(order_id,bot,task,slot,kind,created_at,expires_at,body,sha256)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (oid, bot, task, slot, kind, iso(at), iso(expires), raw.decode(), sha))
    return oid


def pull_orders(con, bot, at: datetime):
    if not NAME.fullmatch(bot or ""):
        raise ValueError("bad bot name")
    rows = con.execute("""SELECT order_id,task,slot,kind,created_at,expires_at,body,sha256,status
                          FROM bot_orders WHERE bot=? AND status IN ('queued','pulled')
                          AND expires_at>? ORDER BY created_at, order_id""",
                       (bot, iso(at))).fetchall()
    out = []
    for r in rows:
        out.append({"order_id": r[0], "task": r[1], "slot": r[2], "kind": r[3], "created_at": r[4],
                    "expires_at": r[5], "body": json.loads(r[6]), "sha256": r[7]})
    if out:
        con.executemany("UPDATE bot_orders SET status='pulled', pulled_at=COALESCE(pulled_at,?) "
                        "WHERE order_id=? AND status='queued'", [(iso(at), o["order_id"]) for o in out])
    return out


def ack_order(con, bot, body, at: datetime):
    oid = body.get("order_id")
    outcome = body.get("outcome")
    if outcome not in ("received", "done", "failed"):
        raise ValueError("outcome must be received|done|failed")
    note = str(body.get("note", ""))[:300]
    r = con.execute("SELECT bot,status,outcome FROM bot_orders WHERE order_id=?", (oid,)).fetchone()
    if not r or r[0] != bot:
        return 404, {"ok": False, "error": "order not found for this bot"}
    if r[2] in ("done", "failed"):
        return (200, {"ok": True, "duplicate": True}) if r[2] == outcome else (
            409, {"ok": False, "error": "order already closed"})
    status = "acked" if outcome == "received" else outcome
    con.execute("UPDATE bot_orders SET status=?, outcome=?, note=?, acked_at=? WHERE order_id=?",
                (status, outcome, note, iso(at), oid))
    return 200, {"ok": True, "status": status}


def export_queue_files(con, ops: Path, at: datetime):
    """공용 폴더를 읽는 봇을 위한 사본: ops/queue/<bot>.json (현재 유효 지시 전체)."""
    rows = con.execute("""SELECT bot,order_id,task,slot,kind,expires_at,body,sha256 FROM bot_orders
                          WHERE status IN ('queued','pulled') AND expires_at>? ORDER BY bot,created_at""",
                       (iso(at),)).fetchall()
    per = {}
    for r in rows:
        per.setdefault(r[0], []).append({"order_id": r[1], "task": r[2], "slot": r[3], "kind": r[4],
                                         "expires_at": r[5], "body": json.loads(r[6]), "sha256": r[7]})
    qdir = ops / "queue"
    qdir.mkdir(parents=True, exist_ok=True)
    for bot, orders in per.items():
        write_json(qdir / (bot + ".json"), {"bot": bot, "generated_at": iso(at), "orders": orders,
                                            "ack": "POST /auto/bot/ack 로 order_id 별 received/done/failed"})
    for f in qdir.glob("*.json"):
        if f.stem not in per:
            write_json(f, {"bot": f.stem, "generated_at": iso(at), "orders": []})
    return len(rows)


# ---------------------------------------------------------------------------
# 1. 18:00 AI 사용 줄이기 비교
def h_ai_usage_compare(ctx: Ctx):
    bots = roster(ctx.ops)
    today = ctx.day.isoformat()
    prev = (ctx.day - timedelta(days=1)).isoformat()
    rows, errors = [], []
    for b in bots:
        cur = ctx.ops / "ai_usage" / today / (b + ".json")
        old = ctx.ops / "ai_usage" / prev / (b + ".json")
        rec = {"bot": b, "reported": cur.exists(), "ai_calls": None, "prev_ai_calls": None,
               "reduction_pct": None, "penalties": []}
        try:
            if cur.exists():
                rec["ai_calls"] = int(json.loads(cur.read_text(encoding="utf-8"))["ai_calls"])
            if old.exists():
                rec["prev_ai_calls"] = int(json.loads(old.read_text(encoding="utf-8"))["ai_calls"])
        except (ValueError, KeyError, TypeError) as exc:
            errors.append("%s 보고 형식 오류(%s)" % (b, type(exc).__name__))
            rec["reported"] = False
            rec["ai_calls"] = None
        if not rec["reported"]:
            rec["penalties"].append("미보고")
        elif rec["prev_ai_calls"] is not None:
            p = rec["prev_ai_calls"]
            rec["reduction_pct"] = round((p - rec["ai_calls"]) / p * 100, 1) if p > 0 else 0.0
            if rec["ai_calls"] >= p:
                rec["penalties"].append("미감소")
        rows.append(rec)
    ranked = [r for r in rows if r["reduction_pct"] is not None]
    if len(ranked) >= 2:
        worst = min(r["reduction_pct"] for r in ranked)
        for r in ranked:
            if r["reduction_pct"] == worst:
                r["penalties"].append("최하위")
    penalized = [r for r in rows if r["penalties"]]
    summary = {"date": today, "bots": len(bots), "reported": sum(r["reported"] for r in rows),
               "penalized": len(penalized), "rows": rows}
    write_json(ctx.out / "summary.json", summary)
    (ctx.out / "chart.svg").write_text(bar_svg(
        "AI 사용 감소율 %s (%%)" % today,
        [(r["bot"], r["reduction_pct"] or 0.0) for r in sorted(ranked, key=lambda x: -x["reduction_pct"])]),
        encoding="utf-8")
    for r in penalized:
        ctx.emit(r["bot"], "penalty_notice", {"date": today, "penalties": r["penalties"],
                                              "ai_calls": r["ai_calls"], "prev_ai_calls": r["prev_ai_calls"],
                                              "todo": "다음 보고에서 AI 호출을 줄이고 ai_usage JSON 을 제때 올리세요."})
    return {"items": len(rows), "errors": errors,
            "notes": "보고 %d/%d, 벌점 %d" % (summary["reported"], len(bots), len(penalized))}


def bar_svg(title, pairs, width=720):
    """표준 라이브러리만으로 만드는 가로 막대 그래프."""
    h_row, pad = 18, 160
    height = 40 + h_row * max(1, len(pairs))
    vmax = max([abs(v) for _, v in pairs] + [1.0])
    span = width - pad - 60
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="sans-serif" '
             'font-size="12">' % (width, height),
             '<text x="8" y="20" font-size="14">%s</text>' % esc(title)]
    zero = pad + span / 2 if any(v < 0 for _, v in pairs) else pad
    scale = (span / 2 if zero != pad else span) / vmax
    for i, (label, v) in enumerate(pairs):
        y = 32 + i * h_row
        w = abs(v) * scale
        x = zero if v >= 0 else zero - w
        color = "#2a7" if v > 0 else "#c44"
        parts.append('<text x="8" y="%d">%s</text>' % (y + 12, esc(label)[:22]))
        parts.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s"/>' % (x, y, w, h_row - 4, color))
        parts.append('<text x="%.1f" y="%d">%.1f</text>' % (max(x + w, zero) + 4, y + 12, v))
    parts.append("</svg>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 2. 2시간마다 봇 점검
def h_bot_health_check(ctx: Ctx):
    bots = roster(ctx.ops)
    stale_min = int(os.environ.get("CONDUCTOR_STALE_MINUTES", "150"))
    alerts, errors = [], []
    for b in bots:
        d = ctx.ops / "bots" / b
        st = d / "status.json"
        issues = []
        if not st.exists():
            issues.append("상태파일 없음")
        else:
            try:
                s = json.loads(st.read_text(encoding="utf-8"))
                upd = parse_dt(s["updated_at"]) if s.get("updated_at") else datetime.fromtimestamp(
                    st.stat().st_mtime, KST)
                age = (ctx.now - upd).total_seconds() / 60
                if age > stale_min:
                    issues.append("상태 %d분 지남" % age)
                if s.get("blocked") or str(s.get("state", "")).lower() in ("blocked", "stuck", "막힘"):
                    issues.append("막힘: " + str(s.get("reason", ""))[:80])
            except (ValueError, KeyError, TypeError) as exc:
                issues.append("상태파일 형식 오류")
                errors.append("%s status.json %s" % (b, type(exc).__name__))
        flags = sorted(f.name for f in d.glob("*.flag")) if d.exists() else []
        if flags:
            issues.append("flag: " + ",".join(flags)[:120])
        if issues:
            alerts.append({"bot": b, "issues": issues})
            ctx.emit(b, "health_check", {"issues": issues,
                                         "todo": "상태 파일을 갱신하고, 막힘·flag 원인을 보고 후 해제하세요."}, hours=3)
    pending = state_get(ctx.con, ctx.state_key("issue_digest:pending"), [])
    report = {"at": iso(ctx.now), "bots": len(bots), "alerts": alerts,
              "issue_comments_waiting": len(pending)}
    write_json(ctx.out / "health.json", report)
    return {"items": len(bots), "errors": errors,
            "notes": "이상 %d, 이슈 새 댓글 대기 %d" % (len(alerts), len(pending))}


# ---------------------------------------------------------------------------
# 3·4. 뉴스 과제 (배정 / 100점 채점 / 70% 미만 재작업 / 요약)
NEWS = {
    "regional": {"targets": "news/regions.json", "key": "region", "rubric": {
        "count": 30, "source_url": 20, "in_period": 15, "unique": 15, "summary": 10, "target_match": 10}},
    "world": {"targets": "news/countries.json", "key": "country", "rubric": {
        "count": 25, "source_url": 20, "in_period": 15, "unique": 10, "summary": 10, "target_match": 10,
        "press_list": 10}},
}


def _targets(ops: Path, kind):
    data = read_json(ops / NEWS[kind]["targets"], NEWS[kind]["targets"])
    items = data.get("targets") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ValueError(NEWS[kind]["targets"] + " 목록이 비었습니다")
    return [str(x) for x in items]


def assign(bots, targets):
    """봇마다 대상 1개 이상. 대상이 많으면 고르게 나누고, 적으면 대상을 여러 봇이 나눠 맡는다."""
    plan = {b: [] for b in bots}
    n = max(len(bots), len(targets))
    for i in range(n):
        plan[bots[i % len(bots)]].append(targets[i % len(targets)])
    return {b: sorted(set(v), key=v.index) for b, v in plan.items()}


def _news_cfg(kind):
    return {"min_items": int(os.environ.get("CONDUCTOR_NEWS_MIN_ITEMS", "10")),
            "baseline": float(os.environ.get("CONDUCTOR_NEWS_BASELINE", "100")),
            "rework_ratio": 0.70, "period_days": 7}


def score_submission(kind, sub, targets, day: date, cfg):
    """100점 표. 반환: (점수, 항목별 점수)."""
    rub = NEWS[kind]["rubric"]
    items = sub.get("items") if isinstance(sub, dict) else None
    items = items if isinstance(items, list) else []
    n = len(items)
    parts = {k: 0.0 for k in rub}
    # 질 항목은 max(제출 수, 최소 기사 수)로 나눠, 적게 내고 비율만 높은 경우를 막는다.
    den = max(n, cfg["min_items"])
    if n:
        parts["count"] = rub["count"] * min(1.0, n / cfg["min_items"])
        good_url = sum(1 for it in items if str(it.get("url", "")).startswith(("http://", "https://")))
        parts["source_url"] = rub["source_url"] * good_url / den
        lo = day - timedelta(days=cfg["period_days"])
        inp = 0
        for it in items:
            try:
                d = date.fromisoformat(str(it.get("published", ""))[:10])
                inp += lo <= d <= day
            except ValueError:
                pass
        parts["in_period"] = rub["in_period"] * inp / den
        keys = {str(it.get("url") or it.get("title", "")).strip().lower() for it in items}
        parts["unique"] = rub["unique"] * len(keys) / den
        parts["summary"] = rub["summary"] * sum(len(str(it.get("summary", ""))) >= 40 for it in items) / den
        key = NEWS[kind]["key"]
        tl = {t.lower() for t in targets}
        parts["target_match"] = rub["target_match"] * sum(
            str(it.get(key, "")).lower() in tl for it in items) / den
    if "press_list" in rub:
        press = sub.get("press") if isinstance(sub, dict) else None
        press = press if isinstance(press, list) else []
        okp = [p for p in press if isinstance(p, dict) and p.get("name")
               and str(p.get("url", "")).startswith(("http://", "https://"))]
        covered = {str(p.get("country", "")).lower() for p in okp}
        parts["press_list"] = rub["press_list"] * (
            len(covered & {t.lower() for t in targets}) / len(targets) if targets else 0)
    total = round(sum(parts.values()), 1)
    return total, {k: round(v, 1) for k, v in parts.items()}


def _news_dir(ctx: Ctx, kind):
    return ctx.ops / "news" / kind / ctx.day.isoformat()


def news_assign(ctx: Ctx, kind):
    bots, targets = roster(ctx.ops), _targets(ctx.ops, kind)
    plan = assign(bots, targets)
    cfg = _news_cfg(kind)
    write_json(ctx.out / "assignment.json", {"kind": kind, "date": ctx.day.isoformat(), "plan": plan})
    if ctx.mode == "live":
        write_json(_news_dir(ctx, kind) / "assignment.json", {"kind": kind, "plan": plan})
    deadline = datetime(ctx.day.year, ctx.day.month, ctx.day.day, 19, 0, tzinfo=KST)
    for b, tg in plan.items():
        body = {"kind": kind, NEWS[kind]["key"] + "s": tg, "min_items": cfg["min_items"],
                "deadline": iso(deadline),
                "submit_to": "ops/news/%s/%s/%s.json" % (kind, ctx.day.isoformat(), b),
                "format": {"items": [{"title": "", "url": "https://", "published": "YYYY-MM-DD",
                                      "summary": "40자 이상", NEWS[kind]["key"]: ""}]}}
        if kind == "world":
            body["format"]["press"] = [{"name": "", "url": "https://", "country": ""}]
        ctx.emit(b, "news_assign", body, hours=14)
    return {"items": len(plan), "errors": [],
            "notes": "%s 봇 %d명에 대상 %d개 배정" % (kind, len(bots), len(targets))}


def news_score(ctx: Ctx, kind):
    cfg = _news_cfg(kind)
    folder = _news_dir(ctx, kind)
    plan_path = folder / "assignment.json"
    if ctx.mode != "live":
        shadow_plan = ctx.ops / "out" / "shadow" / (kind + "_news_assign")
        cands = sorted(shadow_plan.glob(ctx.day.isoformat() + "*/assignment.json"))
        plan_path = plan_path if plan_path.exists() else (cands[-1] if cands else plan_path)
    plan = read_json(plan_path, "배정표 assignment.json")["plan"]
    threshold = cfg["baseline"] * cfg["rework_ratio"]
    rows, errors = [], []
    for b, tg in plan.items():
        f = folder / (b + ".json")
        sub, err = {}, None
        if f.exists():
            try:
                sub = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                err = "제출 파일 JSON 오류"
                errors.append(b + " " + err)
        total, parts = score_submission(kind, sub, tg, ctx.day, cfg)
        rework = total < threshold
        rows.append({"bot": b, "submitted": f.exists() and err is None, "score": total, "parts": parts,
                     "rework": rework, "items": len(sub.get("items", []) if isinstance(sub, dict) else [])})
        if rework:
            ctx.emit(b, "news_rework", {"kind": kind, "score": total, "threshold": threshold,
                                        "parts": parts, "targets": tg,
                                        "todo": "점수가 기준의 70%% 미만입니다. 부족한 항목을 보완해 다시 제출하세요."},
                     hours=24)
    scores = [r["score"] for r in rows]
    summary = {"kind": kind, "date": ctx.day.isoformat(), "bots": len(rows),
               "submitted": sum(r["submitted"] for r in rows),
               "rework": sum(r["rework"] for r in rows), "threshold": threshold,
               "baseline": cfg["baseline"],
               "mean": round(statistics.mean(scores), 1) if scores else 0,
               "median": round(statistics.median(scores), 1) if scores else 0,
               "rows": sorted(rows, key=lambda r: -r["score"])}
    write_json(ctx.out / "scores.json", summary)
    md = ["# %s 뉴스 채점 %s" % ("지역" if kind == "regional" else "세계 부동산", ctx.day.isoformat()), "",
          "- 제출 %d/%d, 재작업 %d, 평균 %.1f, 중앙값 %.1f (재작업 기준 %.0f점 미만)" % (
              summary["submitted"], summary["bots"], summary["rework"], summary["mean"],
              summary["median"], threshold), "", "| 봇 | 점수 | 기사 | 재작업 |", "|---|---:|---:|:---:|"]
    md += ["| %s | %.1f | %d | %s |" % (r["bot"], r["score"], r["items"], "예" if r["rework"] else "")
           for r in summary["rows"]]
    (ctx.out / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return {"items": len(rows), "errors": errors,
            "notes": "제출 %d/%d 재작업 %d" % (summary["submitted"], len(rows), summary["rework"])}


# ---------------------------------------------------------------------------
# 5. 매각물건 낙찰가 채우기
def h_auction_price_fill(ctx: Ctx):
    base = ctx.ops / "auction"
    items = read_json(base / "sold_items.json", "매각물건 목록 sold_items.json")
    items = items.get("items") if isinstance(items, dict) else items
    if not isinstance(items, list):
        raise ValueError("sold_items.json 형식 오류")
    key = lambda it: "%s|%s|%s" % (it.get("court", ""), it.get("case_no", ""), it.get("item_no", "1"))
    found, errors = {}, []
    for f in sorted((base / "price_results").glob("*.json")):
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
            for r in (res.get("items") if isinstance(res, dict) else res) or []:
                bid = r.get("winning_bid")
                if isinstance(bid, (int, float)) and bid > 0 and r.get("case_no"):
                    found.setdefault(key(r), {"winning_bid": int(bid), "source": str(r.get("source", f.stem))[:120]})
        except (ValueError, AttributeError, TypeError):
            errors.append(f.name + " 형식 오류")
    filled, merged, todo = 0, [], []
    for it in items:
        it = dict(it)
        if not it.get("winning_bid") and key(it) in found:
            it.update(found[key(it)])
            filled += 1
        if not it.get("winning_bid"):
            todo.append(it)
        merged.append(it)
    write_json(ctx.out / "sold_items_filled.json", {"items": merged})
    if ctx.mode == "live":
        write_json(base / "sold_items_filled.json", {"items": merged, "updated_at": iso(ctx.now)})
    try:
        bots = roster(ctx.ops)
    except (FileNotFoundError, ValueError) as exc:
        bots = []
        errors.append(str(exc))
    chunk = int(os.environ.get("CONDUCTOR_PRICE_CHUNK", "20"))
    if bots and todo:
        for i in range(0, len(todo), chunk):
            b = bots[(i // chunk) % len(bots)]
            ctx.emit(b, "price_fill_%03d" % (i // chunk),
                     {"items": [{k: t.get(k) for k in ("court", "case_no", "item_no", "sale_date")}
                                for t in todo[i:i + chunk]],
                      "submit_to": "ops/auction/price_results/<bot>_<date>.json",
                      "format": {"items": [{"court": "", "case_no": "", "item_no": "",
                                            "winning_bid": 0, "source": ""}]}}, hours=8)
    done = sum(1 for m in merged if m.get("winning_bid"))
    return {"items": len(items), "errors": errors,
            "notes": "낙찰가 %d/%d (이번 반영 %d, 남은 %d)" % (done, len(items), filled, len(todo))}


# ---------------------------------------------------------------------------
# 6. GitHub 이슈 답변 확인 → 봇 전달 목록
def http_get_json(url, headers):
    req = Request(url, headers=headers)
    with urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def h_issue_reply_digest(ctx: Ctx):
    repos = [r.strip() for r in os.environ.get(
        "CONDUCTOR_GITHUB_REPOS", "jijuclub-tech/court-auction-controller").split(",") if r.strip()]
    token = os.environ.get("CONDUCTOR_GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "bidguard-controller"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        bots = roster(ctx.ops)
    except (FileNotFoundError, ValueError):
        bots = []
    last = state_get(ctx.con, ctx.state_key("issue_digest:since"), {})
    pending = state_get(ctx.con, ctx.state_key("issue_digest:pending"), [])
    errors, new = [], []
    for repo in repos:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            errors.append("저장소 이름 오류")
            continue
        since = last.get(repo) or iso(ctx.now - timedelta(days=1))
        url = "https://api.github.com/repos/%s/issues/comments?%s" % (
            repo, urlencode({"since": since, "per_page": 100, "sort": "created", "direction": "asc"}))
        try:
            comments = ctx.fetch(url, headers)
        except Exception as exc:  # 네트워크 실패는 기록 후 재시도
            errors.append("%s 조회 실패 %s" % (repo, type(exc).__name__))
            continue
        newest = since
        for c in comments if isinstance(comments, list) else []:
            created = c.get("updated_at") or c.get("created_at") or since
            if created <= since:
                continue
            body = str(c.get("body") or "")
            targets = [b for b in bots if re.search(r"(?<![A-Za-z0-9_-])@?" + re.escape(b) + r"(?![A-Za-z0-9_-])",
                                                       body)] or ["conductor"]
            issue_no = str(c.get("issue_url", "")).rsplit("/", 1)[-1]
            new.append({"repo": repo, "issue": issue_no, "comment_url": c.get("html_url", ""),
                        "author": (c.get("user") or {}).get("login", ""), "at": created,
                        "excerpt": body[:300], "deliver_to": targets})
            newest = max(newest, created)
        last[repo] = newest
    for n in new:
        for b in n["deliver_to"]:
            if b != "conductor":
                ctx.emit(b, "issue_reply_" + hashlib.sha256(n["comment_url"].encode()).hexdigest()[:10],
                         {"repo": n["repo"], "issue": n["issue"], "comment_url": n["comment_url"],
                          "author": n["author"], "excerpt": n["excerpt"],
                          "todo": "댓글을 읽고 해당 이슈에서 답하거나 작업을 반영하세요."}, hours=48)
    pending = (pending + new)[-200:]
    state_set(ctx.con, ctx.state_key("issue_digest:since"), last)
    state_set(ctx.con, ctx.state_key("issue_digest:pending"), pending)
    write_json(ctx.out / "deliveries.json", {"at": iso(ctx.now), "new": new})
    return {"items": len(new), "errors": errors, "notes": "새 댓글 %d" % len(new)}


def h_parity_compare_report(ctx: Ctx):
    end = ctx.day
    start = end - timedelta(days=int(os.environ.get("CONDUCTOR_REPORT_DAYS", "14")) - 1)
    rep = compare_report(ctx.con, start, end, ctx.now, clip=True)
    write_json(ctx.out / "compare.json", rep)
    (ctx.out / "compare.md").write_text(report_markdown(rep), encoding="utf-8")
    write_json(ctx.ops / "out" / "reports" / "compare_latest.json", rep)
    (ctx.ops / "out" / "reports" / "compare_latest.md").write_text(report_markdown(rep), encoding="utf-8")
    return {"items": len(rep["tasks"]), "errors": [], "notes": "%s~%s" % (start, end)}


HANDLERS = {
    "ai_usage_compare": h_ai_usage_compare,
    "bot_health_check": h_bot_health_check,
    "regional_news_assign": lambda c: news_assign(c, "regional"),
    "regional_news_score": lambda c: news_score(c, "regional"),
    "world_news_assign": lambda c: news_assign(c, "world"),
    "world_news_score": lambda c: news_score(c, "world"),
    "auction_price_fill": h_auction_price_fill,
    "issue_reply_digest": h_issue_reply_digest,
    "parity_compare_report": h_parity_compare_report,
}


# ---------------------------------------------------------------------------
# 실행기
def _attempts(con, task, slot, mode):
    return con.execute("""SELECT attempt, ok, finished, started FROM auto_runs
                          WHERE task=? AND slot=? AND mode=? AND actor='controller'
                          ORDER BY attempt""", (task, slot, mode)).fetchall()


def run_task(con, task: dict, slot: datetime, mode: str, at: datetime, ops: Path, source="scheduler",
             fetch=None):
    """한 번 실행하고 기록. 실패해도 예외를 밖으로 내지 않는다."""
    day = slot.date()
    expected, _, reason = decide(con, task, day)
    prev = _attempts(con, task["task"], slot_key(slot), mode)
    attempt = (prev[-1][0] + 1) if prev else 1
    run_id = "run-" + uuid.uuid4().hex[:16]
    con.execute("""INSERT INTO auto_runs(run_id,date,parity,actor,mode,task,slot,attempt,started,
                   expected_actor,rule_ok,source,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, day.isoformat(), parity(day), "controller", mode, task["task"], slot_key(slot),
                 attempt, iso(at), expected, 1, source, reason[:200]))
    con.commit()
    ctx = Ctx(con, ops, task["task"], slot, mode, at, fetch=fetch)
    t0 = time.monotonic()
    try:
        res = HANDLERS[task["handler"]](ctx)
        errs = [str(e)[:200] for e in res.get("errors", [])]
        ok = 1
        notes = res.get("notes", "")
        items = int(res.get("items", 0))
    except Exception as exc:
        errs = ["%s: %s" % (type(exc).__name__, str(exc)[:200])]
        ok, notes, items = 0, "실패", 0
        traceback.print_exc()
    if ctx.orders_preview:
        try:
            write_json(ctx.out / "orders_preview.json", ctx.orders_preview)
        except OSError:
            pass
        notes += " / 지시 미리보기 %d건(발송 안 함)" % len(ctx.orders_preview)
    if mode == "live":
        try:
            export_queue_files(con, ops, at)
        except OSError as exc:
            errs.append("큐 파일 쓰기 실패 " + type(exc).__name__)
    fin = at + timedelta(seconds=time.monotonic() - t0)
    con.execute("""UPDATE auto_runs SET finished=?, ok=?, items=?, errors=?, ai_steps=?, notes=?
                   WHERE run_id=?""",
                (iso(fin), ok, items, json.dumps(errs, ensure_ascii=False), ctx.ai_steps,
                 (reason + " | " + notes)[:500], run_id))
    con.commit()
    return {"run_id": run_id, "task": task["task"], "slot": slot_key(slot), "mode": mode,
            "attempt": attempt, "ok": bool(ok), "items": items, "errors": errs, "notes": notes}


def tick(con, at: datetime = None, ops: Path = None, fetch=None):
    """일정이 된 일을 돌린다. 관제기 스레드나 CLI(cron)에서 1분마다 불러도 된다."""
    at = (at or now_kst()).astimezone(KST)
    ops = ops or ops_dir()
    done = []
    for t in tasks(con):
        if not t["enabled"] or t["handler"] not in HANDLERS:
            continue
        for slot in slots_between(t["schedule"], at - timedelta(hours=GRACE_HOURS), at):
            expected, mode, _ = decide(con, t, slot.date())
            if mode is None:            # 지휘자 차례: 관제기는 하지 않는다
                continue
            prev = _attempts(con, t["task"], slot_key(slot), mode)
            if any(p[1] == 1 for p in prev):
                continue                # 성공
            if any(p[1] is None and at - parse_dt(p[3]) < timedelta(minutes=STUCK_MIN) for p in prev):
                continue                # 진행 중 (STUCK_MIN 넘게 안 끝나면 실패로 보고 재시도)
            if len(prev) > int(t["retries"]):
                continue                # 재시도 소진
            if prev:
                wait = BACKOFF_MIN[min(len(prev) - 1, len(BACKOFF_MIN) - 1)]
                if at < parse_dt(prev[-1][2] or prev[-1][3]) + timedelta(minutes=wait):
                    continue
            done.append(run_task(con, t, slot, mode, at, ops, fetch=fetch))
    return done


# ---------------------------------------------------------------------------
# 지휘자 실행 기록 (홀수일·예외·fallback)
def log_conductor_run(con, body: dict, at: datetime):
    allowed = {"date", "actor", "task", "slot", "started", "finished", "ok", "items", "errors",
               "ai_steps", "notes"}
    if set(body) - allowed:
        raise ValueError("unknown field")
    actor = body.get("actor")
    if actor not in ("conductor", "conductor_fallback"):
        raise ValueError("actor must be conductor or conductor_fallback")
    t = task_row(con, body.get("task", ""))
    if not t:
        raise ValueError("unknown task")
    started, finished = parse_dt(body["started"]), parse_dt(body["finished"])
    if finished < started:
        raise ValueError("finished before started")
    day = date.fromisoformat(body.get("date") or started.date().isoformat())
    slot = body.get("slot")
    if slot:
        slot_dt = datetime.fromisoformat(slot).replace(tzinfo=KST)
        if slot_dt.date() != day:
            raise ValueError("slot date mismatch")
    else:
        cands = slots_between(t["schedule"], datetime(day.year, day.month, day.day, tzinfo=KST),
                              datetime(day.year, day.month, day.day, 23, 59, tzinfo=KST))
        before = [c for c in cands if c <= started + timedelta(minutes=30)]
        slot_dt = (before[-1] if before else (cands[0] if cands else started))
    expected, _, _ = decide(con, t, day)
    errors = body.get("errors", [])
    if not isinstance(errors, list):
        raise ValueError("errors must be a list")
    sk = slot_key(slot_dt)
    prev = con.execute("SELECT MAX(attempt) FROM auto_runs WHERE task=? AND slot=? AND actor=? AND mode='live'",
                       (t["task"], sk, actor)).fetchone()[0] or 0
    rid = "run-" + uuid.uuid4().hex[:16]
    con.execute("""INSERT INTO auto_runs(run_id,date,parity,actor,mode,task,slot,attempt,started,finished,ok,
                   items,errors,ai_steps,notes,expected_actor,rule_ok,source)
                   VALUES(?,?,?,?, 'live',?,?,?,?,?,?,?,?,?,?,?,?, 'conductor_log')""",
                (rid, day.isoformat(), parity(day), actor, t["task"], sk, prev + 1, iso(started), iso(finished),
                 1 if body.get("ok") else 0, int(body.get("items", 0)),
                 json.dumps([str(e)[:200] for e in errors][:50], ensure_ascii=False),
                 int(body.get("ai_steps", 0)), str(body.get("notes", ""))[:500], expected,
                 1 if expected == actor else 0))
    return {"ok": True, "run_id": rid, "slot": sk, "expected_actor": expected, "rule_ok": expected == actor}


def add_exemption(con, body, who, at):
    day = date.fromisoformat(str(body.get("date")))
    task = body.get("task", "*")
    if task != "*" and not task_row(con, task):
        raise ValueError("unknown task")
    note = str(body.get("note", "")).strip()
    if not note:
        raise ValueError("note(정한영 지시 요지) required")
    con.execute("INSERT INTO auto_exemptions(date,task,note,created_by,created_at) VALUES(?,?,?,?,?)",
                (day.isoformat(), task, note[:300], who, iso(at)))
    return {"ok": True, "date": day.isoformat(), "task": task, "parity": parity(day)}


def set_task(con, body, at):
    t = task_row(con, body.get("task", ""))
    if not t:
        raise ValueError("unknown task")
    if "enabled" in body:
        con.execute("UPDATE auto_tasks SET enabled=? WHERE task=?", (1 if body["enabled"] else 0, t["task"]))
    if "verified" in body:
        note = str(body.get("note", "")).strip()
        if body["verified"] and not note:
            raise ValueError("실기 확인 근거(note) required")
        con.execute("UPDATE auto_tasks SET verified=?, verified_at=?, verified_note=? WHERE task=?",
                    (1 if body["verified"] else 0, iso(at), note[:300], t["task"]))
    return task_row(con, t["task"])


# ---------------------------------------------------------------------------
# 홀짝 성과 비교
def compare_report(con, start: date, end: date, at: datetime = None, clip=False):
    """clip=True 면 설치 전 날짜는 '놓친 일'로 세지 않도록 기간을 설치일부터로 자른다."""
    at = (at or now_kst()).astimezone(KST)
    installed = state_get(con, "installed_on")
    if clip and installed and date.fromisoformat(installed) > start:
        start = date.fromisoformat(installed)
    out = {"from": start.isoformat(), "to": end.isoformat(), "generated_at": iso(at), "tasks": [],
           "totals": {}}
    for t in tasks(con):
        if t["meta"]:
            continue
        hi = min(at, datetime(end.year, end.month, end.day, 23, 59, tzinfo=KST))
        slots = slots_between(t["schedule"], datetime(start.year, start.month, start.day, tzinfo=KST), hi)
        rows = con.execute("""SELECT slot,actor,mode,ok,started,finished,ai_steps,rule_ok,attempt
                              FROM auto_runs WHERE task=? AND date BETWEEN ? AND ? ORDER BY slot, attempt""",
                           (t["task"], start.isoformat(), end.isoformat())).fetchall()
        live = {}
        shadow = {}
        violations = 0
        for r in rows:
            tgt = shadow if r[2] == "shadow" else live
            tgt.setdefault(r[0], []).append(r)
            if r[2] == "live" and not r[7]:
                violations += 1
        groups = {}
        for s in slots:
            p = parity(s.date())
            runs = live.get(slot_key(s), [])
            last = runs[-1] if runs else None
            g = groups.setdefault(p, {"expected": 0, "done": 0, "ok": 0, "missed": 0, "secs": [],
                                      "ai": [], "actors": {}})
            g["expected"] += 1
            if last is None:
                g["missed"] += 1
                continue
            g["done"] += 1
            g["ok"] += 1 if any(x[3] == 1 for x in runs) else 0
            g["actors"][last[1]] = g["actors"].get(last[1], 0) + 1
            if last[5]:
                g["secs"].append((parse_dt(last[5]) - parse_dt(last[4])).total_seconds())
            g["ai"].append(sum(x[6] for x in runs))
        row = {"task": t["task"], "title": t["title"], "verified": bool(t["verified"]),
               "rule_violations": violations,
               "shadow": {"slots": len(shadow), "ok": sum(1 for v in shadow.values() if any(x[3] == 1 for x in v))}}
        for p in ("odd", "even"):
            g = groups.get(p, {"expected": 0, "done": 0, "ok": 0, "missed": 0, "secs": [], "ai": [], "actors": {}})
            row[p] = {"expected": g["expected"], "done": g["done"], "missed": g["missed"],
                      "success_rate": round(g["ok"] / g["expected"] * 100, 1) if g["expected"] else None,
                      "avg_seconds": round(statistics.mean(g["secs"]), 1) if g["secs"] else None,
                      "avg_ai_steps": round(statistics.mean(g["ai"]), 2) if g["ai"] else None,
                      "actors": g["actors"]}
        out["tasks"].append(row)
    for p in ("odd", "even"):
        e = sum(r[p]["expected"] for r in out["tasks"])
        ok = sum(round((r[p]["success_rate"] or 0) * r[p]["expected"] / 100) for r in out["tasks"])
        out["totals"][p] = {"expected": e, "missed": sum(r[p]["missed"] for r in out["tasks"]),
                            "success_rate": round(ok / e * 100, 1) if e else None}
    return out


def report_markdown(rep):
    f = lambda v, s="": "-" if v is None else ("%s%s" % (v, s))
    lines = ["# 홀수일(지휘자) vs 짝수일(관제기) 성과 %s ~ %s" % (rep["from"], rep["to"]), "",
             "전체: 홀수 성공률 %s, 놓침 %d / 짝수 성공률 %s, 놓침 %d" % (
                 f(rep["totals"]["odd"]["success_rate"], "%"), rep["totals"]["odd"]["missed"],
                 f(rep["totals"]["even"]["success_rate"], "%"), rep["totals"]["even"]["missed"]), "",
             "| 작업 | 실기확인 | 홀 성공률 | 짝 성공률 | 홀 시간(초) | 짝 시간(초) | 홀 AI | 짝 AI | 홀 놓침 | 짝 놓침 | 짝 수행자 | 규칙위반 |",
             "|---|:---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    for r in rep["tasks"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %d | %d | %s | %d |" % (
            r["task"], "O" if r["verified"] else "X", f(r["odd"]["success_rate"], "%"),
            f(r["even"]["success_rate"], "%"), f(r["odd"]["avg_seconds"]), f(r["even"]["avg_seconds"]),
            f(r["odd"]["avg_ai_steps"]), f(r["even"]["avg_ai_steps"]), r["odd"]["missed"], r["even"]["missed"],
            ",".join("%s:%d" % kv for kv in r["even"]["actors"].items()) or "-", r["rule_violations"]))
    lines += ["", "그림자 실행(관제기 실기 확인 전 짝수일 결과)은 비교에서 빼고 따로 셉니다."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# HTTP 연결 (controller.py 가 부른다)
def _test_key_expired(handler, at):
    return (at >= TEST_KEY_CUTOFF and
            "test" in (handler.headers.get("X-Bot-Token", ""), handler.headers.get("X-Admin-Token", "")))


def route(handler, method, path, query, data_dir=None):
    if not path.startswith("/auto/"):
        return False
    import site_map_ingest  # 같은 HTTPS 판정 재사용
    at = now_kst()
    if not site_map_ingest.ssl_ok(handler):
        handler._json(403, {"ok": False, "error": "HTTPS required"})
        return True
    if _test_key_expired(handler, at):
        handler._json(403, {"ok": False, "error": "test key expired"})
        return True
    admin = path.startswith("/auto/admin/")
    if admin and not handler._admin():
        handler._json(403, {"ok": False, "error": "admin required"})
        return True
    if not admin and not handler._bot():
        handler._json(403, {"ok": False, "error": "bot token required"})
        return True
    ops = ops_dir(data_dir)
    try:
        with handler._site_map_db() as con:
            if method == "GET" and path == "/auto/bot/orders":
                result = (200, {"ok": True, "orders": pull_orders(con, query.get("bot", ""), at)})
            elif method == "POST" and path == "/auto/bot/ack":
                b = handler._body()
                result = ack_order(con, str(b.get("bot", "")), b, at)
            elif method == "POST" and path == "/auto/run-log":
                result = (201, log_conductor_run(con, handler._body(), at))
            elif method == "POST" and path in ("/auto/exemption", "/auto/admin/exemption"):
                result = (201, add_exemption(con, handler._body(), "admin" if admin else "conductor_bot", at))
            elif method == "GET" and path in ("/auto/today", "/auto/admin/status"):
                today = at.date()
                result = (200, {"ok": True, "version": VERSION, "date": today.isoformat(),
                                "parity": parity(today),
                                "tasks": [dict(t, today=decide(con, t, today)[0]) for t in tasks(con)],
                                "recent": [dict(zip(("date", "parity", "actor", "mode", "task", "slot",
                                                     "attempt", "ok", "items", "ai_steps", "notes"), r))
                                           for r in con.execute(
                                    "SELECT date,parity,actor,mode,task,slot,attempt,ok,items,ai_steps,notes "
                                    "FROM auto_runs ORDER BY id DESC LIMIT 40")]})
            elif method == "GET" and path == "/auto/admin/report":
                end = date.fromisoformat(query.get("to") or at.date().isoformat())
                start = date.fromisoformat(query.get("from") or (end - timedelta(days=13)).isoformat())
                rep = compare_report(con, start, end, at, clip=True)
                rep["markdown"] = report_markdown(rep)
                result = (200, rep)
            elif method == "POST" and path == "/auto/admin/task":
                result = (200, {"ok": True, "task": set_task(con, handler._body(), at)})
            elif method == "POST" and path == "/auto/admin/run":
                b = handler._body()
                t = task_row(con, b.get("task", ""))
                if not t:
                    raise ValueError("unknown task")
                mode = "shadow" if b.get("shadow", True) else "live"
                result = (200, run_task(con, t, at.replace(second=0, microsecond=0), mode, at, ops,
                                        source="admin"))
            else:
                result = (404, {"ok": False, "error": "unknown /auto route"})
    except (ValueError, TypeError, KeyError, sqlite3.IntegrityError) as exc:
        result = (422, {"ok": False, "error": "invalid request: " + type(exc).__name__})
    handler._json(*result)
    return True


def start_thread(db_factory, data_dir=None, interval=60):
    """관제기 안의 일정 실행기 스레드. CONDUCTOR_AUTOMATION=0 이면 켜지 않는다."""
    if os.environ.get("CONDUCTOR_AUTOMATION", "1") == "0":
        return None
    lock = threading.Lock()

    def loop():
        while True:
            if lock.acquire(blocking=False):
                try:
                    con = db_factory()
                    try:
                        tick(con, ops=ops_dir(data_dir))
                        con.commit()
                    finally:
                        con.close()
                except Exception:
                    traceback.print_exc()
                finally:
                    lock.release()
            time.sleep(interval)

    th = threading.Thread(target=loop, daemon=True, name="conductor-automation")
    th.start()
    return th


# ---------------------------------------------------------------------------
# CLI: 관제기 서버 밖에서도 cron/수동으로 쓸 수 있게
def main(argv=None):
    ap = argparse.ArgumentParser(description="지휘자 반복 업무 자동화 (홀수일 지휘자 / 짝수일 관제기)")
    ap.add_argument("--db", default=os.path.join(os.environ.get("CONTROLLER_DATA", "controller_data"),
                                                 "controller.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("today", help="오늘 홀짝과 작업별 담당")
    sub.add_parser("tick", help="일정이 된 관제기 작업 실행")
    r = sub.add_parser("report", help="홀짝 성과 비교")
    r.add_argument("--from", dest="start")
    r.add_argument("--to", dest="end")
    lg = sub.add_parser("log", help="지휘자 실행 기록 (JSON 한 줄)")
    lg.add_argument("json")
    a = ap.parse_args(argv)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(a.db, timeout=10)
    init(con)
    at = now_kst()
    if a.cmd == "today":
        d = at.date()
        print(json.dumps({"date": d.isoformat(), "parity": parity(d),
                          "tasks": {t["task"]: decide(con, t, d)[0] for t in tasks(con)}},
                         ensure_ascii=False, indent=2))
    elif a.cmd == "tick":
        print(json.dumps(tick(con, at), ensure_ascii=False, indent=2))
    elif a.cmd == "report":
        end = date.fromisoformat(a.end) if a.end else at.date()
        start = date.fromisoformat(a.start) if a.start else end - timedelta(days=13)
        print(report_markdown(compare_report(con, start, end, at, clip=True)))
    elif a.cmd == "log":
        print(json.dumps(log_conductor_run(con, json.loads(a.json), at), ensure_ascii=False))
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
