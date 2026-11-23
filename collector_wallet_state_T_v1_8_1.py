"""Admin-only read-only status of encrypted controller wallet (never secret values).

Only shows existence/permissions/format and device enrollment count. The
offline wallet admin CLI still requires explicit owner approval to provision.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path


def status(data_root, con, env=None):
    env = os.environ if env is None else env
    file = Path(env.get("BIDGUARD_WALLET") or (Path(data_root) / "key-wallet.json"))
    exists = file.is_file() and not file.is_symlink()
    protected = False
    valid_format = False
    if exists:
        try:
            inf = file.stat()
            protected = stat.S_ISREG(inf.st_mode) and (
                os.name == "nt" or inf.st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
            )
            if protected and inf.st_size <= 512 * 1024:
                with file.open("rb") as reader:
                    info = json.load(reader)
                valid_format = bool(
                    isinstance(info, dict) and
                    info.get("format") == "bidguard.wallet.v1" and
                    isinstance(info.get("wrapped_master"), dict) and
                    isinstance(info.get("kdf"), dict)
                )
        except (OSError, ValueError, UnicodeError):
            valid_format = False
    try:
        registered = con.execute(
            "SELECT COUNT(*) FROM collectors WHERE status='approved' AND token_hash != ''"
        ).fetchone()[0]
    except sqlite3.Error:
        registered = None
    return {
        "wallet_module": "offline_admin_cli",
        "encrypted_wallet_file_present": exists,
        "wallet_private_permissions": protected,
        "wallet_format_recognized": valid_format,
        "wallet_active_confirmed": False,  # no unlock, real device test or approval
        "approved_collector_tokens_registered": registered,
        "nas_credential_leasing_active": False,  # issue #47 real lease API not connected
        "key_values_returned": False,
    }
