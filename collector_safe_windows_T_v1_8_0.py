"""Windows-only supervised input lock and dedicated Chrome kiosk (#50 / T-v1.8.0).

No attempt is made to disable arbitrary HID devices by vendor ID.
A field-proven, independently started watchdog and a verified RDP /unlock
channel are REQUIRED before physical input disable can be armed.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from collector_launch_options_T_v1_8_0 import KioskPolicy, lock_preflight

DEVICE_ID = re.compile(r"^[A-Za-z0-9_\\&+.#{} -]{8,220}$")
WATCHDOG_MAX_AGE = 15 * 60
PICO_VID = "VID_2E8A"


def _windows_admin():
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


class PnPDeviceControl:
    """Only exact, approved PnP instance IDs. No wildcard or class-wide disable."""
    def __init__(self, run=None):
        self.run = run or subprocess.run

    def _action(self, action, device):
        if action not in ("disable", "enable"):
            raise ValueError("invalid PnP operation")
        if not DEVICE_ID.fullmatch(str(device)) or PICO_VID in str(device).upper():
            raise ValueError("invalid or Pico device")
        return self.run(
            ["pnputil.exe", "/" + action + "-device", device],
            check=True, capture_output=True, timeout=20,
        )

    def disable(self, device):
        return self._action("disable", device)

    def enable(self, device):
        return self._action("enable", device)


def write_journal(path, device_ids, *, expiry):
    """Write recovery information BEFORE touching devices; contains no secrets."""
    path = Path(path)
    if path.exists():
        raise RuntimeError("unresolved previous input lock journal")
    path.parent.mkdir(parents=True, exist_ok=True)
    info = {"devices": list(device_ids), "expiry": int(expiry),
            "version": "T-v1.8.0"}
    temp = path.with_suffix(".pending")
    with open(temp, "x", encoding="utf-8") as out:
        json.dump(info, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    return path


def recover(path, backend=None):
    """Idempotent remote /unlock or watchdog recovery (admin required in production)."""
    path = Path(path)
    if not path.exists():
        return {"ok": True, "already_unlocked": True}
    raw = json.loads(path.read_text(encoding="utf-8"))
    devices = raw.get("devices")
    if not isinstance(devices, list) or not devices:
        raise RuntimeError("invalid recovery journal")
    backend = backend or PnPDeviceControl()
    errors = []
    # Re-enable every device even if one recovery fails.
    for device in devices:
        try:
            backend.enable(device)
        except Exception:
            errors.append(device)
    if errors:
        return {"ok": False, "retry_required": len(errors)}
    path.unlink()
    return {"ok": True, "already_unlocked": False}


@contextlib.contextmanager
def supervised_input_lock(*, physical_device_ids, pico_device_ids,
                          journal_path, watchdog_armed, unlock_channel_tested,
                          remote_session_tested, backend=None, clock=time.time,
                          require_admin=True):
    """Disable only approved exact instances; always attempt restoration.

    Watchdog is a separate OS process/task, not this context manager. A field
    operator must prove that the boot/startup watchdog remains active even
    when this collector crashes. The journal is written before any disable.
    """
    ready, why = lock_preflight(
        pico_device_ids=pico_device_ids, physical_device_ids=physical_device_ids,
        watchdog_armed=watchdog_armed,
        unlock_channel_tested=unlock_channel_tested,
        remote_session_tested=remote_session_tested,
    )
    if not ready:
        raise RuntimeError(why)
    if require_admin and not _windows_admin():
        raise RuntimeError("ADMIN_REQUIRED")
    backend = backend or PnPDeviceControl()
    journal = write_journal(
        journal_path, physical_device_ids,
        expiry=int(clock() + WATCHDOG_MAX_AGE),
    )
    try:
        for device in physical_device_ids:
            backend.disable(device)
        yield
    finally:
        receipt = recover(journal, backend)
        if not receipt["ok"]:
            raise RuntimeError("INPUT_RECOVERY_REQUIRED")


def watchdog_check(journal_path, backend=None, clock=time.time):
    """External watchdog should poll this function and work when collector is dead."""
    path = Path(journal_path)
    if not path.is_file():
        return {"ok": True, "armed": False}
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
        expiry = int(entry["expiry"])
    except (ValueError, KeyError, OSError, TypeError):
        return {"ok": False, "reason": "invalid journal; operator intervention"}
    if int(clock()) >= expiry:
        return recover(journal_path, backend)
    return {"ok": True, "armed": True, "expiry": expiry}


def chrome_debug_port(profile):
    """Chrome --remote-debugging-port=0 creates DevToolsActivePort in private profile."""
    marker = Path(profile) / "DevToolsActivePort"
    if not marker.is_file():
        return None
    try:
        port = int(marker.read_text(encoding="ascii").splitlines()[0])
        return port if 1024 <= port <= 65535 else None
    except (ValueError, OSError, IndexError):
        return None


class DedicatedChromeKiosk:
    """Own-profile Chrome only. Never kills unrelated user Chrome/RDP windows."""
    def __init__(self, chrome_exe, profile, allow_domains, *, report=None):
        self.chrome_exe = str(chrome_exe)
        self.profile = Path(profile).resolve()
        self.policy = KioskPolicy(allow_domains)
        self.report = report or (lambda *a, **k: None)
        self.proc = None

    def start(self, first_url):
        if not self.policy.allowed_domains:
            raise ValueError("site allowlist required")
        from collector_launch_options_T_v1_8_0 import allowed_site
        if not allowed_site(first_url, self.policy.allowed_domains):
            raise ValueError("starting URL not permitted")
        self.profile.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen([
            self.chrome_exe,
            "--user-data-dir=" + str(self.profile),
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "--no-first-run", "--no-default-browser-check",
            "--new-window", first_url,
        ])
        return self.proc

    def check_once(self):
        port = chrome_debug_port(self.profile)
        if port is None:
            return {"ok": False, "reason": "chrome debug channel unavailable"}
        endpoint = "http://127.0.0.1:" + str(port)
        try:
            with urllib.request.urlopen(endpoint + "/json/list", timeout=2) as resp:
                items = json.loads(resp.read(256 * 1024).decode("utf-8"))
            if not isinstance(items, list):
                return {"ok": False, "reason": "invalid chrome target list"}
            def close_tab(target):
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", target):
                    raise ValueError("unsafe browser target ID")
                with urllib.request.urlopen(endpoint + "/json/close/" + target, timeout=2):
                    pass
            closed = self.policy.enforce(items, close_tab, self.report)
            return {"ok": True, "closed": closed}
        except (OSError, ValueError, KeyError):
            return {"ok": False, "reason": "kiosk monitor disconnected"}

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
