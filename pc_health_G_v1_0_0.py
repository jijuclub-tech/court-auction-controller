"""수집 PC 전원·호출 상태를 받아 관제기 화면에 보여 준다.

상태값 자체는 GitHub에 올리지 않는다. 수집기 키·비밀번호·ID는 읽거나 저장하지 않는다.
파일명 규칙: 이슈 #28. import 이름은 기존 T_v0_2 처럼 점 대신 밑줄을 쓴다.
"""
from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone

IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
SCHEMA_ID = "bidguard.pc_health.v1"
DEFAULT_OFFLINE_MIN = 30
HUMAN_CALL_WARN_HOURS = 24
LOW_BATTERY = 20
MAX_BODY = 8 * 1024
KST = timezone(timedelta(hours=9), "KST")
ALLOWED = (
    "schema", "observed_at", "machine_name", "battery_percent", "charging",
    "ac_plugged", "bot_ask_pending", "human_call_time", "collector_running",
    "collector_root",
)
SECRET_KEYS = {
    "password", "passwd", "secret", "token", "apikey", "api_key",
    "authorization", "cookie", "set-cookie", "session", "credential",
    "private_key", "client_secret", "id", "user", "username", "login",
}
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pc_health (
  machine_name TEXT PRIMARY KEY,
  received_at TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  payload TEXT NOT NULL);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(con: sqlite3.Connection) -> None:
    con.execute(SCHEMA_SQL)


def _token() -> str:
    return os.environ.get("CONTROLLER_PC_HEALTH_TOKEN", "") or ""


def _token_ok(handler) -> bool:
    expected = _token()
    if not expected:
        return True
    got = handler.headers.get("X-PC-Health-Token", "") or ""
    if not got or len(got) != len(expected):
        # compare_digest requires same length; still do not leak
        return False
    return hmac.compare_digest(got, expected)


def _reject_secrets(value, depth=0) -> None:
    if depth > 8:
        raise ValueError("payload too deep")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("invalid key")
            lower = key.lower().replace("-", "_")
            if lower in SECRET_KEYS or lower.endswith("_token") or lower.endswith("_password"):
                raise ValueError("sensitive field rejected")
            _reject_secrets(child, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _reject_secrets(item, depth + 1)


def _parse_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    for cand in (text, text.replace("Z", "+00:00"), text.replace(" ", "T")):
        try:
            dt = datetime.fromisoformat(cand)
            return dt if dt.tzinfo else dt.replace(tzinfo=KST)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%m/%d %H:%M", "%m/%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            if "%Y" not in fmt:
                dt = dt.replace(year=datetime.now(KST).year)
            return dt.replace(tzinfo=KST)
        except ValueError:
            pass
    return None


def sanitize(body: dict) -> dict:
    if not isinstance(body, dict):
        raise ValueError("object required")
    _reject_secrets(body)
    name = str(body.get("machine_name") or "").strip()
    if not IDENT.fullmatch(name):
        raise ValueError("machine_name required")
    out = {"schema": SCHEMA_ID, "machine_name": name}
    observed = body.get("observed_at")
    out["observed_at"] = str(observed)[:40] if observed else now_utc()
    bat = body.get("battery_percent")
    if bat is None or bat == "":
        out["battery_percent"] = None
    else:
        try:
            pct = int(bat)
        except (TypeError, ValueError) as exc:
            raise ValueError("battery_percent") from exc
        if pct < 0 or pct > 100:
            raise ValueError("battery_percent")
        out["battery_percent"] = pct
    for key in ("charging", "ac_plugged", "bot_ask_pending", "collector_running"):
        val = body.get(key)
        out[key] = None if val is None else bool(val)
    hum = body.get("human_call_time")
    out["human_call_time"] = None if hum in (None, "") else str(hum)[:40]
    root = body.get("collector_root")
    if root not in (None, ""):
        # path only, no credentials
        out["collector_root"] = str(root)[:260]
    extra = set(body) - set(ALLOWED)
    if extra:
        # ignore unknown non-secret keys; secrets already rejected
        pass
    return out


def warnings_for(row: dict, *, now=None, offline_after_min=DEFAULT_OFFLINE_MIN) -> list:
    now = now or datetime.now(timezone.utc)
    warns = []
    received = _parse_time(row.get("received_at")) or _parse_time(row.get("observed_at"))
    online = True
    if received is None:
        online = False
    else:
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        if now - received >= timedelta(minutes=int(offline_after_min)):
            online = False
    if not online:
        warns.append("offline")
    charging, ac = row.get("charging"), row.get("ac_plugged")
    if charging is False and ac is not True:
        warns.append("charger_unplugged")
    elif charging is None and ac is False:
        warns.append("charger_unplugged")
    pct = row.get("battery_percent")
    if isinstance(pct, int) and pct < LOW_BATTERY:
        warns.append("battery_low")
    hum = row.get("human_call_time")
    if hum:
        parsed = _parse_time(hum)
        if parsed is None:
            warns.append("human_call")
        else:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=KST)
            if now - parsed <= timedelta(hours=HUMAN_CALL_WARN_HOURS):
                warns.append("human_call")
    return warns


def is_online(row: dict, *, now=None, offline_after_min=DEFAULT_OFFLINE_MIN) -> bool:
    return "offline" not in warnings_for(row, now=now, offline_after_min=offline_after_min)


def store(con: sqlite3.Connection, payload: dict, received_at: str | None = None) -> dict:
    clean = sanitize(payload)
    received_at = received_at or now_utc()
    con.execute(
        "INSERT INTO pc_health(machine_name, received_at, observed_at, payload) VALUES(?,?,?,?) "
        "ON CONFLICT(machine_name) DO UPDATE SET received_at=excluded.received_at, "
        "observed_at=excluded.observed_at, payload=excluded.payload",
        (clean["machine_name"], received_at, clean["observed_at"],
         json.dumps(clean, ensure_ascii=False)),
    )
    clean["received_at"] = received_at
    return clean


def admin_snapshot(con: sqlite3.Connection, offline_after_min: int | None = None) -> dict:
    minutes = int(offline_after_min or os.environ.get("PC_HEALTH_OFFLINE_MIN") or DEFAULT_OFFLINE_MIN)
    now = datetime.now(timezone.utc)
    machines = []
    for r in con.execute(
        "SELECT machine_name, received_at, observed_at, payload FROM pc_health "
        "ORDER BY received_at DESC"
    ):
        try:
            payload = json.loads(r["payload"])
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        row = {
            "machine_name": r["machine_name"],
            "received_at": r["received_at"],
            "observed_at": r["observed_at"] or payload.get("observed_at"),
            "battery_percent": payload.get("battery_percent"),
            "charging": payload.get("charging"),
            "ac_plugged": payload.get("ac_plugged"),
            "bot_ask_pending": payload.get("bot_ask_pending"),
            "human_call_time": payload.get("human_call_time"),
            "collector_running": payload.get("collector_running"),
        }
        warns = warnings_for(row, now=now, offline_after_min=minutes)
        row["warnings"] = warns
        row["online"] = "offline" not in warns
        machines.append(row)
    return {"offline_after_min": minutes, "machines": machines}


def _handler_db(_handler=None):
    import controller
    return controller.db()


def route(handler, method, path, query) -> bool:
    if path not in ("/api/collector/pc_health", "/api/admin/pc_health"):
        return False
    if path == "/api/admin/pc_health":
        if method != "GET":
            handler._json(405, {"ok": False})
            return True
        if not handler._admin():
            handler._json(403, {"ok": False, "error": "admin token"})
            return True
        minutes = query.get("offline_min")
        try:
            minutes_i = int(minutes) if minutes else None
        except ValueError:
            handler._json(400, {"ok": False, "error": "offline_min"})
            return True
        with _handler_db(handler) as con:
            handler._json(200, {"ok": True, **admin_snapshot(con, offline_after_min=minutes_i)})
        return True
    if method != "POST":
        handler._json(405, {"ok": False})
        return True
    if not _token_ok(handler):
        handler._json(403, {"ok": False, "error": "pc health token"})
        return True
    try:
        body = handler._body()
        if not isinstance(body, dict):
            raise ValueError("object required")
        raw_len = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        if raw_len > MAX_BODY:
            raise ValueError("payload too large")
        with _handler_db(handler) as con:
            stored = store(con, body)
        handler._json(200, {"ok": True, "machine_name": stored["machine_name"]})
    except ValueError as exc:
        handler._json(400, {"ok": False, "error": str(exc)[:200]})
    except (TypeError, json.JSONDecodeError):
        handler._json(400, {"ok": False, "error": "invalid json"})
    return True
