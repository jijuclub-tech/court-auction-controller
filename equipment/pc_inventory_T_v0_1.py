"""Controller-side equipment registry and status history (not yet deployed).

Only authenticated and pre-registered devices may pass data to heartbeat().
The HTTP authentication layer and operating-system agents are separate work.
This module never reads user files, screen contents, or input keystrokes.
"""
from __future__ import annotations
import json
import re
import sqlite3
import time

_ASSET = re.compile(r"^[A-Z][A-Z0-9_-]{3,55}$")
_FIELDS = {
    "cpu_percent", "ram_percent", "disk_free_bytes", "battery_percent",
    "job_state", "service_state", "nas_state", "drive_state",
    "local_pending_count", "local_pending_bytes", "collector_id",
    "os", "app_version"
}


class EquipmentRegistry:
    def __init__(self, path):
        self.path = str(path)
        con = sqlite3.connect(self.path)
        try:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS equipment_assets_T (
                    asset_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL, role TEXT, model TEXT,
                    verification TEXT NOT NULL DEFAULT 'UNVERIFIED',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS equipment_heartbeats_T (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    asset_id TEXT NOT NULL, received_at INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(asset_id) REFERENCES equipment_assets_T(asset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_equipment_recent_T
                    ON equipment_heartbeats_T(asset_id, received_at DESC);
            """)
            con.commit()
        finally:
            con.close()

    def register(self, asset_id, label, role="", model=""):
        if not _ASSET.fullmatch(asset_id):
            raise ValueError("Invalid asset ID")
        with sqlite3.connect(self.path) as db:
            db.execute(
                "INSERT INTO equipment_assets_T"
                "(asset_id,label,role,model,created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(asset_id) DO UPDATE SET "
                "label=excluded.label,role=excluded.role,model=excluded.model",
                (asset_id, str(label)[:120], str(role)[:120],
                 str(model)[:120], int(time.time()))
            )

    def heartbeat(self, asset_id, data):
        """Call only after controller authenticates the submitting device."""
        if not _ASSET.fullmatch(asset_id) or not isinstance(data, dict):
            raise ValueError("Invalid heartbeat")
        clean = {
            k: v for k, v in data.items()
            if k in _FIELDS
            and isinstance(v, (str, int, float, bool, type(None)))
            and len(str(v)) <= 160
        }
        if len(json.dumps(clean)) > 2048:
            raise ValueError("Payload too large")
        with sqlite3.connect(self.path) as db:
            if not db.execute(
                "SELECT 1 FROM equipment_assets_T WHERE asset_id=?",
                (asset_id,)
            ).fetchone():
                raise PermissionError("Unregistered device")
            db.execute(
                "INSERT INTO equipment_heartbeats_T"
                "(asset_id,received_at,payload) VALUES(?,?,?)",
                (asset_id, int(time.time()),
                 json.dumps(clean, ensure_ascii=False))
            )

    def list_status(self, stale_seconds=300):
        with sqlite3.connect(self.path) as db:
            rows = db.execute(
                """SELECT a.asset_id,a.label,a.role,a.model,a.verification,
                          h.received_at,h.payload
                   FROM equipment_assets_T a LEFT JOIN equipment_heartbeats_T h
                   ON h.id=(SELECT id FROM equipment_heartbeats_T
                            WHERE asset_id=a.asset_id
                            ORDER BY received_at DESC,id DESC LIMIT 1)
                   ORDER BY a.asset_id"""
            ).fetchall()
        now = int(time.time())
        return [
            {
                "asset_id": a, "label": b, "role": c, "model": d,
                "verification": e, "last_heartbeat": stamp,
                "connection": (
                    "UNKNOWN" if stamp is None
                    else ("ONLINE" if now - stamp <= stale_seconds else "STALE")
                ),
                "health": json.loads(payload) if payload else {}
            }
            for a,b,c,d,e,stamp,payload in rows
        ]
