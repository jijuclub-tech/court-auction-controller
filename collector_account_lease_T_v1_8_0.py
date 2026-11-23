"""Issue #50: collector-scoped NAS account session, runtime-only credential borrow.

The central key vault (#47) must provide the approved lease callback.
Server-side NAS ACLs must restrict each account to its own collector folder.
"""
from __future__ import annotations

import contextlib
import os
import re
from dataclasses import dataclass
from pathlib import PureWindowsPath

COLLECTOR = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


@dataclass
class SecretLease:
    username: str
    password: bytearray
    lease_id: str = ""

    def wipe(self):
        if isinstance(self.password, bytearray):
            self.password[:] = b"\0" * len(self.password)


def collector_unc(share_root, collector):
    """Drive letters are not credentials; resolve an explicit server UNC."""
    if not COLLECTOR.fullmatch(str(collector)):
        raise ValueError("invalid collector ID")
    root = str(share_root).rstrip("\\")
    if not root.startswith("\\\\") or "/" in root:
        raise ValueError("UNC root required")
    parts = [p for p in root.split("\\") if p]
    if len(parts) != 2 or any(p in (".", "..") for p in parts):
        raise ValueError("UNC must be an exact \\\\server\\share")
    return root + "\\" + collector


class WindowsNasConnector:
    """WNetAddConnection2W: no passwords on subprocess CLI or persistent drive mapping."""
    def connect(self, path, username, password):
        if os.name != "nt":
            raise RuntimeError("Windows NAS connection only")
        import ctypes
        from ctypes import wintypes
        class NETRESOURCEW(ctypes.Structure):
            _fields_ = [
                ("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD),
                ("dwDisplayType", wintypes.DWORD), ("dwUsage", wintypes.DWORD),
                ("lpLocalName", wintypes.LPWSTR), ("lpRemoteName", wintypes.LPWSTR),
                ("lpComment", wintypes.LPWSTR), ("lpProvider", wintypes.LPWSTR),
            ]
        resource = NETRESOURCEW(0, 1, 0, 0, None, path, None, None)
        # CONNECT_TEMPORARY(4); never CONNECT_UPDATE_PROFILE(1).
        rc = ctypes.windll.mpr.WNetAddConnection2W(
            ctypes.byref(resource), password, username, 4
        )
        if rc != 0:
            raise OSError("NAS connection rejected")
        return True

    def disconnect(self, path):
        if os.name != "nt":
            return
        import ctypes
        rc = ctypes.windll.mpr.WNetCancelConnection2W(path, 0, False)
        if rc != 0:
            raise OSError("NAS disconnect failed")


@contextlib.contextmanager
def scoped_nas_session(collector, share_root, borrow_approved_key, *,
                       connector=None, audit=None):
    """Use a #47 vault lease, never a saved password. Audit IDs only.

    borrow_approved_key(collector, "nas_write") -> SecretLease.
    """
    path = collector_unc(share_root, collector)
    connector = connector or WindowsNasConnector()
    audit = audit or (lambda *a, **kw: None)
    lease = borrow_approved_key(collector, "nas_write")
    if not isinstance(lease, SecretLease) or not isinstance(lease.password, bytearray):
        raise ValueError("approved runtime NAS credential lease required")
    connected = False
    try:
        password_text = lease.password.decode("utf-8")
        try:
            connector.connect(str(share_root).rstrip("\\"), lease.username, password_text)
            connected = True
        finally:
            del password_text
        audit("nas_lease_open", collector=collector, lease_id=lease.lease_id)
        yield path
    finally:
        try:
            if connected:
                connector.disconnect(str(share_root).rstrip("\\"))
        finally:
            lease.wipe()
            audit("nas_lease_closed", collector=collector, lease_id=lease.lease_id)
