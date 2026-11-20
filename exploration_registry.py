"""BIDGUARD exploration knowledge exchange, controller as source of truth.

Separate from existing learning.for_visit and site-map-ingest. Collectors may
suggest navigation *metadata*, never code, credentials, comments, screen pixels
or private documents. A human controller administrator approves before any
other collector can download the versioned result.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import os
import re
import ssl
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import learning

SITE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
DEVICE_ID = re.compile(r"^[A-Za-z0-9._@:-]{2,90}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{12,120}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
PAGE_KINDS = frozenset(("home","search","list","detail","document","other"))
ELEMENT_NAMES = frozenset(("search","search_submit","menu","filter","results","next_page",
                           "case_number","document_link","status","navigation","other"))
MAX_ELEMENTS = 40
MAX_BYTES = 48_000
DEFAULT_SITES = []

SCHEMA = """
CREATE TABLE IF NOT EXISTS exploration_proposals (
 proposal_id TEXT PRIMARY KEY, collector TEXT NOT NULL, request_key TEXT NOT NULL,
 site_id TEXT NOT NULL, status TEXT NOT NULL, content_sha256 TEXT NOT NULL,
 payload TEXT NOT NULL, submitted_at INTEGER NOT NULL, decided_at INTEGER,
 decided_by TEXT, UNIQUE(collector,request_key));
CREATE INDEX IF NOT EXISTS exploration_proposals_pending
 ON exploration_proposals(status,submitted_at);
CREATE TABLE IF NOT EXISTS exploration_versions (
 site_id TEXT NOT NULL, version INTEGER NOT NULL, proposal_id TEXT NOT NULL UNIQUE,
 content_sha256 TEXT NOT NULL, payload TEXT NOT NULL, approved_at INTEGER NOT NULL,
 PRIMARY KEY(site_id,version));
CREATE TABLE IF NOT EXISTS exploration_events (
 event_id TEXT PRIMARY KEY, at INTEGER NOT NULL, actor TEXT NOT NULL,
 action TEXT NOT NULL, site_id TEXT NOT NULL, proposal_id TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS exploration_versions_no_update BEFORE UPDATE ON exploration_versions
 BEGIN SELECT RAISE(ABORT,'approved navigation version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS exploration_versions_no_delete BEFORE DELETE ON exploration_versions
 BEGIN SELECT RAISE(ABORT,'approved navigation version is immutable'); END;
CREATE TRIGGER IF NOT EXISTS exploration_events_no_delete BEFORE DELETE ON exploration_events
 BEGIN SELECT RAISE(ABORT,'navigation event is immutable'); END;
CREATE TRIGGER IF NOT EXISTS exploration_events_no_update BEFORE UPDATE ON exploration_events
 BEGIN SELECT RAISE(ABORT,'navigation event is immutable'); END;
"""

def init(con, sites=None):
    global DEFAULT_SITES
    DEFAULT_SITES = sites if isinstance(sites,list) else []
    con.executescript(SCHEMA)

def _canonical(obj):
    return json.dumps(obj,ensure_ascii=False,sort_keys=True,
                      separators=(",",":"),allow_nan=False).encode("utf-8")

def _now():
    return int(time.time())

def _event(con,actor,action,site,proposal,detail=""):
    con.execute("""INSERT INTO exploration_events
                  (event_id,at,actor,action,site_id,proposal_id,detail)
                  VALUES(?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex,_now(),actor[:90],action,site,proposal,detail[:140]))

def _site(con,site_id):
    if not isinstance(site_id,str) or not SITE_ID.fullmatch(site_id):
        raise ValueError("등록된 사이트를 선택하세요")
    row=con.execute("SELECT v FROM kv WHERE k='sites'").fetchone()
    sites=json.loads(row[0]) if row else DEFAULT_SITES
    if not isinstance(sites,list):
        raise ValueError("사이트 목록 오류")
    for obj in sites:
        if isinstance(obj,dict) and obj.get("site_id")==site_id:
            url=str(obj.get("url") or "")
            parsed=urlsplit(url)
            if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("등록된 사이트 주소를 확인하세요")
            return {"site_id":site_id,"name":str(obj.get("name") or site_id)[:90],
                    "host":parsed.hostname.lower(),"url":url}
    raise ValueError("등록된 사이트가 아닙니다")

def _validate(snapshot,site):
    if not isinstance(snapshot,dict):
        raise ValueError("탐색자료 형식 오류")
    if set(snapshot)!={"url","captured_at","page_kind","structure_sha256","elements"}:
        raise ValueError("허용되지 않은 탐색자료 필드")
    url=snapshot["url"]
    if not isinstance(url,str) or len(url)>500:
        raise ValueError("탐색 주소 오류")
    parsed=urlsplit(url)
    if (parsed.scheme!="https" or (parsed.hostname or "").lower()!=site["host"]
        or parsed.username or parsed.password or parsed.port not in (None,443)
        or parsed.query or parsed.fragment):
        raise ValueError("등록 사이트 HTTPS 페이지의 공개 경로만 전송하세요")
    if not isinstance(snapshot["captured_at"],str):
        raise ValueError("수집시각 오류")
    try:
        stamp=datetime.fromisoformat(snapshot["captured_at"].replace("Z","+00:00"))
        if stamp.tzinfo is None or abs(stamp.timestamp()-time.time())>86400*31:
            raise ValueError("수집시각 범위 오류")
    except (ValueError,OverflowError) as exc:
        raise ValueError("수집시각 오류") from exc
    kind=snapshot["page_kind"]
    if kind not in PAGE_KINDS:
        raise ValueError("페이지 유형 오류")
    fingerprint=snapshot["structure_sha256"]
    if not isinstance(fingerprint,str) or not SHA.fullmatch(fingerprint):
        raise ValueError("화면 구조 해시 오류")
    elements=snapshot["elements"]
    if not isinstance(elements,list) or len(elements)>MAX_ELEMENTS:
        raise ValueError("탐색 대상 수 제한 초과")
    valid=[]
    for obj in elements:
        if not isinstance(obj,dict) or set(obj)!={"name","selector"}:
            raise ValueError("탐색 요소는 식별자와 선택자만 사용")
        name,selector=obj["name"],obj["selector"]
        if name not in ELEMENT_NAMES or not isinstance(selector,str) or not 1<=len(selector)<=160:
            raise ValueError("탐색 요소 형식 오류")
        # CSS selectors are treated as text, never as arbitrary JS execution.
        if any(ord(c)<32 for c in selector) or re.search(
                r"(?:javascript:|<|>|\\|cookie|password|token|authorization|session|secret)",
                selector,re.I):
            raise ValueError("민감한 필드가 포함된 선택자")
        valid.append({"name":name,"selector":selector})
    clean={"url":url,"captured_at":stamp.isoformat(),"page_kind":kind,
           "structure_sha256":fingerprint,"elements":valid}
    if len(_canonical(clean))>MAX_BYTES:
        raise ValueError("탐색자료 크기 초과")
    return clean

def _registered(con,handler,name):
    if not isinstance(name,str) or not DEVICE_ID.fullmatch(name):
        return False
    row=con.execute("SELECT * FROM collectors WHERE name=?",(name,)).fetchone()
    token=handler.headers.get("X-Collector-Token","")
    if (row is None or row["status"]!="approved" or not row["token_hash"]
        or len(token)<12):
        return False
    return hmac.compare_digest(row["token_hash"],
                               hashlib.sha256(token.encode("utf-8")).hexdigest())

def _secure_transport(handler):
    """Local proxy or genuine TLS only; never trust remote X-Forwarded-Proto."""
    addr=handler.client_address[0]
    return isinstance(handler.connection,ssl.SSLSocket) or addr in ("127.0.0.1","::1")

def propose(con,collector,request_key,site_id,snapshot):
    if not DEVICE_ID.fullmatch(collector) or not isinstance(request_key,str) or not REQUEST_ID.fullmatch(request_key):
        raise ValueError("요청번호 또는 수집기 오류")
    site=_site(con,site_id)
    clean=_validate(snapshot,site)
    payload=_canonical(clean)
    checksum=hashlib.sha256(payload).hexdigest()
    con.execute("SAVEPOINT exploration_proposal")
    try:
        prior=con.execute("""SELECT proposal_id,site_id,content_sha256,status FROM exploration_proposals
                             WHERE collector=? AND request_key=?""",
                          (collector,request_key)).fetchone()
        if prior:
            if prior["content_sha256"]!=checksum or prior["site_id"]!=site_id:
                raise ValueError("같은 요청번호에 다른 탐색자료를 등록할 수 없습니다")
            out={"proposal_id":prior["proposal_id"],"status":prior["status"],
                 "duplicate":True,"sha256":checksum}
        else:
            prior_content=con.execute("""SELECT proposal_id,status FROM exploration_proposals
               WHERE collector=? AND site_id=? AND content_sha256=?
               ORDER BY submitted_at DESC LIMIT 1""",(collector,site_id,checksum)).fetchone()
            if prior_content:
                out={"proposal_id":prior_content["proposal_id"],"status":prior_content["status"],
                     "duplicate":True,"sha256":checksum}
            else:
                pid=uuid.uuid4().hex
                con.execute("""INSERT INTO exploration_proposals
                   (proposal_id,collector,request_key,site_id,status,content_sha256,payload,submitted_at)
                   VALUES(?,?,?,?,'PENDING',?,?,?)""",
                   (pid,collector,request_key,site_id,checksum,payload.decode("utf-8"),_now()))
                _event(con,collector,"PROPOSE",site_id,pid,checksum)
                out={"proposal_id":pid,"status":"PENDING","duplicate":False,"sha256":checksum}
        con.execute("RELEASE exploration_proposal")
        return out
    except Exception:
        con.execute("ROLLBACK TO exploration_proposal")
        con.execute("RELEASE exploration_proposal")
        raise

def decide(con,proposal_id,action,actor="admin"):
    if action not in ("approve","reject"):
        raise ValueError("승인 또는 반려를 선택하세요")
    con.execute("SAVEPOINT exploration_decision")
    try:
        p=con.execute("SELECT * FROM exploration_proposals WHERE proposal_id=?",(proposal_id,)).fetchone()
        if p is None:
            raise ValueError("탐색 제안이 없습니다")
        if p["status"]!="PENDING":
            raise ValueError("이미 검토된 탐색 제안입니다")
        version=None
        if action=="approve":
            last=con.execute("""SELECT COALESCE(MAX(version),0) FROM exploration_versions
                                 WHERE site_id=?""",(p["site_id"],)).fetchone()[0]
            version=last+1
            con.execute("""INSERT INTO exploration_versions(site_id,version,proposal_id,content_sha256,
                         payload,approved_at) VALUES(?,?,?,?,?,?)""",
                        (p["site_id"],version,proposal_id,p["content_sha256"],p["payload"],_now()))
        con.execute("""UPDATE exploration_proposals SET status=?,decided_at=?,decided_by=?
                       WHERE proposal_id=?""",
                    ("APPROVED" if action=="approve" else "REJECTED",_now(),actor,proposal_id))
        _event(con,actor,action.upper(),p["site_id"],proposal_id,str(version or ""))
        con.execute("RELEASE exploration_decision")
        return {"status":"APPROVED" if action=="approve" else "REJECTED",
                "site_id":p["site_id"],"version":version}
    except Exception:
        con.execute("ROLLBACK TO exploration_decision")
        con.execute("RELEASE exploration_decision")
        raise

def sync(con,collector,site_id,have_version=0):
    site=_site(con,site_id)
    if str(have_version) and not str(have_version).isdigit():
        raise ValueError("버전 오류")
    have=int(have_version or 0)
    if have<0 or have>1_000_000:
        raise ValueError("버전 오류")
    latest=con.execute("""SELECT version,content_sha256,payload,approved_at
                          FROM exploration_versions WHERE site_id=?
                          ORDER BY version DESC LIMIT 1""",(site_id,)).fetchone()
    learning_book=learning.for_visit(con,collector,site_id,None)
    response={"ok":True,"site_id":site_id,"site_name":site["name"],
              "version":int(latest["version"]) if latest else 0,
              "unchanged":not latest or int(latest["version"])==have,
              "learning":learning_book}
    if latest:
        response["sha256"]=latest["content_sha256"]
        if int(latest["version"])!=have:
            response["navigation"]=json.loads(latest["payload"])
            response["approved_at"]=latest["approved_at"]
    return response

def catalog(con):
    """All registered sites, including sites with only an approved learning book."""
    row=con.execute("SELECT v FROM kv WHERE k='sites'").fetchone()
    sites=json.loads(row[0]) if row else DEFAULT_SITES
    versions=con.execute("""SELECT v.site_id,v.version,v.content_sha256,v.approved_at
        FROM exploration_versions v INNER JOIN
        (SELECT site_id,MAX(version) m FROM exploration_versions GROUP BY site_id) recent
        ON recent.site_id=v.site_id AND recent.m=v.version ORDER BY v.site_id""").fetchall()
    known={r["site_id"]:dict(r) for r in versions}
    out=[]
    for site in (sites if isinstance(sites,list) else []):
        sid=site.get("site_id") if isinstance(site,dict) else None
        if isinstance(sid,str) and SITE_ID.fullmatch(sid):
            out.append({"site_id":sid,"version":0,**known.get(sid,{})})
    return out

def state(con):
    pending=con.execute("""SELECT proposal_id,collector,site_id,content_sha256,payload,
                 submitted_at FROM exploration_proposals WHERE status='PENDING'
                 ORDER BY submitted_at LIMIT 100""").fetchall()
    return {"pending":[{**{k:r[k] for k in ("proposal_id","collector","site_id","content_sha256",
                                           "submitted_at")},"navigation":json.loads(r["payload"])}
                       for r in pending],"latest":catalog(con)}

def route(handler,method,path,query):
    paths={"/api/collector/exploration/catalog","/api/collector/exploration/sync",
           "/api/collector/exploration/propose","/api/admin/exploration/state",
           "/api/admin/exploration/decision"}
    if path not in paths:
        return False
    if not _secure_transport(handler):
        handler._json(403,{"ok":False,"error":"보안 연결이 필요합니다"})
        return True
    try:
        if path.startswith("/api/admin/"):
            if not handler._admin():
                handler._json(403,{"ok":False})
                return True
            with handler.db_module() as con:
                if path.endswith("/state") and method=="GET":
                    answer=state(con)
                elif path.endswith("/decision") and method=="POST":
                    body=handler._body()
                    answer=decide(con,str(body.get("proposal_id") or ""),
                                  str(body.get("action") or ""))
                else:
                    handler._json(405,{"ok":False});return True
        else:
            body=handler._body() if method=="POST" else {}
            name=str(body.get("name") if method=="POST" else query.get("name") or "")
            with handler.db_module() as con:
                if not _registered(con,handler,name):
                    handler._json(403,{"ok":False,"error":"등록된 수집기 인증 필요"})
                    return True
                if path.endswith("/catalog") and method=="GET":
                    answer={"latest":catalog(con)}
                elif path.endswith("/sync") and method=="GET":
                    answer=sync(con,name,str(query.get("site_id") or ""),
                                query.get("have_version",0))
                elif path.endswith("/propose") and method=="POST":
                    answer=propose(con,name,str(body.get("request_key") or ""),
                                   str(body.get("site_id") or ""),body.get("snapshot"))
                else:
                    handler._json(405,{"ok":False});return True
        handler._json(200,{"ok":True,**answer})
    except (ValueError,TypeError,OverflowError,sqlite3.IntegrityError) as exc:
        handler._json(400,{"ok":False,"error":str(exc)[:180]})
    return True
