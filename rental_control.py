"""BIDGUARD member rental: controller-owned queue, leases, audit and status.

Member access codes are issued by the administrator for a controlled pilot.
A paid public launch requires the real member/points ledger adapter.
No browser passwords, screenshots or raw downloaded documents are stored here.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
import member_core

LEASE_SECONDS = 180
MAX_RESERVATION_SECONDS = 12 * 3600
DEFAULT_ROUTES = []
RENTAL_PILOT_ENABLED = os.environ.get('BIDGUARD_RENTAL_PILOT_ENABLED', '') == '1'

SCHEMA = """
CREATE TABLE IF NOT EXISTS rental_members (
  member_id TEXT PRIMARY KEY, access_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS rental_jobs (
  job_id TEXT PRIMARY KEY, member_id TEXT NOT NULL, route_id TEXT NOT NULL,
  mode TEXT NOT NULL, duration_limit INTEGER, used_seconds INTEGER NOT NULL DEFAULT 0,
  last_running_at INTEGER, status TEXT NOT NULL, collector TEXT,
  lease_epoch INTEGER NOT NULL DEFAULT 0, lease_until INTEGER NOT NULL DEFAULT 0,
  agent_session TEXT, checkpoint INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  FOREIGN KEY(member_id) REFERENCES rental_members(member_id));
CREATE INDEX IF NOT EXISTS rental_queue_idx ON rental_jobs(status,created_at);
CREATE TABLE IF NOT EXISTS rental_favorites (member_id TEXT NOT NULL,route_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,PRIMARY KEY(member_id,route_id));
CREATE TABLE IF NOT EXISTS rental_events (
  event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, at INTEGER NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(job_id) REFERENCES rental_jobs(job_id));
CREATE TABLE IF NOT EXISTS rental_admin_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER NOT NULL, actor TEXT NOT NULL,
  action TEXT NOT NULL, detail TEXT NOT NULL);
"""

def init(con, default_routes=None):
    global DEFAULT_ROUTES
    DEFAULT_ROUTES = default_routes or []
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(rental_jobs)")}
    if "agent_session" not in cols:
        con.execute("ALTER TABLE rental_jobs ADD COLUMN agent_session TEXT")

def _now():
    return int(time.time())

def _hash(value):
    return hashlib.sha256(value.encode()).hexdigest()

def _event(con, job_id, actor, action, detail="", event_id=None):
    con.execute("INSERT INTO rental_events(event_id,job_id,at,actor,action,detail) VALUES(?,?,?,?,?,?)",
                (event_id or uuid.uuid4().hex, job_id, _now(), actor, action, detail[:240]))

def _member(con, header):
    if not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    if not token or len(token) > 200:
        return None
    digest = _hash(token)
    # Do not fetch raw tokens into logs.
    row = con.execute("""SELECT r.member_id FROM rental_members r
                          JOIN members m ON m.member_id=r.member_id
                          WHERE r.access_hash=? AND r.active=1
                            AND m.account_status IN ('active','pilot')""",(digest,)).fetchone()
    return row["member_id"] if row else None

def _catalog(con):
    r = con.execute("SELECT v FROM kv WHERE k='routes'").fetchone()
    try:
        routes = json.loads(r["v"]) if r else DEFAULT_ROUTES
    except (TypeError, ValueError):
        routes = []
    if not isinstance(routes, list):
        routes = DEFAULT_ROUTES
    # Only pre-approved routes can be rented, not arbitrary commands or websites.
    return [{"route_id":x.get("route_id"),"name":str(x.get("name") or x.get("route_id"))[:80]}
            for x in routes if isinstance(x,dict) and x.get("route_id")
            and re.fullmatch(r"[A-Za-z0-9_-]{1,90}", str(x["route_id"]))]

def _row(r):
    if not r:
        return None
    return {k:r[k] for k in ("job_id","member_id","route_id","mode","duration_limit",
            "used_seconds","status","collector","lease_epoch","lease_until",
            "checkpoint","created_at","updated_at")}

def _busy_primary(con, name):
    # Primary BIDGUARD jobs outrank rental. Treat active and unclaimed eligible
    # central jobs as primary work, without changing the existing central queue.
    row = con.execute("""SELECT 1 FROM central_jobs
      WHERE (status IN ('LEASED','ACKED') AND assignee=?)
         OR (status='PENDING' AND (target_name IS NULL OR target_name=?))
      LIMIT 1""", (name,name)).fetchone()
    return bool(row)

def _node_idle(con, name):
    r=con.execute("SELECT last_seen,last_state FROM collectors WHERE name=?", (name,)).fetchone()
    if not r:
        return False
    try:
        from datetime import datetime, timezone
        seen=datetime.fromisoformat(r["last_seen"]).timestamp()
        state=json.loads(r["last_state"] or "{}")
        return (_now()-seen<180 and state.get("rental_enabled") is True
                and state.get("state") in ("idle","waiting","ready"))
    except (ValueError, TypeError, AttributeError):
        return False

def _preferred_collector(con):
    """Choose only live, opt-in approved control nodes; priority is admin data."""
    r=con.execute("SELECT v FROM kv WHERE k='rental_priority'").fetchone()
    try:
        priority=json.loads(r["v"]) if r else {}
        if not isinstance(priority,dict):
            priority={}
    except (ValueError,TypeError):
        priority={}
    candidates=[]
    for node in con.execute("SELECT name FROM collectors WHERE status='approved' AND type='control'"):
        name=node["name"]
        if _node_idle(con,name) and not _busy_primary(con,name):
            candidates.append((int(priority.get(name,0)),name))
    if not candidates:
        return None
    candidates.sort(key=lambda x:(-x[0],x[1]))
    return candidates[0][1]

def priorities(con):
    r=con.execute("SELECT v FROM kv WHERE k='rental_priority'").fetchone()
    try:
        obj=json.loads(r["v"]) if r else {}
        return obj if isinstance(obj,dict) else {}
    except (ValueError,TypeError):
        return {}

def set_priority(con, name, value):
    if not con.execute("SELECT 1 FROM collectors WHERE name=? AND type='control'",(name,)).fetchone():
        raise ValueError("관제형 수집기를 선택하세요")
    value=int(value)
    if value<0 or value>100:
        raise ValueError("우선순위는 0~100입니다")
    obj=priorities(con)
    obj[name]=value
    con.execute("""INSERT INTO kv(k,v) VALUES('rental_priority',?)
                 ON CONFLICT(k) DO UPDATE SET v=excluded.v""",(json.dumps(obj),))
    con.execute("INSERT INTO rental_admin_events(at,actor,action,detail) VALUES(?,?,?,?)",
                (_now(),"admin","SET_PRIORITY",json.dumps({"collector":name,"priority":value})))
    return obj

def create_member(con, member_id):
    member_id=str(member_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,80}", member_id):
        raise ValueError("member_id invalid")
    member_core.ensure_pilot_member(con,member_id)
    token=secrets.token_urlsafe(32)
    con.execute("""INSERT INTO rental_members(member_id,access_hash,active,created_at)
                   VALUES(?,?,1,?) ON CONFLICT(member_id) DO UPDATE SET
                   access_hash=excluded.access_hash,active=1""", (member_id,_hash(token),_now()))
    return {"member_id":member_id,"access_code":token}

def request(con, member_id, body):
    profile=member_core.profile(con,member_id)
    if profile["status"] not in ("active","pilot"):
        raise ValueError("회원 상태를 확인해 주세요")
    route=str(body.get("route_id") or "").strip()
    if con.execute("SELECT COUNT(*) FROM rental_jobs WHERE member_id=? AND status NOT IN ('DONE','FAILED','CANCELLED')", (member_id,)).fetchone()[0] >= 3:
        raise ValueError("진행 중 신청은 3건까지 가능합니다")
    if route not in {r["route_id"] for r in _catalog(con)}:
        raise ValueError("등록된 작업만 신청할 수 있습니다")
    mode=str(body.get("mode") or "one_time")
    if mode not in ("one_time","reserved"):
        raise ValueError("mode invalid")
    duration=None
    if mode=="reserved":
        hours=int(body.get("hours") or 1)
        if not 1<=hours<=12:
            raise ValueError("예약은 1~12시간입니다")
        duration=hours*3600
    job_id=uuid.uuid4().hex
    stamp=_now()
    con.execute("""INSERT INTO rental_jobs
        (job_id,member_id,route_id,mode,duration_limit,status,note,created_at,updated_at)
        VALUES(?,?,?,?,?,'AWAITING_APPROVAL',?,?,?)""",
        (job_id,member_id,route,mode,duration,
         str(body.get("note") or "")[:240],stamp,stamp))
    _event(con,job_id,member_id,"REQUESTED")
    return _row(con.execute("SELECT * FROM rental_jobs WHERE job_id=?", (job_id,)).fetchone())

def change(con, job_id, action, who="admin"):
    job=con.execute("SELECT * FROM rental_jobs WHERE job_id=?", (job_id,)).fetchone()
    if not job:
        raise ValueError("job not found")
    states={
      "approve":({"AWAITING_APPROVAL"},"PENDING"),
      "pause":({"LEASED","RUNNING","PENDING","RESUME_QUEUED"},"PAUSE_REQUESTED"),
      "resume":({"PAUSED"},"RESUME_QUEUED"),
      "cancel":({"AWAITING_APPROVAL","PENDING","PAUSED","RESUME_QUEUED"},"CANCELLED"),
      "review_resume":({"RECOVERY_REQUIRED"},"RESUME_QUEUED"),
    }
    if action not in states or job["status"] not in states[action][0]:
        raise ValueError("invalid state transition")
    new=states[action][1]
    if action=="pause" and job["status"] in ("PENDING","RESUME_QUEUED"):
        new="PAUSED"
    if action=="review_resume":
        con.execute("""UPDATE rental_jobs SET status=?,collector=NULL,agent_session=NULL,
                     lease_until=0,updated_at=? WHERE job_id=?""",(new,_now(),job_id))
    else:
        con.execute("UPDATE rental_jobs SET status=?,updated_at=? WHERE job_id=?", (new,_now(),job_id))
    _event(con,job_id,who,action.upper())
    return _row(con.execute("SELECT * FROM rental_jobs WHERE job_id=?", (job_id,)).fetchone())

def reconcile_expired(con):
    """Move stale leases to operator review without replaying unfinished steps."""
    rows=con.execute("""SELECT job_id FROM rental_jobs
        WHERE status IN ('LEASED','RUNNING','PAUSE_REQUESTED')
          AND lease_until<=?""", (_now(),)).fetchall()
    for row in rows:
        con.execute("""UPDATE rental_jobs SET status='RECOVERY_REQUIRED',
             last_running_at=NULL,updated_at=? WHERE job_id=?""",(_now(),row["job_id"]))
        _event(con,row["job_id"],"controller","LEASE_EXPIRED")
    return len(rows)

def poll(con, name, agent_session="offline-test"):
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}", agent_session):
        raise ValueError("session_id invalid")
    con.execute("BEGIN IMMEDIATE")
    try:
        stale=con.execute("""SELECT * FROM rental_jobs WHERE collector=? AND
             status IN ('LEASED','RUNNING','PAUSE_REQUESTED')
             AND lease_until<=? ORDER BY created_at LIMIT 1""",(name,_now())).fetchone()
        reconcile_expired(con)
        if stale:
            con.commit()
            return {"action":"PAUSE","job":_row(con.execute(
                "SELECT * FROM rental_jobs WHERE job_id=?",(stale["job_id"],)).fetchone()),
                "reason":"lease_expired"}
        active=con.execute("""SELECT * FROM rental_jobs WHERE collector=? AND
             status IN ('LEASED','RUNNING','PAUSE_REQUESTED') ORDER BY created_at LIMIT 1""",(name,)).fetchone()
        if active and active["last_running_at"] is not None:
            # Account for verified wall time at each lease renewal, including
            # long-running steps that have not emitted progress callbacks.
            stamp=_now()
            delta=max(0,min(LEASE_SECONDS,stamp-active["last_running_at"]))
            used=active["used_seconds"]+delta
            status=active["status"]
            if active["duration_limit"] and used>=active["duration_limit"] and status=="RUNNING":
                status="PAUSE_REQUESTED"
                _event(con,active["job_id"],"controller","RESERVATION_LIMIT")
            con.execute("""UPDATE rental_jobs SET used_seconds=?,last_running_at=?,
                         status=?,updated_at=? WHERE job_id=?""",
                        (used,stamp,status,stamp,active["job_id"]))
            active=con.execute("SELECT * FROM rental_jobs WHERE job_id=?",
                               (active["job_id"],)).fetchone()
        busy=_busy_primary(con,name)
        if active and active["agent_session"] and active["agent_session"] != agent_session:
            con.execute("""UPDATE rental_jobs SET status='RECOVERY_REQUIRED',
                         last_running_at=NULL,updated_at=? WHERE job_id=?""",
                        (_now(),active["job_id"]))
            _event(con,active["job_id"],"controller","SESSION_CHANGED")
            con.commit()
            return {"action":"PAUSE","reason":"session_changed","job":_row(active)}
        if active:
            if busy or active["status"]=="PAUSE_REQUESTED":
                con.execute("UPDATE rental_jobs SET lease_until=? WHERE job_id=?",
                            (_now()+LEASE_SECONDS,active["job_id"]))
                if active["status"]!="PAUSE_REQUESTED":
                    con.execute("UPDATE rental_jobs SET status='PAUSE_REQUESTED',updated_at=? WHERE job_id=?",
                                (_now(),active["job_id"]))
                    _event(con,active["job_id"],"controller","PRIMARY_PRIORITY")
                result={"action":"PAUSE","job":_row(active)}
            else:
                con.execute("UPDATE rental_jobs SET lease_until=?,updated_at=? WHERE job_id=?",
                            (_now()+LEASE_SECONDS,_now(),active["job_id"]))
                result={"action":"RUN","job":_row(con.execute(
                  "SELECT * FROM rental_jobs WHERE job_id=?",(active["job_id"],)).fetchone())}
        elif busy or not _node_idle(con,name):
            result={"action":"WAIT","reason":"primary_work_or_not_ready"}
        else:
            job=con.execute("""SELECT * FROM rental_jobs
              WHERE status IN ('PENDING','RESUME_QUEUED')
              ORDER BY CASE status WHEN 'RESUME_QUEUED' THEN 0 ELSE 1 END,created_at LIMIT 1""").fetchone()
            if not job:
                result={"action":"WAIT"}
            elif _preferred_collector(con)!=name:
                result={"action":"WAIT","reason":"higher_priority_collector"}
            else:
                con.execute("""UPDATE rental_jobs SET status='LEASED',collector=?,
                    agent_session=?,lease_epoch=lease_epoch+1,lease_until=?,updated_at=?
                    WHERE job_id=? AND status IN ('PENDING','RESUME_QUEUED')""",
                    (name,agent_session,_now()+LEASE_SECONDS,_now(),job["job_id"]))
                _event(con,job["job_id"],"controller","ASSIGN",name)
                result={"action":"RUN","job":_row(con.execute("SELECT * FROM rental_jobs WHERE job_id=?",
                    (job["job_id"],)).fetchone())}
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise

def report(con, name, body):
    job_id=str(body.get("job_id") or "")
    epoch=int(body.get("lease_epoch") or -1)
    session_id=str(body.get("session_id") or "offline-test")
    outcome=str(body.get("outcome") or "")
    event_id=str(body.get("event_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}",event_id):
        raise ValueError("event_id invalid")
    if outcome not in ("started","progress","paused","completed","failed"):
        raise ValueError("outcome invalid")
    con.execute("BEGIN IMMEDIATE")
    try:
        old=con.execute("SELECT * FROM rental_events WHERE event_id=?",(event_id,)).fetchone()
        if old:
            if old["job_id"]!=job_id or old["actor"]!=name or old["action"]!=outcome.upper():
                raise ValueError("event conflict")
            con.commit()
            return {"status":"DUPLICATE"}
        job=con.execute("SELECT * FROM rental_jobs WHERE job_id=?",(job_id,)).fetchone()
        if not job or job["collector"]!=name or job["lease_epoch"]!=epoch:
            raise PermissionError("stale job lease")
        if job["agent_session"] and job["agent_session"]!=session_id:
            raise PermissionError("wrong worker session")
        if job["lease_until"]<_now() and outcome!="paused":
            raise PermissionError("job lease expired")
        if job["status"] == "RECOVERY_REQUIRED" and outcome == "paused":
            # Persist last checkpoint but require operator reconciliation.
            cp=max(job["checkpoint"],int(body.get("checkpoint") or 0))
            con.execute("UPDATE rental_jobs SET checkpoint=?,updated_at=? WHERE job_id=?",
                        (cp,_now(),job_id))
            _event(con,job_id,name,outcome.upper(),event_id=event_id)
            con.commit()
            return {"status":"RECOVERY_REQUIRED","checkpoint":cp}
        if job["status"] not in ("LEASED","RUNNING","PAUSE_REQUESTED"):
            raise ValueError("job not active")
        status={"started":"RUNNING","progress":"RUNNING","paused":"PAUSED",
                "completed":"DONE","failed":"FAILED"}[outcome]
        if job["status"]=="PAUSE_REQUESTED" and outcome in ("started","progress"):
            raise PermissionError("pause required")
        # Compute chargeable running time on the controller, never trust a
        # collector-supplied elapsed_seconds value.
        now=_now()
        previous=job["last_running_at"]
        seconds=max(0,min(LEASE_SECONDS,now-previous)) if previous is not None else 0
        used=job["used_seconds"]+seconds
        if job["duration_limit"] and used>=job["duration_limit"] and status=="RUNNING":
            status="PAUSE_REQUESTED"
        cp=max(job["checkpoint"],int(body.get("checkpoint") or 0))
        if cp>1000000:
            raise ValueError("checkpoint invalid")
        running_at=now if status in ("RUNNING","PAUSE_REQUESTED") else None
        con.execute("""UPDATE rental_jobs SET status=?,used_seconds=?,checkpoint=?,
                       last_running_at=?,updated_at=? WHERE job_id=?""",
                    (status,used,cp,running_at,now,job_id))
        _event(con,job_id,name,outcome.upper(),json.dumps({"checkpoint":cp,"seconds":seconds}),event_id)
        con.commit()
        return {"status":status,"checkpoint":cp,"used_seconds":used}
    except Exception:
        con.rollback()
        raise

def route(handler, method, path, query):
    if path not in ("/api/admin/rentals","/api/admin/rentals/member",
                    "/api/admin/rentals/change","/api/admin/rentals/priority",
                    "/api/rentals/catalog",
                    "/api/rentals/jobs","/api/rentals/favorites","/api/collector/rentals/poll",
                    "/api/collector/rentals/report"):
        return False
    if not RENTAL_PILOT_ENABLED:
        handler._json(503,{"ok":False,"error":"임대형 수집기는 준비 중입니다"})
        return True
    try:
        if path.startswith("/api/admin/"):
            if not handler._admin():
                handler._json(403,{"ok":False,"error":"admin authorization required"})
                return True
            with handler.db_module() as con:
                if path=="/api/admin/rentals" and method=="GET":
                    reconcile_expired(con)
                    out={"priority":priorities(con),
                         "jobs":[_row(r) for r in con.execute("SELECT * FROM rental_jobs ORDER BY created_at DESC LIMIT 100")],
                         "members":[{"member_id":r["member_id"],"active":r["active"]} for r in
                                    con.execute("SELECT member_id,active FROM rental_members")]}
                elif path=="/api/admin/rentals/member" and method=="POST":
                    out=create_member(con,handler._body().get("member_id"))
                elif path=="/api/admin/rentals/change" and method=="POST":
                    b=handler._body()
                    out=change(con,str(b.get("job_id")),str(b.get("action")))
                elif path=="/api/admin/rentals/priority" and method=="POST":
                    b=handler._body()
                    out={"priority":set_priority(con,str(b.get("name") or ""),b.get("priority",0))}
                else:
                    handler._json(405,{"ok":False});return True
        elif path.startswith("/api/collector/"):
            if method!="POST":
                handler._json(405,{"ok":False});return True
            b=handler._body()
            name=str(b.get("name") or "")
            with handler.db_module() as con:
                row=handler._collector_ok(con,name)
                if not row or not row["token_hash"] or not handler.headers.get("X-Collector-Token") or row["type"]!="control":
                    handler._json(403,{"ok":False,"error":"collector auth required"})
                    return True
                session_id=str(b.get("session_id") or "")
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{8,120}",session_id):
                    handler._json(400,{"ok":False,"error":"session_id required"})
                    return True
                out=poll(con,name,session_id) if path.endswith("/poll") else report(con,name,b)
        else:
            with handler.db_module() as con:
                if path=="/api/rentals/catalog" and method=="GET":
                    out={"routes":_catalog(con)}
                else:
                    member=_member(con,handler.headers.get("Authorization",""))
                    if not member:
                        handler._json(401,{"ok":False,"error":"회원 인증 필요"});return True
                    if path=="/api/rentals/jobs" and method=="GET":
                        out={"jobs":[_row(r) for r in con.execute(
                            "SELECT * FROM rental_jobs WHERE member_id=? ORDER BY created_at DESC LIMIT 60",
                            (member,))]}
                    elif path=="/api/rentals/jobs" and method=="POST":
                        out={"job":request(con,member,handler._body())}
                    elif path=="/api/rentals/favorites" and method=="GET":
                        out={"favorites":[r["route_id"] for r in con.execute(
                            "SELECT route_id FROM rental_favorites WHERE member_id=? ORDER BY created_at",(member,))]}
                    elif path=="/api/rentals/favorites" and method=="POST":
                        b=handler._body()
                        rid=str(b.get("route_id") or "")
                        if rid not in {x["route_id"] for x in _catalog(con)}:
                            raise ValueError("지원하지 않는 사이트")
                        if b.get("action")=="remove":
                            con.execute("DELETE FROM rental_favorites WHERE member_id=? AND route_id=?",(member,rid))
                        else:
                            n=con.execute("SELECT COUNT(*) FROM rental_favorites WHERE member_id=?",(member,)).fetchone()[0]
                            if n>=10:
                                raise ValueError("즐겨찾기는 10곳까지 등록할 수 있습니다")
                            con.execute("INSERT OR IGNORE INTO rental_favorites VALUES(?,?,?)",(member,rid,_now()))
                        out={"favorites":[r["route_id"] for r in con.execute(
                            "SELECT route_id FROM rental_favorites WHERE member_id=? ORDER BY created_at",(member,))]}
                    else:
                        handler._json(405,{"ok":False});return True
        handler._json(200,{"ok":True,**out})
    except (ValueError,TypeError,sqlite3.IntegrityError) as e:
        handler._json(400,{"ok":False,"error":str(e)[:120]})
    except PermissionError:
        handler._json(409,{"ok":False,"error":"다른 작업이 진행 중이거나 승인 시간이 만료되었습니다"})
    return True
