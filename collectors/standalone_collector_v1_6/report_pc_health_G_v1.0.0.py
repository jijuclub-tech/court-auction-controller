"""수집 PC의 전원·호출 상태를 state\\pc_health.json 으로 쓰고, 설정된 관제기에만 POST 한다.

키·비밀번호·ID 파일을 읽거나 출력하지 않는다. 관제기 전송이 실패해도 파일은 남긴다(독립 동작).
기본 수집기 경로: D:\\BIDGUARD\\standalone_collector_v1_6_v8
(없으면 C:\\BIDGUARD\\standalone_collector_v1_6_v8).
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCHEMA_ID = "bidguard.pc_health.v1"
KST = timezone(timedelta(hours=9), "KST")
DEFAULT_ROOTS = (
    r"D:\BIDGUARD\standalone_collector_v1_6_v8",
    r"C:\BIDGUARD\standalone_collector_v1_6_v8",
)
CHARGING_STATUS = {2, 6, 7, 8, 9}  # 지시: BatteryStatus==2 를 충전으로 본다. 6~9 는 CIM 충전값.
PROCESS_HINTS = ("-m standalone", "standalone run", "standalone_collector")


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def resolve_root(given: str | None) -> Path:
    if given:
        return Path(given)
    env = os.environ.get("COLLECTOR_ROOT", "").strip()
    if env:
        return Path(env)
    for cand in DEFAULT_ROOTS:
        p = Path(cand)
        if p.is_dir():
            return p
    return Path(DEFAULT_ROOTS[0])


def _ps(command: str, timeout: int = 15) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", command],
        capture_output=True, text=True, timeout=timeout, check=False,
    )
    return (r.stdout or "").strip()


def read_win32_battery() -> dict:
    """Win32_Battery EstimatedChargeRemaining, BatteryStatus==2 이면 충전.

    CIM 충전값(6~9)과 kernel32 ACLineStatus 도 함께 본다. 데스크톱처럼 배터리가
    없으면 percent/charging 은 null 이고, AC 연결만 확인할 수 있다.
    """
    out = {"battery_percent": None, "charging": None, "ac_plugged": None}
    script = (
        "$b = Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue; "
        "if (-not $b) { Write-Output '[]' } else { "
        "$b | Select-Object EstimatedChargeRemaining,BatteryStatus | ConvertTo-Json -Compress }"
    )
    try:
        raw = _ps(script)
        data = json.loads(raw) if raw else []
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        data = []
    if isinstance(data, dict):
        data = [data]
    if data:
        first = data[0] or {}
        try:
            pct = first.get("EstimatedChargeRemaining")
            out["battery_percent"] = int(pct) if pct is not None else None
        except (TypeError, ValueError):
            out["battery_percent"] = None
        try:
            status = int(first.get("BatteryStatus")) if first.get("BatteryStatus") is not None else None
        except (TypeError, ValueError):
            status = None
        if status is not None:
            out["charging"] = status in CHARGING_STATUS
            if status in CHARGING_STATUS or status == 3:
                out["ac_plugged"] = True
            elif status == 1:
                out["ac_plugged"] = False
    ac = _ac_line_status()
    if ac is not None:
        out["ac_plugged"] = ac
    return out


def _ac_line_status():
    """GetSystemPowerStatus ACLineStatus: 0 끊김, 1 연결. 실패하면 None."""
    try:
        import ctypes
        class _SPS(ctypes.Structure):
            _fields_ = [
                ("ACLineStatus", ctypes.c_byte),
                ("BatteryFlag", ctypes.c_byte),
                ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte),
                ("BatteryLifeTime", ctypes.c_uint32),
                ("BatteryFullLifeTime", ctypes.c_uint32),
            ]
        if not hasattr(ctypes, "windll"):
            return None
        s = _SPS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
            return None
        if s.ACLineStatus == 1:
            return True
        if s.ACLineStatus == 0:
            return False
    except (OSError, AttributeError, ValueError):
        return None
    return None


def read_human_call_time(root: Path) -> str | None:
    path = root / "state" / "human_call_latest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("time", "at", "observed_at", "ts", "timestamp"):
        val = data.get(key)
        if val not in (None, ""):
            return str(val)[:40]
    return None


def bot_ask_pending(root: Path) -> bool:
    return (root / "state" / "bot_ask" / "pending.json").is_file()


def collector_running(root: Path, checker=None) -> bool:
    if checker is not None:
        return bool(checker(root))
    root_s = str(root).replace("/", "\\").lower()
    if os.name == "nt":
        # 명령줄 원문은 저장·출력하지 않고, 힌트 일치 여부만 본다.
        hints = "', '".join(PROCESS_HINTS)
        script = (
            "$root = '%s'; $hints = @('%s'); "
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.Name -match 'python(w)?\\.exe$' -or $_.Name -eq 'standalone.exe' } | "
            "ForEach-Object { $c = [string]$_.CommandLine; $n = [string]$_.ExecutablePath; "
            "foreach ($h in $hints) { if ($c -like ('*'+$h+'*')) { '1'; break } } "
            "if ($n -and $n.ToLower().StartsWith($root)) { '1' } }; "
            % (root_s.replace("'", "''"), hints)
        )
        try:
            return "1" in _ps(script, timeout=15).split()
        except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
            return False
    # 리눅스 시험용: /proc 명령줄을 출력하지 않고 힌트만 확인
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                cmd = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            except OSError:
                continue
            low = cmd.lower()
            if any(h.lower() in low for h in PROCESS_HINTS) or root_s.lower() in low.replace("/", "\\"):
                return True
    except OSError:
        return False
    return False


def build_payload(root: Path, *, battery_reader=None, process_checker=None,
                  machine_name: str | None = None) -> dict:
    bat = (battery_reader or read_win32_battery)()
    return {
        "schema": SCHEMA_ID,
        "observed_at": now_kst(),
        "machine_name": (machine_name or os.environ.get("COMPUTERNAME") or socket.gethostname() or "unknown")[:80],
        "battery_percent": bat.get("battery_percent"),
        "charging": bat.get("charging"),
        "ac_plugged": bat.get("ac_plugged"),
        "bot_ask_pending": bot_ask_pending(root),
        "human_call_time": read_human_call_time(root),
        "collector_running": collector_running(root, checker=process_checker),
        "collector_root": str(root),
    }


def write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="pc_health.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def post_status(url: str, payload: dict, token: str | None) -> str:
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/pc_health"):
        endpoint = endpoint + "/api/collector/pc_health"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-PC-Health-Token"] = token
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urlopen(req, timeout=10) as resp:
            return "ok" if 200 <= resp.status < 300 else "failed"
    except HTTPError:
        return "failed"
    except (URLError, TimeoutError, OSError, ValueError):
        return "failed"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="수집 PC 상태를 파일로 남기고 관제기에 보낸다.")
    ap.add_argument("--collector-root", default=None, help="수집기 루트 경로")
    ap.add_argument("--controller-url", default=os.environ.get("CONTROLLER_URL", ""),
                    help="관제기 주소 (예: http://관제기:8770). 없으면 파일만 기록")
    ap.add_argument("--token", default=None,
                    help="관제기 CONTROLLER_PC_HEALTH_TOKEN. 비우면 환경변수 사용. 출력하지 않음")
    ap.add_argument("--output", default=None, help="상태 파일 경로 (기본: <루트>\\state\\pc_health.json)")
    args = ap.parse_args(argv)
    root = resolve_root(args.collector_root)
    out_path = Path(args.output) if args.output else root / "state" / "pc_health.json"
    payload = build_payload(root)
    write_status(out_path, payload)
    token = args.token if args.token is not None else os.environ.get("CONTROLLER_PC_HEALTH_TOKEN", "")
    posted = "skipped"
    if args.controller_url:
        posted = post_status(args.controller_url, payload, token or None)
    # 토큰·경로 비밀·파일 본문은 출력하지 않는다.
    print("pc_health written; post=%s" % posted)
    return 0 if posted in ("ok", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
