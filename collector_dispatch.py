"""BIDGUARD two-collector central lease queue. Additive, stdlib only."""
import hashlib
import hmac
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

KINDS = {'appraisal', 'sale_spec', 'registry', 'case_status', 'other'}
SHA = re.compile(r'^[a-f0-9]{64}$')
LEASE_SECONDS = 900

def init(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS central_jobs (
      job_id TEXT PRIMARY KEY,
      work_key TEXT NOT NULL UNIQUE,
      source_id TEXT NOT NULL,
      court_id TEXT NOT NULL,
      case_no TEXT NOT NULL,
      item_no TEXT NOT NULL,
      doc_type TEXT NOT NULL,
      doc_version TEXT NOT NULL,
      target_name TEXT,
      priority INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'PENDING',
      assignee TEXT,
      lease_epoch INTEGER NOT NULL DEFAULT 0,
      lease_until INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL);
    CREATE INDEX IF NOT EXISTS central_jobs_claim_idx
      ON central_jobs(status,target_name,priority,created_at);
    CREATE TABLE IF NOT EXISTS central_job_events (
      event_id TEXT PRIMARY KEY,
      job_id TEXT NOT NULL,
      collector TEXT NOT NULL,
      lease_epoch INTEGER NOT NULL,
      kind TEXT NOT NULL,
      payload_sha256 TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      FOREIGN KEY(job_id) REFERENCES central_jobs(job_id));
    CREATE TABLE IF NOT EXISTS central_job_reports (
      job_id TEXT PRIMARY KEY,
      event_id TEXT NOT NULL UNIQUE,
      outcome TEXT NOT NULL,
      sha256 TEXT,
      size_bytes INTEGER,
      nas_path TEXT,
      verified_at INTEGER,
      reported_at INTEGER NOT NULL,
      FOREIGN KEY(job_id) REFERENCES central_jobs(job_id));
    """)

def _job(row):
    return {k: row[k] for k in ('job_id','source_id','court_id','case_no','item_no','doc_type',
                                'doc_version','target_name','priority','status','assignee',
                                'lease_epoch','lease_until')} if row else None

def _clock():
    return int(time.time())

def _work_key(b):
    values = [str(b.get(k, '')).strip() for k in
              ('source_id','court_id','case_no','item_no','doc_type','doc_version')]
    if any(not v or len(v) > 120 for v in values):
        raise ValueError('source_id/court_id/case_no/item_no/doc_type/doc_version required (1..120)')
    if values[4] not in KINDS:
        raise ValueError('unknown doc_type')
    return json.dumps(values, ensure_ascii=False, separators=(',', ':'))

def enqueue(con, b):
    key = _work_key(b)
    target = b.get('target_name') or None
    if target and (not isinstance(target, str) or len(target) > 60):
        raise ValueError('bad target_name')
    priority = int(b.get('priority', 0))
    if not -100 <= priority <= 100:
        raise ValueError('bad priority')
    now = _clock()
    con.execute('BEGIN IMMEDIATE')
    try:
        con.execute("""INSERT OR IGNORE INTO central_jobs
        (job_id,work_key,source_id,court_id,case_no,item_no,doc_type,doc_version,
         target_name,priority,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uuid.uuid4().hex,key,*json.loads(key),target,priority,now,now))
        row = con.execute('SELECT * FROM central_jobs WHERE work_key=?',(key,)).fetchone()
        con.commit()
        return _job(row)
    except Exception:
        con.rollback()
        raise

def claim(con, name):
    now = _clock()
    con.execute('BEGIN IMMEDIATE')
    try:
        # Expired work requires reconciliation, never blind duplicate allocation.
        con.execute("UPDATE central_jobs SET status='REVIEW',updated_at=? WHERE status IN ('LEASED','ACKED') AND lease_until<=?",
                    (now,now))
        own = con.execute("""SELECT * FROM central_jobs WHERE assignee=?
          AND status IN ('LEASED','ACKED') AND lease_until>? ORDER BY created_at LIMIT 1""",(name,now)).fetchone()
        if own:
            result = _job(own)
        else:
            job = con.execute("""SELECT * FROM central_jobs WHERE status='PENDING'
                AND (target_name IS NULL OR target_name=?) ORDER BY
                CASE WHEN target_name=? THEN 0 ELSE 1 END,
                priority DESC,created_at ASC,job_id ASC LIMIT 1""",(name,name)).fetchone()
            if job:
                con.execute("UPDATE central_jobs SET status='LEASED',assignee=?,lease_epoch=lease_epoch+1,lease_until=?,updated_at=? WHERE job_id=? AND status='PENDING'",
                            (name,now+LEASE_SECONDS,now,job['job_id']))
                job = con.execute('SELECT * FROM central_jobs WHERE job_id=?',(job['job_id'],)).fetchone()
            result = _job(job)
        con.commit()
        return result
    except Exception:
        con.rollback()
        raise

def _owned(con, name, b, allowed):
    job = con.execute('SELECT * FROM central_jobs WHERE job_id=?',(str(b.get('job_id','')),)).fetchone()
    if not job or job['assignee'] != name or job['status'] not in allowed or job['lease_epoch'] != int(b.get('lease_epoch',-1)):
        raise PermissionError('job lease does not belong to collector')
    if job['lease_until'] <= _clock():
        raise PermissionError('job lease expired; reconcile with controller')
    return job

def ack(con, name, b):
    con.execute('BEGIN IMMEDIATE')
    try:
        job = _owned(con,name,b,('LEASED','ACKED'))
        now = _clock()
        con.execute("UPDATE central_jobs SET status='ACKED',lease_until=?,updated_at=? WHERE job_id=?",
                    (now+LEASE_SECONDS,now,job['job_id']))
        con.commit()
        return {'job_id':job['job_id'],'status':'ACKED','lease_until':now+LEASE_SECONDS}
    except Exception:
        con.rollback()
        raise

def report(con, name, b):
    event_id = str(b.get('event_id') or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{8,120}', event_id):
        raise ValueError('event_id required')
    outcome = b.get('outcome')
    if outcome not in ('success','failure','duplicate'):
        raise ValueError('bad outcome')
    sha = str(b.get('sha256') or '').lower()
    size = b.get('size_bytes')
    if outcome == 'success' and (not SHA.fullmatch(sha) or type(size) is not int or size <= 0):
        raise ValueError('success requires sha256 and positive size_bytes')
    payload = json.dumps({k:b.get(k) for k in ('job_id','lease_epoch','outcome','sha256','size_bytes')},
                         sort_keys=True,separators=(',',':'))
    digest = hashlib.sha256(payload.encode()).hexdigest()
    con.execute('BEGIN IMMEDIATE')
    try:
        existing = con.execute('SELECT payload_sha256,job_id FROM central_job_events WHERE event_id=?',(event_id,)).fetchone()
        if existing:
            if not hmac.compare_digest(existing['payload_sha256'],digest):
                raise FileExistsError('same event_id with different content')
            con.commit()
            return {'status':'DUPLICATE_DELIVERY','event_id':event_id}
        job = _owned(con,name,b,('ACKED',))
        now = _clock()
        con.execute('INSERT INTO central_job_events VALUES (?,?,?,?,?,?,?)',
                    (event_id,job['job_id'],name,job['lease_epoch'],'report',digest,now))
        con.execute('INSERT INTO central_job_reports (job_id,event_id,outcome,sha256,size_bytes,reported_at) VALUES (?,?,?,?,?,?)',
                    (job['job_id'],event_id,outcome,sha or None,size,now))
        state = 'REPORTED' if outcome == 'success' else ('SKIPPED' if outcome == 'duplicate' else 'FAILED')
        con.execute('UPDATE central_jobs SET status=?,updated_at=? WHERE job_id=?',(state,now,job['job_id']))
        con.commit()
        return {'job_id':job['job_id'],'status':state,'verified':False}
    except Exception:
        con.rollback()
        raise

def reconcile(con, b):
    job_id = str(b.get('job_id') or '')
    con.execute('BEGIN IMMEDIATE')
    try:
        row = con.execute('SELECT * FROM central_jobs WHERE job_id=?',(job_id,)).fetchone()
        if not row or row['status'] not in ('REVIEW','FAILED'):
            raise ValueError('only REVIEW/FAILED jobs may be manually requeued')
        con.execute("""UPDATE central_jobs SET status='PENDING',assignee=NULL,lease_until=0,updated_at=? WHERE job_id=?""",
                    (_clock(),job_id))
        con.commit()
        return {'job_id':job_id,'status':'PENDING'}
    except Exception:
        con.rollback()
        raise

def verify(con,b):
    """Confirm actual NAS bytes, not the collector's asserted checksum."""
    root = os.environ.get('BIDGUARD_NAS_ROOT','')
    if not root or not os.path.ismount(root):
        raise ValueError('NAS not mounted')
    rel = str(b.get('nas_relative_path') or '')
    if not rel or Path(rel).is_absolute() or '..' in Path(rel).parts:
        raise ValueError('invalid relative NAS path')
    base = Path(root).resolve(strict=True)
    candidate = (base/rel).resolve(strict=True)
    if base not in candidate.parents or not candidate.is_file():
        raise ValueError('path outside NAS or not a file')
    with con:
        row = con.execute("""SELECT j.status,r.sha256,r.size_bytes FROM central_jobs j JOIN central_job_reports r
                             ON j.job_id=r.job_id WHERE j.job_id=?""",(str(b.get('job_id') or ''),)).fetchone()
        if not row or row['status'] != 'REPORTED':
            raise ValueError('not awaiting file verification')
        h = hashlib.sha256()
        total = 0
        with candidate.open('rb') as file:
            for buf in iter(lambda: file.read(1024*1024), b''):
                h.update(buf)
                total += len(buf)
        if total != row['size_bytes'] or not hmac.compare_digest(h.hexdigest(),row['sha256']):
            raise ValueError('NAS SHA/size mismatch')
        con.execute('UPDATE central_job_reports SET verified_at=?,nas_path=? WHERE job_id=?',
                    (_clock(),rel,str(b['job_id'])))
        con.execute("UPDATE central_jobs SET status='VERIFIED',updated_at=? WHERE job_id=?",(_clock(),str(b['job_id'])))
    return {'job_id':str(b['job_id']),'status':'VERIFIED','sha256':h.hexdigest()}

def route(handler, method, path, query):
    if path not in ('/api/admin/jobs','/api/admin/jobs/reconcile','/api/admin/jobs/verify',
                    '/api/collector/jobs/claim','/api/collector/jobs/ack','/api/collector/jobs/report'):
        return False
    if path.startswith('/api/admin/'):
        if not handler._admin():
            handler._json(403,{'ok':False,'error':'admin auth required'})
            return True
    else:
        body = handler._body() if method == 'POST' else {}
        name = str(body.get('name') or '')
        with handler.db_module() as con:
            row = handler._collector_ok(con,name)
            if (not row or not row['token_hash'] or
                not handler.headers.get('X-Collector-Token')):
                handler._json(403,{'ok':False,'error':'per-device collector token required'})
                return True
    try:
        if path == '/api/admin/jobs' and method == 'GET':
            with handler.db_module() as con:
                rows = con.execute('SELECT * FROM central_jobs ORDER BY created_at DESC,job_id DESC LIMIT 150').fetchall()
            out = {'ok':True,'jobs':[_job(r) for r in rows]}
        elif path == '/api/admin/jobs' and method == 'POST':
            with handler.db_module() as con:
                out = {'ok':True,'job':enqueue(con,handler._body())}
        elif path == '/api/admin/jobs/reconcile' and method == 'POST':
            with handler.db_module() as con:
                out = {'ok':True,**reconcile(con,handler._body())}
        elif path == '/api/admin/jobs/verify' and method == 'POST':
            with handler.db_module() as con:
                out = {'ok':True,**verify(con,handler._body())}
        elif path == '/api/collector/jobs/claim' and method == 'POST':
            with handler.db_module() as con:
                out = {'ok':True,'job':claim(con,name)}
        elif path == '/api/collector/jobs/ack' and method == 'POST':
            with handler.db_module() as con:
                out = {'ok':True,**ack(con,name,body)}
        elif path == '/api/collector/jobs/report' and method == 'POST':
            with handler.db_module() as con:
                out = {'ok':True,**report(con,name,body)}
        else:
            handler._json(405,{'ok':False,'error':'method not allowed'})
            return True
        handler._json(200,out)
    except (PermissionError,FileExistsError) as exc:
        handler._json(409,{'ok':False,'error':str(exc)})
    except (ValueError,KeyError,TypeError,OSError,sqlite3.IntegrityError) as exc:
        handler._json(400,{'ok':False,'error':str(exc)[:160]})
    return True
