"""BIDGUARD central member registry and audited multi-bucket point ledger.

This is the controller pilot's canonical member schema. No public login, payment
or self-service balance adjustment is exposed. Existing PWA authentication must
be verified by a trusted server before using an account. Amounts are integer
hundredths of a point (100 units == 1P); never store floating-point money.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
import uuid

POINT_TYPES = ("P", "J", "K", "S")
SPEND_ORDER = ("S", "P", "J", "K")
ROLES = frozenset(("member", "collector_user", "shareholder_member", "realtor",
                   "operator", "admin", "super_admin"))
PRIVILEGED = frozenset(("operator", "admin", "super_admin"))
ID = re.compile(r"^[a-zA-Z0-9_.:-]{3,100}$")
KEY = re.compile(r"^[a-zA-Z0-9_.:-]{12,120}$")
SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
 member_id TEXT PRIMARY KEY, account_status TEXT NOT NULL
   CHECK(account_status IN ('pending','pilot','active','suspended','closed')),
 member_kind TEXT NOT NULL CHECK(member_kind IN ('general','curious','pro','realtor')),
 tier TEXT NOT NULL CHECK(tier IN ('free','paid','regular_pro')),
 realtor_verified INTEGER NOT NULL DEFAULT 0 CHECK(realtor_verified IN (0,1)),
 realtor_evidence_ref TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS member_identities (
 provider TEXT NOT NULL, subject_digest TEXT NOT NULL, member_id TEXT NOT NULL,
 verified_at INTEGER NOT NULL, PRIMARY KEY(provider,subject_digest),
 FOREIGN KEY(member_id) REFERENCES members(member_id));
CREATE TABLE IF NOT EXISTS member_roles (
 member_id TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
 updated_by TEXT NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(member_id,role),FOREIGN KEY(member_id) REFERENCES members(member_id));
CREATE TABLE IF NOT EXISTS member_consents (
 consent_id TEXT PRIMARY KEY,member_id TEXT NOT NULL,
 document_code TEXT NOT NULL, version TEXT NOT NULL, accepted INTEGER NOT NULL,
 verified_source TEXT NOT NULL, recorded_at INTEGER NOT NULL,
 FOREIGN KEY(member_id) REFERENCES members(member_id));
CREATE TABLE IF NOT EXISTS point_balances (
 member_id TEXT NOT NULL, point_type TEXT NOT NULL, balance_units INTEGER NOT NULL DEFAULT 0,
 updated_at INTEGER NOT NULL, PRIMARY KEY(member_id,point_type),
 FOREIGN KEY(member_id) REFERENCES members(member_id),
 CHECK(point_type IN ('P','J','K','S')));
CREATE TABLE IF NOT EXISTS point_transactions (
 transaction_id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
 member_id TEXT NOT NULL, service_id TEXT NOT NULL, action TEXT NOT NULL,
 total_units INTEGER NOT NULL, actor TEXT NOT NULL, created_at INTEGER NOT NULL,
 FOREIGN KEY(member_id) REFERENCES members(member_id));
CREATE TABLE IF NOT EXISTS point_entries (
 entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
 transaction_id TEXT NOT NULL, member_id TEXT NOT NULL,
 point_type TEXT NOT NULL, delta_units INTEGER NOT NULL,
 balance_before_units INTEGER NOT NULL, balance_after_units INTEGER NOT NULL,
 FOREIGN KEY(transaction_id) REFERENCES point_transactions(transaction_id));
CREATE TABLE IF NOT EXISTS member_events (
 event_id TEXT PRIMARY KEY, member_id TEXT NOT NULL, actor TEXT NOT NULL,
 action TEXT NOT NULL, detail TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS member_event_member_idx ON member_events(member_id,created_at);
CREATE TRIGGER IF NOT EXISTS point_txn_no_update BEFORE UPDATE ON point_transactions
 BEGIN SELECT RAISE(ABORT,'append-only point transactions'); END;
CREATE TRIGGER IF NOT EXISTS point_txn_no_delete BEFORE DELETE ON point_transactions
 BEGIN SELECT RAISE(ABORT,'append-only point transactions'); END;
CREATE TRIGGER IF NOT EXISTS point_entries_no_update BEFORE UPDATE ON point_entries
 BEGIN SELECT RAISE(ABORT,'append-only point entries'); END;
CREATE TRIGGER IF NOT EXISTS point_entries_no_delete BEFORE DELETE ON point_entries
 BEGIN SELECT RAISE(ABORT,'append-only point entries'); END;
CREATE TRIGGER IF NOT EXISTS member_events_no_update BEFORE UPDATE ON member_events
 BEGIN SELECT RAISE(ABORT,'append-only member events'); END;
CREATE TRIGGER IF NOT EXISTS member_events_no_delete BEFORE DELETE ON member_events
 BEGIN SELECT RAISE(ABORT,'append-only member events'); END;
"""

def init(con):
    con.executescript(SCHEMA)

def _now():
    return int(time.time())

def _event(con, member, actor, action, detail=""):
    con.execute("""INSERT INTO member_events
       (event_id,member_id,actor,action,detail,created_at) VALUES(?,?,?,?,?,?)""",
       (uuid.uuid4().hex,member,str(actor)[:90],action,str(detail)[:200],_now()))

def _validate_member_id(member_id):
    if not isinstance(member_id,str) or not ID.fullmatch(member_id):
        raise ValueError("invalid member ID")
    return member_id

def _profile(con, member_id):
    return con.execute("SELECT * FROM members WHERE member_id=?", (_validate_member_id(member_id),)).fetchone()

def create_member(con, member_id=None, member_kind="general", status="pending", actor="admin"):
    member_id=_validate_member_id(member_id or ("m_"+uuid.uuid4().hex))
    if member_kind not in ("general","curious","pro","realtor") or status not in ("pending","pilot"):
        raise ValueError("member kind or status invalid")
    stamp=_now()
    con.execute("""INSERT INTO members(member_id,account_status,member_kind,tier,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",(member_id,status,member_kind,"free",stamp,stamp))
    con.executemany("""INSERT INTO point_balances(member_id,point_type,balance_units,updated_at)
                       VALUES(?,?,0,?)""",[(member_id,k,stamp) for k in POINT_TYPES])
    con.execute("""INSERT INTO member_roles(member_id,role,updated_by,updated_at)
                   VALUES(?,?,?,?)""",(member_id,"member",actor,stamp))
    _event(con,member_id,actor,"MEMBER_CREATED",status)
    return {"member_id":member_id,"status":status,"member_kind":member_kind}

def ensure_pilot_member(con, member_id):
    _validate_member_id(member_id)
    if not _profile(con,member_id):
        return create_member(con,member_id,status="pilot",actor="rental_pilot")
    return {"member_id":member_id,"status":_profile(con,member_id)["account_status"]}

def grant_role(con, member_id, role, actor, enabled=True):
    if not _profile(con,member_id):
        raise ValueError("member not found")
    if role not in ROLES or role == "member":
        raise ValueError("invalid role")
    con.execute("""INSERT INTO member_roles(member_id,role,active,updated_by,updated_at)
                   VALUES(?,?,?,?,?) ON CONFLICT(member_id,role)
                   DO UPDATE SET active=excluded.active,updated_by=excluded.updated_by,
                   updated_at=excluded.updated_at""",
                (member_id,role,int(bool(enabled)),actor,_now()))
    _event(con,member_id,actor,"ROLE_GRANTED" if enabled else "ROLE_REVOKED",role)
    return roles(con,member_id)

def roles(con, member_id):
    if not _profile(con,member_id):
        raise ValueError("member not found")
    return [r[0] for r in con.execute(
        "SELECT role FROM member_roles WHERE member_id=? AND active=1 ORDER BY role",
        (member_id,))]

def set_member_status(con, member_id, status, actor):
    if status not in ("suspended","closed"):
        raise ValueError("activation requires verified identity provider")
    if not _profile(con,member_id):
        raise ValueError("member not found")
    con.execute("UPDATE members SET account_status=?,updated_at=? WHERE member_id=?",
                (status,_now(),member_id))
    _event(con,member_id,actor,"ACCOUNT_"+status.upper())
    return status

def link_verified_identity(con, member_id, provider, subject, pepper, actor="auth_server"):
    """ONLY called by trusted, successful OAuth/account verification callback."""
    if not _profile(con,member_id):
        raise ValueError("member not found")
    if provider not in ("google","kakao","naver","email") or not pepper or len(pepper)<32:
        raise ValueError("identity provider or server secret not configured")
    if not subject or len(subject)>512:
        raise ValueError("identity subject invalid")
    digest=hashlib.sha256((pepper+"\0"+provider+"\0"+subject).encode()).hexdigest()
    con.execute("""INSERT INTO member_identities(provider,subject_digest,member_id,verified_at)
                   VALUES(?,?,?,?)""",(provider,digest,member_id,_now()))
    con.execute("UPDATE members SET account_status='active',updated_at=? WHERE member_id=?",
                (_now(),member_id))
    _event(con,member_id,actor,"IDENTITY_VERIFIED",provider)
    return {"member_id":member_id,"provider":provider,"verified":True}

def set_realtor_verified(con, member_id, evidence_ref, actor):
    if not _profile(con,member_id) or not evidence_ref or len(evidence_ref)>140:
        raise ValueError("verified evidence reference required")
    con.execute("UPDATE members SET realtor_verified=1,realtor_evidence_ref=?,updated_at=? WHERE member_id=?",
                (evidence_ref,_now(),member_id))
    _event(con,member_id,actor,"REALTOR_VERIFIED","evidence_checked")
    return True

def wallet(con, member_id):
    if not _profile(con,member_id):
        raise ValueError("member not found")
    out={row["point_type"]:row["balance_units"] for row in
         con.execute("SELECT point_type,balance_units FROM point_balances WHERE member_id=?",(member_id,))}
    return {p:out.get(p,0) for p in POINT_TYPES}

def profile(con, member_id):
    r=_profile(con,member_id)
    if not r:
        raise ValueError("member not found")
    return {"member_id":r["member_id"],"status":r["account_status"],
            "member_kind":r["member_kind"],"tier":r["tier"],
            "realtor_verified":bool(r["realtor_verified"]),
            "roles":roles(con,member_id),"points_units":wallet(con,member_id)}

def _mutate_start(con):
    con.execute("SAVEPOINT member_points")

def _mutate_commit(con):
    con.execute("RELEASE member_points")

def _mutate_rollback(con):
    con.execute("ROLLBACK TO member_points")
    con.execute("RELEASE member_points")

def _existing(con, request_key, member_id, service_id, action, units):
    row=con.execute("SELECT * FROM point_transactions WHERE request_key=?",(request_key,)).fetchone()
    if not row:
        return None
    if (row["member_id"],row["service_id"],row["action"],row["total_units"])!=(
        member_id,service_id,action,units):
        raise ValueError("idempotency key reused with different request")
    return {"transaction_id":row["transaction_id"],"duplicate":True}

def _points_entry(con, txid, member_id, typ, delta):
    row=con.execute("SELECT balance_units FROM point_balances WHERE member_id=? AND point_type=?",
                    (member_id,typ)).fetchone()
    if row is None:
        raise ValueError("wallet not initialized")
    before=int(row[0])
    after=before+delta
    con.execute("UPDATE point_balances SET balance_units=?,updated_at=? WHERE member_id=? AND point_type=?",
                (after,_now(),member_id,typ))
    con.execute("""INSERT INTO point_entries(transaction_id,member_id,point_type,delta_units,
                   balance_before_units,balance_after_units) VALUES(?,?,?,?,?,?)""",
                (txid,member_id,typ,delta,before,after))
    return after

def _validate_transaction(con, member_id, service_id, units, request_key):
    row=_profile(con,member_id)
    if not row or row["account_status"] not in ("active","pilot"):
        raise ValueError("member not active")
    if row["account_status"]=="pilot" and os.environ.get("BIDGUARD_MEMBER_TEST_MODE")!="1":
        raise ValueError("pilot wallet disabled")
    if not isinstance(service_id,str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}",service_id):
        raise ValueError("invalid service ID")
    if type(units) is not int or units<=0 or units>10_000_000:
        raise ValueError("invalid amount in 0.01P units")
    if not isinstance(request_key,str) or not KEY.fullmatch(request_key):
        raise ValueError("invalid idempotency key")

def charge_internal(con, member_id, service_id, units, request_key,
                    actor="billing_gate", overdraft_units=0):
    """Internal billing adapter ONLY; service pricing must be verified upstream.

    Positive balances are spent S>P>J>K; S is ineligible for shopping.
    Approved collector-only overdraft (max -200P) applies to P, not all services.
    """
    _validate_transaction(con,member_id,service_id,units,request_key)
    if overdraft_units and (not service_id.startswith("COLLECT_") or
                            type(overdraft_units) is not int or not 0<=overdraft_units<=20_000):
        raise ValueError("collector overdraft policy invalid")
    _mutate_start(con)
    try:
        old=_existing(con,request_key,member_id,service_id,"CHARGE",units)
        if old:
            _mutate_commit(con)
            return old
        bal=wallet(con,member_id)
        available=sum(max(0,bal[k]) for k in SPEND_ORDER
                      if not (k=="S" and service_id.startswith("SHOPPING_")))
        # Available overdraft is the unused portion of the -200P limit on P only.
        extra=max(0,overdraft_units+min(0,bal["P"]))
        if available+extra<units:
            raise ValueError("INSUFFICIENT_POINTS")
        txn=uuid.uuid4().hex
        con.execute("""INSERT INTO point_transactions
             (transaction_id,request_key,member_id,service_id,action,total_units,actor,created_at)
             VALUES(?,?,?,?,?,?,?,?)""",
             (txn,request_key,member_id,service_id,"CHARGE",units,actor,_now()))
        remain=units
        for typ in SPEND_ORDER:
            if typ=="S" and service_id.startswith("SHOPPING_"):
                continue
            take=min(remain,max(0,bal[typ]))
            if take:
                _points_entry(con,txn,member_id,typ,-take)
                remain-=take
        if remain:
            _points_entry(con,txn,member_id,"P",-remain)
            remain=0
        _event(con,member_id,actor,"POINT_CHARGE",service_id)
        _mutate_commit(con)
        return {"transaction_id":txn,"duplicate":False}
    except Exception:
        _mutate_rollback(con)
        raise

def test_grant(con, member_id, point_type, units, request_key, actor="admin_test"):
    """Offline trial credit only. This never confirms an external payment."""
    if os.environ.get("BIDGUARD_MEMBER_TEST_MODE")!="1":
        raise PermissionError("test credit disabled")
    _validate_transaction(con,member_id,"TEST_GRANT",units,request_key)
    if point_type not in POINT_TYPES:
        raise ValueError("invalid point type")
    _mutate_start(con)
    try:
        old=_existing(con,request_key,member_id,"TEST_GRANT_"+point_type,"CREDIT",units)
        if old:
            _mutate_commit(con)
            return old
        txn=uuid.uuid4().hex
        con.execute("""INSERT INTO point_transactions
          (transaction_id,request_key,member_id,service_id,action,total_units,actor,created_at)
          VALUES(?,?,?,?,?,?,?,?)""",
          (txn,request_key,member_id,"TEST_GRANT_"+point_type,"CREDIT",units,actor,_now()))
        _points_entry(con,txn,member_id,point_type,units)
        _event(con,member_id,actor,"TEST_CREDIT",point_type)
        _mutate_commit(con)
        return {"transaction_id":txn,"duplicate":False}
    except Exception:
        _mutate_rollback(con)
        raise

def _super_admin(handler):
    secret=os.environ.get("BIDGUARD_SUPER_ADMIN_TOKEN","")
    token=handler.headers.get("X-Super-Admin-Token","")
    return bool(secret) and secrets.compare_digest(secret,token)

def route(handler, method, path, query):
    routes={"/api/admin/members","/api/admin/members/detail",
            "/api/admin/members/role","/api/admin/members/status",
            "/api/admin/members/test-credit"}
    if path not in routes:
        return False
    if not handler._admin():
        handler._json(403,{"ok":False,"error":"관리자 권한이 필요합니다"})
        return True
    try:
        with handler.db_module() as con:
            if path=="/api/admin/members" and method=="GET":
                rows=con.execute("""SELECT member_id,account_status,member_kind,tier,
                                   realtor_verified,created_at FROM members
                                   ORDER BY created_at DESC LIMIT 150""").fetchall()
                result={"members":[dict(r) for r in rows]}
            elif path=="/api/admin/members" and method=="POST":
                b=handler._body()
                result={"member":create_member(con,b.get("member_id"),b.get("member_kind","general"),
                                               status="pilot",actor="admin")}
            elif path=="/api/admin/members/detail" and method=="GET":
                result={"member":profile(con,str(query.get("member_id") or ""))}
            elif path=="/api/admin/members/role" and method=="POST":
                b=handler._body()
                if str(b.get("role")) in PRIVILEGED and not _super_admin(handler):
                    handler._json(403,{"ok":False,"error":"최고관리자 승인이 필요합니다"})
                    return True
                result={"roles":grant_role(con,str(b.get("member_id") or ""),
                                           str(b.get("role") or ""),actor="admin",
                                           enabled=b.get("enabled") is True)}
            elif path=="/api/admin/members/status" and method=="POST":
                b=handler._body()
                result={"status":set_member_status(con,str(b.get("member_id") or ""),
                                                   str(b.get("status") or ""),actor="admin")}
            elif path=="/api/admin/members/test-credit" and method=="POST":
                if not _super_admin(handler):
                    handler._json(403,{"ok":False,"error":"시험 포인트 발급 권한이 필요합니다"})
                    return True
                b=handler._body()
                result={"transaction":test_grant(con,str(b.get("member_id") or ""),
                      str(b.get("point_type") or ""),b.get("units"),str(b.get("request_key") or ""))}
            else:
                handler._json(405,{"ok":False,"error":"method not allowed"})
                return True
        handler._json(200,{"ok":True,**result})
    except (ValueError,sqlite3.IntegrityError) as exc:
        handler._json(400,{"ok":False,"error":str(exc)[:130]})
    except PermissionError:
        handler._json(403,{"ok":False,"error":"not authorized"})
    return True
