"""pc_health 모듈·수집 PC 스크립트 오프라인 시험. 사이트·실제 PC 전원은 쓰지 않는다."""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import socket

HERE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(HERE))
import pc_health_G_v1_0_0 as health

SCRIPT = HERE / "collectors" / "standalone_collector_v1_6" / "report_pc_health_G_v1.0.0.py"


def load_script():
    spec = importlib.util.spec_from_file_location("report_pc_health_G_v1_0_0", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mem_db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    health.init(con)
    return con


KST = timezone(timedelta(hours=9))


class SanitizeAndWarnTests(unittest.TestCase):
    def test_sanitize_keeps_known_fields(self):
        clean = health.sanitize({
            "machine_name": "jijuclub5",
            "observed_at": "2026-11-22T13:23:00+09:00",
            "battery_percent": 96,
            "charging": True,
            "ac_plugged": True,
            "bot_ask_pending": False,
            "human_call_time": "2026-11-21T11:55:00+09:00",
            "collector_running": True,
            "collector_root": r"D:\BIDGUARD\standalone_collector_v1_6_v8",
            "schema": "bidguard.pc_health.v1",
        })
        self.assertEqual(clean["machine_name"], "jijuclub5")
        self.assertEqual(clean["battery_percent"], 96)
        self.assertTrue(clean["charging"])

    def test_rejects_secret_keys(self):
        with self.assertRaises(ValueError):
            health.sanitize({"machine_name": "pc1", "password": "x"})
        with self.assertRaises(ValueError):
            health.sanitize({"machine_name": "pc1", "api_token": "x"})

    def test_rejects_bad_name_and_percent(self):
        with self.assertRaises(ValueError):
            health.sanitize({"machine_name": "../etc"})
        with self.assertRaises(ValueError):
            health.sanitize({"machine_name": "pc1", "battery_percent": 140})

    def test_warnings_offline_charger_battery_human(self):
        now = datetime(2026, 11, 22, 4, 23, tzinfo=timezone.utc)  # 13:23 KST
        row = {
            "received_at": "2026-11-22T04:23:00+00:00",  # same instant, online
            "battery_percent": 96,
            "charging": True,
            "ac_plugged": True,
            "human_call_time": "2026-11-21T11:55:00+09:00",  # yesterday, >24h
        }
        self.assertEqual(health.warnings_for(row, now=now, offline_after_min=30), [])
        row["received_at"] = "2026-11-22T03:50:00+00:00"  # 33 min ago
        self.assertIn("offline", health.warnings_for(row, now=now, offline_after_min=30))
        row["received_at"] = "2026-11-22T04:23:00+00:00"
        row["charging"] = False
        row["ac_plugged"] = False
        self.assertIn("charger_unplugged", health.warnings_for(row, now=now))
        row["charging"] = False
        row["ac_plugged"] = True  # 완충·AC 연결은 충전기 빠짐이 아님
        self.assertNotIn("charger_unplugged", health.warnings_for(row, now=now))
        row["battery_percent"] = 19
        self.assertIn("battery_low", health.warnings_for(row, now=now))
        row["human_call_time"] = "2026-11-22T12:00:00+09:00"
        self.assertIn("human_call", health.warnings_for(row, now=now))


class StoreTests(unittest.TestCase):
    def test_store_and_snapshot(self):
        con = mem_db()
        health.store(con, {
            "machine_name": "jijuclub5",
            "observed_at": "2026-11-22T13:23:00+09:00",
            "battery_percent": 96,
            "charging": True,
            "ac_plugged": True,
            "bot_ask_pending": False,
            "human_call_time": "2026-11-21T11:55:00+09:00",
            "collector_running": True,
        }, received_at="2026-11-22T04:23:00+00:00")
        snap = health.admin_snapshot(con, offline_after_min=30)
        self.assertEqual(snap["offline_after_min"], 30)
        self.assertEqual(len(snap["machines"]), 1)
        m = snap["machines"][0]
        self.assertEqual(m["machine_name"], "jijuclub5")
        self.assertEqual(m["battery_percent"], 96)
        # received_at 이 고정된 과거면 실행 시점에 따라 offline 일 수 있어, 최신으로 다시 저장
        health.store(con, {
            "machine_name": "jijuclub5",
            "battery_percent": 96,
            "charging": True,
            "ac_plugged": True,
            "bot_ask_pending": False,
            "collector_running": True,
        })
        m = health.admin_snapshot(con, offline_after_min=30)["machines"][0]
        self.assertTrue(m["online"])
        self.assertNotIn("offline", m["warnings"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.con = mem_db()
        self._orig = health._handler_db
        health._handler_db = lambda _h=None: _Ctx(self.con)
        self._token = os.environ.pop("CONTROLLER_PC_HEALTH_TOKEN", None)

    def tearDown(self):
        health._handler_db = self._orig
        if self._token is None:
            os.environ.pop("CONTROLLER_PC_HEALTH_TOKEN", None)
        else:
            os.environ["CONTROLLER_PC_HEALTH_TOKEN"] = self._token

    def test_post_and_admin_get(self):
        h = FakeHandler(body={
            "machine_name": "jijuclub5",
            "battery_percent": 50,
            "charging": True,
            "bot_ask_pending": True,
            "collector_running": False,
        })
        self.assertTrue(health.route(h, "POST", "/api/collector/pc_health", {}))
        self.assertEqual(h.code, 200)
        self.assertTrue(h.payload["ok"])
        h2 = FakeHandler(admin=True)
        self.assertTrue(health.route(h2, "GET", "/api/admin/pc_health", {}))
        self.assertEqual(h2.code, 200)
        self.assertEqual(h2.payload["machines"][0]["bot_ask_pending"], True)

    def test_admin_requires_token(self):
        h = FakeHandler(admin=False)
        health.route(h, "GET", "/api/admin/pc_health", {})
        self.assertEqual(h.code, 403)

    def test_optional_post_token(self):
        os.environ["CONTROLLER_PC_HEALTH_TOKEN"] = "s3cret-test"
        h = FakeHandler(body={"machine_name": "pc1"})
        health.route(h, "POST", "/api/collector/pc_health", {})
        self.assertEqual(h.code, 403)
        h2 = FakeHandler(body={"machine_name": "pc1"}, headers={"X-PC-Health-Token": "s3cret-test"})
        health.route(h2, "POST", "/api/collector/pc_health", {})
        self.assertEqual(h2.code, 200)
        # 응답에 토큰이 없어야 한다
        self.assertNotIn("s3cret-test", json.dumps(h2.payload))


class _Ctx:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self.con

    def __exit__(self, *a):
        return False


class FakeHandler:
    def __init__(self, body=None, admin=False, headers=None):
        self._body_data = body or {}
        self._admin_flag = admin
        self.headers = headers or {}
        self.code = None
        self.payload = None

    def _body(self):
        return self._body_data

    def _admin(self):
        return self._admin_flag

    def _json(self, code, obj):
        self.code, self.payload = code, obj


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_script()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "state" / "bot_ask").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_and_write_without_post(self):
        (self.root / "state" / "bot_ask" / "pending.json").write_text("{}", encoding="utf-8")
        (self.root / "state" / "human_call_latest.json").write_text(
            json.dumps({"time": "2026-11-21T11:55:00+09:00"}), encoding="utf-8")
        payload = self.mod.build_payload(
            self.root,
            battery_reader=lambda: {"battery_percent": 96, "charging": True, "ac_plugged": True},
            process_checker=lambda _r: True,
            machine_name="jijuclub5",
        )
        self.assertTrue(payload["bot_ask_pending"])
        self.assertEqual(payload["human_call_time"], "2026-11-21T11:55:00+09:00")
        self.assertTrue(payload["collector_running"])
        self.assertEqual(payload["battery_percent"], 96)
        out = self.root / "state" / "pc_health.json"
        self.mod.write_status(out, payload)
        saved = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(saved["machine_name"], "jijuclub5")
        self.assertNotIn("password", saved)
        self.assertNotIn("token", saved)

    def test_post_failure_keeps_file(self):
        payload = self.mod.build_payload(
            self.root,
            battery_reader=lambda: {"battery_percent": 10, "charging": False, "ac_plugged": False},
            process_checker=lambda _r: False,
            machine_name="jijuclub5",
        )
        out = self.root / "state" / "pc_health.json"
        self.mod.write_status(out, payload)
        result = self.mod.post_status("http://127.0.0.1:1", payload, None)
        self.assertEqual(result, "failed")
        self.assertTrue(out.is_file())

    def test_post_success(self):
        got = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                got["path"] = self.path
                n = int(self.headers.get("Content-Length", "0"))
                got["body"] = json.loads(self.rfile.read(n))
                got["token"] = self.headers.get("X-PC-Health-Token")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                return

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        srv = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            payload = {"schema": "bidguard.pc_health.v1", "machine_name": "jijuclub5"}
            self.assertEqual(self.mod.post_status("http://127.0.0.1:%d" % port, payload, "tok"), "ok")
            self.assertEqual(got["path"], "/api/collector/pc_health")
            self.assertEqual(got["body"]["machine_name"], "jijuclub5")
            self.assertEqual(got["token"], "tok")
        finally:
            srv.shutdown()


if __name__ == "__main__":
    unittest.main()
