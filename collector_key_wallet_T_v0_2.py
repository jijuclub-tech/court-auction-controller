#!/usr/bin/env python3
"""BIDGUARD collector device wallet T-v0.2.

Administrator provisions many *different* 256-bit device tokens at once.
Tokens live AES-256-GCM encrypted inside the existing administrator wallet.
The controller verifies a SHA-256 verifier per request, without loading the master.
One-time, 256-bit pairing codes allow each collector to fetch its own token.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import sys
import time

def _wallet():
    # The normal controller remains stdlib-only until an administrator pairs a device.
    # Missing optional cryptography must not stop its health/admin/collector routes.
    import key_wallet
    return key_wallet

NAME = re.compile(r"^[A-Za-z0-9가-힣][A-Za-z0-9가-힣_.-]{0,59}$")
TYPES = {"control", "supply", "manual", "remote", "accessibility"}
AAD = b"BIDGUARD-COLLECTOR-v2:"
CODE_AAD = b"BIDGUARD-ONE-TIME-PAIRING-v2:"
CLAIM_SCHEMA = """
CREATE TABLE IF NOT EXISTS collector_key_claims (
  name TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL,
  encrypted_token TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  used_at TEXT,
  failures INTEGER NOT NULL DEFAULT 0
);
"""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def code_hash(code: str) -> str:
    return hashlib.sha256(b"BIDGUARD-CLAIM-V2:" + code.encode("ascii")).hexdigest()


def check_name(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("invalid collector name (letters (including Hangul), digits, ._-; 1-60)")
    return name


def check_type(value):
    if value not in TYPES:
        raise ValueError("unsupported collector type")
    return value


def init_schema(con):
    con.executescript(CLAIM_SCHEMA)


def read_manifest(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, list) or not 1 <= len(value) <= 200:
        raise ValueError("manifest must be a 1..200 item JSON array")
    names = set()
    rows = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("manifest entry must be an object")
        name = check_name(item.get("name"))
        ctype = check_type(item.get("type", "control"))
        if name in names:
            raise ValueError("duplicate collector name")
        names.add(name)
        rows.append((name, ctype))
    return rows


def _private_file(path, payload):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("symlink not allowed")
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def provision_batch(wallet_path, database_path, rows, out_dir, password, rotate=False, seconds=86400):
    """New enrollment: no secret to GitHub, admin API, logs, or URLs.

    Existing working tokens are never overwritten unless rotate=True.
    A private pairing package is created for each newly provisioned collector.
    """
    wallet = _wallet()
    if not rows or len(rows) > 200 or not 300 <= seconds <= 604800:
        raise ValueError("invalid batch or pairing lifetime")
    seen = set()
    for name, ctype in rows:
        check_name(name)
        check_type(ctype)
        if name in seen:
            raise ValueError("duplicate name")
        seen.add(name)
    out_dir = Path(out_dir).expanduser()
    if out_dir.is_symlink():
        raise ValueError("handoff directory symlink rejected")
    out_dir = out_dir.absolute()
    out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix" and (out_dir.stat().st_mode & 0o077):
        raise PermissionError("handoff directory must be private (0700)")
    source = wallet.read_private(wallet_path, wallet.WALLET_FORMAT)
    master = wallet.unlock(source, password)
    created = []
    results = []
    try:
        con = sqlite3.connect(str(database_path), timeout=20, isolation_level=None)
        try:
            con.row_factory = sqlite3.Row
            init_schema(con)
            con.execute("BEGIN IMMEDIATE")
            records = dict(source.get("collector_keys", {}))
            for name, ctype in rows:
                current = con.execute("SELECT token_hash FROM collectors WHERE name=?", (name,)).fetchone()
                if current and current["token_hash"] and not rotate:
                    results.append({"name": name, "result": "kept-existing"})
                    continue
                token = secrets.token_urlsafe(32)
                code = secrets.token_urlsafe(32)
                sealed = wallet.seal(master, token.encode("ascii"), AAD + name.encode("ascii"))
                records[name] = {"sealed": sealed, "issued_at": wallet.stamp(), "type": ctype}
                # The claim code can unwrap only this one device token. It is single-use.
                key = hashlib.sha256(CODE_AAD + code.encode("ascii")).digest()
                claim = wallet.seal(key, token.encode("ascii"), CODE_AAD + name.encode("ascii"))
                package = {"format": "bidguard.collector-pair.v2", "name": name,
                           "type": ctype, "claim_code": code, "expires_at": int(time.time()) + seconds}
                dest = out_dir / (name + ".pair.json")
                _private_file(dest, package)
                created.append(dest)
                con.execute("INSERT INTO collectors(name,type,status,token_hash,first_seen,last_seen) "
                            "VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
                            "type=excluded.type,status='approved',token_hash=excluded.token_hash",
                            (name, ctype, "approved", token_hash(token), wallet.stamp(), wallet.stamp()))
                con.execute("INSERT INTO collector_key_claims(name,code_hash,encrypted_token,expires_at,used_at,failures) "
                            "VALUES(?,?,?,?,NULL,0) ON CONFLICT(name) DO UPDATE SET "
                            "code_hash=excluded.code_hash,encrypted_token=excluded.encrypted_token,"
                            "expires_at=excluded.expires_at,used_at=NULL,failures=0",
                            (name, code_hash(code), json.dumps(claim), package["expires_at"]))
                results.append({"name": name, "result": "issued", "package": str(dest)})
            if created:
                source["collector_keys"] = records
                # On failure: DB transaction rolls back; encrypted wallet may be ahead.
                wallet.write_private(wallet_path, source)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK") if con.in_transaction else None
            for p in created:
                p.unlink(missing_ok=True)
            raise
        finally:
            con.close()
        return results
    finally:
        wallet.erase(master)


def redeem(con, name, claim_code, clock=None):
    """Return (token, reason) exactly once, only to the paired device."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        return None, "denied"
    if not isinstance(claim_code, str) or len(claim_code) > 100 or len(claim_code) < 40:
        return None, "denied"
    at = int(time.time() if clock is None else clock)
    con.execute("BEGIN IMMEDIATE")
    try:
        r = con.execute("SELECT c.status,c.token_hash,k.* FROM collectors c "
                        "JOIN collector_key_claims k ON c.name=k.name WHERE c.name=?",
                        (name,)).fetchone()
        if r is None or r["status"] != "approved" or r["used_at"] or r["expires_at"] < at or r["failures"] >= 5:
            con.execute("COMMIT")
            return None, "denied"
        if not hmac.compare_digest(r["code_hash"], code_hash(claim_code)):
            con.execute("UPDATE collector_key_claims SET failures=failures+1 WHERE name=?", (name,))
            con.execute("COMMIT")
            return None, "denied"
        wallet = _wallet()
        key = hashlib.sha256(CODE_AAD + claim_code.encode("ascii")).digest()
        try:
            token = wallet.open_seal(key, json.loads(r["encrypted_token"]), CODE_AAD + name.encode("ascii")).decode("ascii")
        except (ValueError, KeyError, TypeError):
            con.execute("COMMIT")
            return None, "denied"
        if not hmac.compare_digest(r["token_hash"], token_hash(token)):
            con.execute("COMMIT")
            return None, "denied"
        con.execute("UPDATE collector_key_claims SET used_at=? WHERE name=?", (wallet.stamp(), name))
        con.execute("COMMIT")
        return token, "issued"
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise


def revoke(database_path, name):
    check_name(name)
    with sqlite3.connect(str(database_path)) as con:
        init_schema(con)
        con.execute("UPDATE collectors SET status='blocked' WHERE name=?", (name,))
        con.execute("UPDATE collector_key_claims SET used_at='revoked' WHERE name=?", (name,))


def status(database_path):
    with sqlite3.connect(str(database_path)) as con:
        con.row_factory = sqlite3.Row
        init_schema(con)
        return [dict(r) for r in con.execute(
            "SELECT c.name,c.type,c.status,"
            "CASE WHEN c.token_hash<>'' THEN 1 ELSE 0 END has_key,"
            "k.expires_at,k.used_at FROM collectors c "
            "LEFT JOIN collector_key_claims k ON k.name=c.name ORDER BY c.name")]


def main(argv=None):
    p = argparse.ArgumentParser(description="BIDGUARD collector key wallet T-v0.2")
    p.add_argument("--wallet", default=os.environ.get("BIDGUARD_WALLET", "controller_data/key-wallet.json"))
    p.add_argument("--db", default=os.environ.get("CONTROLLER_DB", "controller_data/controller.db"))
    sub = p.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("provision")
    issue.add_argument("--manifest", required=True)
    issue.add_argument("--out-dir", required=True)
    issue.add_argument("--rotate", action="store_true")
    issue.add_argument("--hours", type=int, default=24)
    block = sub.add_parser("revoke")
    block.add_argument("--name", required=True)
    sub.add_parser("status")
    a = p.parse_args(argv)
    try:
        if a.command == "status":
            for item in status(a.db):
                print(item)
            return 0
        wallet = _wallet()
        wallet.require_tty()
        pw = getpass.getpass("관제기 키 지갑 관리자 암호: ")
        # For revoke also authenticate with the encrypted master wallet.
        master = wallet.unlock(wallet.read_private(a.wallet, wallet.WALLET_FORMAT), pw)
        wallet.erase(master)
        if a.command == "revoke":
            revoke(a.db, a.name)
            print("수집기 접근 차단:", a.name)
        else:
            if a.hours < 1 or a.hours > 168:
                raise ValueError("pairing valid 1..168 hours")
            for result in provision_batch(a.wallet, a.db, read_manifest(a.manifest),
                                          a.out_dir, pw, rotate=a.rotate,
                                          seconds=a.hours * 3600):
                print(result["name"], result["result"], result.get("package", ""))
        return 0
    except (ValueError, PermissionError, OSError, sqlite3.Error) as exc:
        print("ERROR:", type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
