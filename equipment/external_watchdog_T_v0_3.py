"""Out-of-process watchdog for the controller itself, T-v0.3.

Install on an independent always-on PC, not on the monitored Ubuntu controller.
It does not power on/reboot machines or perform remote commands.
"""
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
from urllib.parse import urlparse


def main():
    health = os.environ["CONTROLLER_HEALTH_URL"]
    notify = os.environ["NOTIFY_URL"]
    state_path = Path(os.environ.get("WATCHDOG_STATE_FILE", "watchdog_state.json"))
    if urlparse(health).scheme != "https" or urlparse(notify).scheme != "https":
        raise ValueError("Both health and notification endpoints require HTTPS")
    interval = min(120, max(20, int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "30"))))
    try:
        saved = json.loads(state_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        saved = {"state": "UNKNOWN", "failures": 0, "last_notice": 0}
    while True:
        try:
            with urlopen(health, timeout=5) as response:
                healthy = response.status == 200
        except Exception:
            healthy = False
        if healthy:
            saved["failures"] = 0
            next_state = "UP"
        else:
            saved["failures"] = int(saved.get("failures", 0)) + 1
            next_state = "DOWN" if saved["failures"] >= 2 else saved["state"]
        prev = saved.get("state", "UNKNOWN")
        is_change = next_state != prev
        should_notify = (next_state in ("DOWN", "UP")
                         and (is_change and prev != "UNKNOWN"
                              or (next_state == "DOWN" and time.time()-float(saved.get("last_notice", 0)) > 300)))
        if should_notify:
            body = json.dumps({"type":"controller_watchdog",
                               "node_id":"BG-PC-001", "state":next_state,
                               "severity":"critical" if next_state=="DOWN" else "recovered",
                               "detail":"관제기 HTTPS 상태 검사 실패" if next_state=="DOWN" else "관제기 HTTPS 응답 복구"},
                              ensure_ascii=False).encode()
            try:
                req = Request(notify, data=body, method="POST",
                              headers={"Content-Type":"application/json"})
                with urlopen(req, timeout=8) as response:
                    if 200 <= response.status < 300:
                        saved["last_notice"] = time.time()
            except Exception:
                pass  # retry while alert remains active
        saved["state"] = next_state
        temp = state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(saved), encoding="utf-8")
        temp.replace(state_path)
        time.sleep(interval)


if __name__ == "__main__":
    main()
