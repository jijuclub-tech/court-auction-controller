"""Optional read-only equipment agent for registered Windows/Linux PCs (T-v0.3).

No keyboard capture, screenshots, file inventory, credentials in logs, or remote
command execution. Run with a service manager; only the controller is authoritative.
"""
from __future__ import annotations
import ctypes
import json
import os
import platform
from pathlib import Path
import shutil
import ssl
import sys
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def metrics():
    disk = shutil.disk_usage(os.environ.get("EQUIPMENT_DISK_PATH", Path.home()))
    d = {"os": platform.system(), "app_version": "equipment-agent-T-v0.3",
         "disk_free_bytes": disk.free,
         "service_state": "UNKNOWN",
         "job_state": "UNKNOWN",
         "nas_state": "UNKNOWN", "drive_state": "UNKNOWN"}
    if platform.system() == "Linux":
        try:
            data = Path("/proc/meminfo").read_text().splitlines()
            amounts = {line.split(":")[0]: int(line.split(":")[1].strip().split()[0])
                       for line in data if ":" in line}
            d["ram_percent"] = round((1-amounts["MemAvailable"]/amounts["MemTotal"])*100, 1)
        except (OSError, KeyError, ZeroDivisionError, ValueError):
            pass
        try:
            battery = next(Path("/sys/class/power_supply").glob("BAT*/capacity"))
            d["battery_percent"] = int(battery.read_text())
        except (OSError, StopIteration, ValueError):
            pass
    elif platform.system() == "Windows":
        class MEM(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("memoryLoad", ctypes.c_ulong),
                ("totalPhys", ctypes.c_ulonglong), ("availPhys", ctypes.c_ulonglong),
                ("totalPageFile", ctypes.c_ulonglong), ("availPageFile", ctypes.c_ulonglong),
                ("totalVirtual", ctypes.c_ulonglong), ("availVirtual", ctypes.c_ulonglong),
                ("availExtendedVirtual", ctypes.c_ulonglong)]
        m = MEM()
        m.length = ctypes.sizeof(m)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            d["ram_percent"] = m.memoryLoad
        class POWER(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_byte), ("batteryFlag", ctypes.c_byte),
                        ("batteryPct", ctypes.c_byte), ("systemStatus", ctypes.c_byte),
                        ("batteryLife", ctypes.c_ulong), ("batteryFullLife", ctypes.c_ulong)]
        p = POWER()
        if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(p)):
            if p.batteryPct != -1:
                d["battery_percent"] = max(0, int(p.batteryPct))
    health = os.environ.get("EQUIPMENT_LOCAL_HEALTH_URL", "").strip()
    if health:
        parsed = urlparse(health)
        if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            d["service_state"] = "UNKNOWN"
        else:
            try:
                with urlopen(health, timeout=3) as response:
                    d["service_state"] = "RUNNING" if response.status == 200 else "FAILED"
            except Exception:
                d["service_state"] = "FAILED"
    return d


def run():
    node = os.environ["EQUIPMENT_NODE_ID"]
    token = os.environ["EQUIPMENT_TOKEN"]
    endpoint = os.environ["EQUIPMENT_CONTROLLER_URL"].rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("EQUIPMENT_CONTROLLER_URL must be HTTPS")
    interval = max(30, min(300, int(os.environ.get("EQUIPMENT_HEARTBEAT_SECONDS", "60"))))
    while True:
        body = json.dumps({"node_id": node, "metrics": metrics()},
                          ensure_ascii=False).encode("utf-8")
        req = Request(endpoint + "/api/equipment/heartbeat", data=body, method="POST",
                      headers={"Content-Type": "application/json",
                               "X-Equipment-Token": token})
        try:
            with urlopen(req, timeout=8, context=ssl.create_default_context()) as result:
                if result.status != 200:
                    print("heartbeat failed", result.status, flush=True)
        except Exception as exc:
            print("heartbeat unavailable", type(exc).__name__, flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    run()
