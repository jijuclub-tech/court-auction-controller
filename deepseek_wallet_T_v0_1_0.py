"""Local-only DeepSeek credential enrollment and controller-side connection check."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from key_wallet import (
    WALLET_FORMAT, erase, new_wallet, open_seal, prompt, read_private,
    require_tty, seal, unlock, write_private,
)

ROOT = Path(os.environ.get("BIDGUARD_DEEPSEEK_WALLET_DIR",
                           "/var/lib/court-auction-controller/deepseek"))
MODEL = "deepseek-flash"
WALLET = "wallet.json"
AAD = b"BIDGUARD:provider:deepseek:v1"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def enroll(root, password, token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,512}", token):
        raise ValueError("invalid API key")
    try:
        wallet = read_private(root / WALLET, WALLET_FORMAT)
    except FileNotFoundError:
        wallet, master = new_wallet(password)
        create = True
    else:
        master = unlock(wallet, password)
        create = False
    try:
        wallet["deepseek"] = {
            "revision": secrets.token_hex(16),
            "secret": seal(master, token.encode("ascii"), AAD),
        }
        write_private(root / WALLET, wallet, create=create)
    finally:
        erase(master)


def _probe(token):
    request = Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps({
            "model": MODEL, "messages": [{"role": "user", "content": "Reply OK."}],
            "max_tokens": 8, "thinking": {"type": "disabled"},
        }).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    with build_opener(NoRedirect()).open(request, timeout=10) as response:
        body = json.loads(response.read(65536))
    content = body["choices"][0]["message"]["content"]
    usage = body["usage"]
    counts = (usage["prompt_tokens"], usage["completion_tokens"])
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty completion")
    if any(type(count) is not int or count < 0 for count in counts):
        raise ValueError("invalid usage")
    return counts


def check(root, password, probe=_probe):
    wallet = read_private(root / WALLET, WALLET_FORMAT)
    master = unlock(wallet, password)
    try:
        entry = wallet["deepseek"]
        token = open_seal(master, entry["secret"], AAD).decode("ascii")
    finally:
        erase(master)
    try:
        incoming, outgoing = probe(token)
        status = "connected"
    except HTTPError as exc:
        status = {401: "key_rejected", 402: "insufficient_balance", 429: "rate_limited"}.get(
            exc.code, "provider_error")
        incoming = outgoing = 0
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        status = "provider_error"
        incoming = outgoing = 0

    timestamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    with closing(sqlite3.connect(root / "usage.sqlite3")) as con, con:
        con.execute("CREATE TABLE IF NOT EXISTS checks ("
                    "checked_at TEXT, revision TEXT, status TEXT, input_tokens INTEGER, output_tokens INTEGER)")
        con.execute("INSERT INTO checks VALUES (?, ?, ?, ?, ?)",
                    (timestamp, entry["revision"], status, incoming, outgoing))
    return status


def status(root=ROOT):
    try:
        wallet = read_private(root / WALLET, WALLET_FORMAT)
        revision = wallet.get("deepseek", {}).get("revision")
        if not revision:
            return {"connected": False, "state": "unregistered"}
        if not (root / "usage.sqlite3").exists():
            return {"connected": False, "state": "unverified"}
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        with closing(sqlite3.connect(root / "usage.sqlite3")) as con:
            last = con.execute("SELECT checked_at, status FROM checks WHERE revision=? "
                               "ORDER BY checked_at DESC LIMIT 1", (revision,)).fetchone()
            counts = con.execute(
                "SELECT COUNT(*), COUNT(CASE WHEN status='connected' THEN 1 END), "
                "COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) "
                "FROM checks WHERE substr(checked_at,1,7)=?", (month,)).fetchone()
        state = last[1] if last else "unverified"
        if state == "connected":
            elapsed = datetime.now(timezone.utc) - datetime.fromisoformat(last[0])
            if not 0 <= elapsed.total_seconds() <= 900:
                state = "stale"
        return {"connected": state == "connected", "state": state,
                "last_checked_at": last[0] if last else None, "month_utc": month,
                "check_calls": counts[0], "successful_checks": counts[1],
                "input_tokens": counts[2], "output_tokens": counts[3]}
    except FileNotFoundError:
        return {"connected": False, "state": "unregistered"}
    except (OSError, ValueError, sqlite3.Error):
        return {"connected": False, "state": "wallet_unavailable"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["enroll", "check", "status"])
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action != "status":
            require_tty()
            new = not (ROOT / WALLET).exists()
            password = prompt("키지갑 암호: ", confirm=new and args.action == "enroll", min_length=12)
            if args.action == "enroll":
                enroll(ROOT, password, prompt("DeepSeek API 키: ", confirm=True))
            else:
                check(ROOT, password)
        result = status()
        print(json.dumps(result, ensure_ascii=False))
        return int(args.action == "check" and not result["connected"])
    except (Exception, KeyboardInterrupt):
        print("키지갑 작업에 실패했습니다. 파일 권한, 입력값 또는 지갑 암호를 확인해 주세요.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
