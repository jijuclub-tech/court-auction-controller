"""Offline tests: start the controller on a free port and talk to it over HTTP."""
import base64
import importlib
import json
import os
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTROLLER_DATA"] = cls.tmp.name
        os.environ["CONTROLLER_ADMIN_TOKEN"] = "adm"
        os.environ["CONTROLLER_BOT_TOKEN"] = "bot"
        os.environ.pop("CONTROLLER_AI", None)
        import controller
        cls.c = importlib.reload(controller)
        cls.c.init()
        port = free_port()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", port), cls.c.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.tmp.cleanup()

    def call(self, path, body=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        req = Request(self.base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers=h, method="POST" if body is not None else "GET")
        try:
            with urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def admin(self, path, body=None):
        return self.call(path, body, {"X-Admin-Token": "adm"})

    def register(self, name, ctype="control"):
        self.call("/api/collector/connect", {"name": name, "type": ctype})
        self.assertEqual(self.admin("/api/admin/collector", {"name": name, "status": "approved", "type": ctype})[0], 200)

    def test_01_health_and_admin_guard(self):
        self.assertEqual(self.call("/api/health")[1]["ok"], True)
        self.assertEqual(self.call("/api/admin/state")[0], 403)

    def test_02_connect_needs_approval_then_instruction_and_report(self):
        st, d = self.call("/api/collector/connect", {"name": "jiju01", "type": "control"})
        self.assertFalse(d["result"]["registered"])
        self.assertEqual(self.call("/api/collector/instruction/jiju01")[0], 403)
        self.register("jiju01")
        self.assertTrue(self.call("/api/collector/connect", {"name": "jiju01", "type": "control"})[1]["result"]["registered"])
        self.admin("/api/admin/instruction", {"name": "jiju01", "task_type": "감정평가서", "cases": ["2025타경1"]})
        inst = self.call("/api/collector/instruction/jiju01")[1]["instruction"]
        self.assertEqual(inst["cases"], ["2025타경1"])
        self.assertIsNone(self.call("/api/collector/instruction/jiju01")[1]["instruction"])
        st, d = self.call("/api/collector/report", {"name": "jiju01", "case_no": "2025타경1", "success": True,
                                                    "instruction_id": inst["instruction_id"]})
        self.assertEqual((st, d["ok"], d["result"]["task_status"]), (200, True, "done"))
        self.assertEqual(self.call("/api/collector/report", {"name": "jiju01"})[0], 400)

    def test_03_bundle_updates_and_supply_without_controller_rung(self):
        ladder = {"levels": {"3": [{"kind": "controller", "model": "control_center", "enabled": True},
                                   {"kind": "openai", "model": "gpt-4.1", "enabled": False}]}}
        st, d = self.admin("/api/admin/bundle", {"sites": [{"site_id": "s", "name": "S", "group": "other"}],
                                                 "routes": [], "ladder": ladder})
        rev = d["rev"]
        self.register("ctl")
        self.register("sup", "supply")
        u = self.call("/api/collector/updates?name=ctl&rev=0")[1]
        self.assertEqual((u["rev"], u["sites"][0]["site_id"]), (rev, "s"))
        self.assertEqual(u["ladder"]["levels"]["3"][0]["kind"], "controller")
        self.assertTrue(self.call("/api/collector/updates?name=ctl&rev=" + rev)[1].get("unchanged"))
        us = self.call("/api/collector/updates?name=sup&rev=0")[1]
        self.assertNotIn("controller", [x["kind"] for x in us["ladder"]["levels"]["3"]])
        self.assertEqual(self.call("/api/collector/updates?name=nobody")[0], 403)

    def test_04_rung3_ask_pending_then_bot_answers(self):
        self.register("a1")
        body = {"name": "a1", "level": 3, "reason": "메뉴를 못 찾음", "goal": "g", "map_size": [100, 70],
                "image_jpeg_b64": base64.b64encode(JPEG).decode()}
        st, d = self.call("/api/ladder/ask", body)
        self.assertEqual(d["status"], "pending")
        cid = d["id"]
        self.assertEqual(self.call("/api/ladder/ask/%s?name=a1" % cid)[1]["status"], "pending")
        asks = self.call("/api/bot/asks", headers={"X-Bot-Token": "bot"})[1]["asks"]
        self.assertTrue(any(a["id"] == cid and a.get("image_jpeg_b64") for a in asks))
        st, _ = self.call("/api/bot/answer/" + cid, {"decision": "action", "action": {"action": "CLICK", "x": 5, "y": 6}},
                          {"X-Bot-Token": "bot"})
        self.assertEqual(st, 200)
        d = self.call("/api/ladder/ask/%s?name=a1" % cid)[1]
        self.assertEqual((d["decision"], d["action"]["x"]), ("action", 5))
        self.assertEqual(self.call("/api/ladder/ask/%s?name=other" % cid)[0], 404)

    def test_05_security_ask_goes_to_manager(self):
        self.register("a2")
        d = self.call("/api/ladder/ask", {"name": "a2", "reason": "reCAPTCHA 화면", "goal": "g"})[1]
        self.assertEqual((d["status"], d["decision"]), ("answered", "escalate"))

    def test_06_manager_call_type_text_is_memory_only(self):
        self.register("m1")
        d = self.call("/api/escalations", {"name": "m1", "level": 4, "reason": "로그인 문제", "goal": "g", "phone": True,
                                           "image_jpeg_b64": base64.b64encode(JPEG).decode(), "map_size": [100, 70]})[1]
        cid = d["id"]
        # the bot cannot answer the manager's call
        self.assertEqual(self.call("/api/bot/answer/" + cid, {"decision": "skip"}, {"X-Bot-Token": "bot"})[0], 403)
        self.assertEqual(self.admin("/api/admin/answer/" + cid, {"decision": "type", "text": "Secret#9"})[0], 200)
        self.assertEqual(self.admin("/api/admin/answer/" + cid, {"decision": "skip"})[0], 400)   # once only
        d = self.call("/api/escalations/%s?name=m1" % cid)[1]
        self.assertEqual((d["decision"], d["text"]), ("type", "Secret#9"))
        d2 = self.call("/api/escalations/%s?name=m1" % cid)[1]
        self.assertNotIn("text", d2)                                   # handed out once
        raw = open(os.path.join(self.tmp.name, "controller.db"), "rb").read()
        self.assertNotIn(b"Secret#9", raw)

    def test_07_program_update_only_when_approved(self):
        self.register("p1")
        pkg = os.path.join(self.tmp.name, "updates", "collector_S0331.zip")
        open(pkg, "wb").write(b"PK test")
        d = self.admin("/api/admin/program", {"version": "S-0.3.31-CONTROL", "file": "collector_S0331.zip", "approved": False})[1]
        u = self.call("/api/collector/updates?name=p1&rev=x")[1]
        self.assertNotIn("program", u)
        self.assertEqual(self.call("/api/collector/update_file/collector_S0331.zip?name=p1")[0], 404)
        self.admin("/api/admin/program", {"version": "S-0.3.31-CONTROL", "file": "collector_S0331.zip", "approved": True})
        u = self.call("/api/collector/updates?name=p1&rev=x")[1]
        self.assertEqual(u["program"]["sha256"], d["sha256"])
        with urlopen(self.base + "/api/collector/update_file/collector_S0331.zip?name=p1") as r:
            self.assertEqual(r.read(), b"PK test")
        self.assertEqual(self.admin("/api/admin/program", {"version": "v", "file": "../x"})[0], 400)

    def test_08_token_bound_collector(self):
        self.admin("/api/admin/collector", {"name": "t1", "status": "approved", "token": "abc"})
        self.assertEqual(self.call("/api/collector/instruction/t1")[0], 403)
        self.assertEqual(self.call("/api/collector/instruction/t1", headers={"X-Collector-Token": "abc"})[0], 200)

    def test_09_admin_page(self):
        with urlopen(self.base + "/admin") as r:
            html = r.read().decode()
        self.assertIn("관리인 호출", html)


if __name__ == "__main__":
    unittest.main()
