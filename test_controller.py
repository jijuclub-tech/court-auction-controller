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
        cls.test_tokens = {}
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
        if path.startswith("/api/collector/") or path.startswith("/api/policy") or path.startswith("/api/ladder/") or path.startswith("/api/escalations"):
            from urllib.parse import urlsplit, parse_qs
            request_path = urlsplit(path)
            name = (body or {}).get("name", "") if isinstance(body, dict) else ""
            if not name:
                name = parse_qs(request_path.query).get("name", [""])[0]
            if not name and request_path.path.startswith("/api/collector/instruction/"):
                name = request_path.path.rsplit("/", 1)[-1]
            if name in self.test_tokens and "X-Collector-Token" not in h:
                h["X-Collector-Token"] = self.test_tokens[name]
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
        self.assertEqual(self.admin("/api/admin/collector", {"name": name, "status": "approved", "type": ctype})[0], 200)
        synthetic_key = "synthetic-device-key-" + name + "-A" * 5
        with self.c.db() as con:
            con.execute("UPDATE collectors SET token_hash=? WHERE name=?",
                        (self.c.th(synthetic_key), name))
        self.test_tokens[name] = synthetic_key

    def test_01_health_and_admin_guard(self):
        self.assertEqual(self.call("/api/health")[1]["ok"], True)
        self.assertEqual(self.call("/api/admin/state")[0], 403)

    def test_02_connect_needs_approval_then_instruction_and_report(self):
        st, d = self.call("/api/collector/connect", {"name": "jiju01", "type": "control"})
        self.assertEqual(st, 403)  # unknown device must not be auto-registered
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
        with urlopen(Request(self.base + "/api/collector/update_file/collector_S0331.zip?name=p1",
                             headers={"X-Collector-Token": self.test_tokens["p1"]})) as r:
            self.assertEqual(r.read(), b"PK test")
        self.assertEqual(self.admin("/api/admin/program", {"version": "v", "file": "../x"})[0], 400)

    def test_08_token_bound_collector(self):
        self.register("t1")
        self.assertEqual(self.call("/api/collector/instruction/t1", headers={"X-Collector-Token": ""})[0], 403)
        self.assertEqual(self.call("/api/collector/instruction/t1", headers={"X-Collector-Token": "incorrect"})[0], 403)
        self.assertEqual(self.call("/api/collector/instruction/t1", headers={"X-Collector-Token": self.test_tokens["t1"]})[0], 200)
        self.assertEqual(self.call("/api/policy", headers={"X-Collector-Token": ""})[0], 403)

    def test_10_program_upload_and_defaults(self):
        req = Request(self.base + "/api/admin/program_upload?file=pkg_S0332.zip&version=S-0.3.32-CONTROL&approved=1",
                      data=b"PK zip bytes", headers={"X-Admin-Token": "adm", "Content-Type": "application/octet-stream"},
                      method="POST")
        with urlopen(req) as r:
            d = json.loads(r.read())
        self.assertEqual(d["bytes"], 12)
        self.register("u1")
        self.assertEqual(self.call("/api/collector/updates?name=u1&rev=x")[1]["program"]["file"], "pkg_S0332.zip")
        bad = Request(self.base + "/api/admin/program_upload?file=../x&version=v", data=b"x",
                      headers={"X-Admin-Token": "adm"}, method="POST")
        with self.assertRaises(HTTPError):
            urlopen(bad)
        st = self.admin("/api/admin/state")[1]
        self.assertIn("courtauction", [x["site_id"] for x in st["defaults"]["sites"]])
        self.assertEqual(st["defaults"]["ladder"]["levels"]["3"][0]["kind"], "controller")
        self.assertTrue(st["audit"])

    def test_09_admin_page(self):
        with urlopen(self.base + "/admin") as r:
            html = r.read().decode()
        self.assertIn("관리인 호출", html)
        self.assertIn("정해진 경로", html)

    # ---- 학습서 (S-0.1.2) ----
    def _wait_job(self, site, statuses, tries=50):
        import time
        for _ in range(tries):
            jobs = [j for j in self.admin("/api/admin/state")[1]["learning"]["jobs"] if j["site_id"] == site]
            if jobs and jobs[0]["status"] in statuses:
                return jobs[0]
            time.sleep(0.1)
        self.fail("job did not reach %s: %s" % (statuses, jobs))

    def test_11_learning_defaults_and_experience_privacy(self):
        st = self.admin("/api/admin/state")[1]["learning"]
        self.assertEqual(st["settings"]["writer"], "grok")          # 관리인 지시: 글록 봇 기본
        self.assertEqual(st["settings"]["ai"]["grok"]["mode"], "bot")
        self.assertEqual(sorted(st["ais"]), ["chatgpt", "claude", "deepseek", "gemini", "grok"])
        self.register("learn01")
        rec = {"site_id": "courtauction", "route_id": "court_appraisal", "step": 1, "screen": "메인", "action": "click",
               "x": 10, "y": 5, "result": "ok", "seconds": 2.5, "level": 1, "ai": "deepseek",
               "text": "secret-password", "password": "pw", "image_jpeg_b64": "AAAA"}
        code, d = self.call("/api/collector/experience", {"name": "learn01", "records": [rec] * 6})
        self.assertEqual((code, d["stored"]), (200, 6))
        import sqlite3
        con = sqlite3.connect(os.path.join(self.tmp.name, "controller.db"))
        dump = "\n".join(con.iterdump())
        self.assertNotIn("secret-password", dump)
        self.assertNotIn("AAAA", dump)
        self.assertEqual(self.call("/api/collector/experience", {"name": "nobody", "records": [rec]})[0], 403)
        self.assertEqual(self.call("/api/collector/experience", {"name": "learn01", "records": [dict(rec, result="maybe")]})[0], 400)
        # no book yet
        self.assertIsNone(self.call("/api/collector/visit", {"name": "learn01", "site_id": "courtauction"})[1]["book"])

    def test_12_grok_bot_writes_chatgpt_reviews_then_trial_and_live(self):
        self.register("learn02")
        self.register("learn03")
        recs = [{"site_id": "iros", "route_id": "r1", "step": 1, "result": "ok", "level": 1, "action": "CLICK"}] * 5 + \
               [{"site_id": "iros", "route_id": "r1", "step": 2, "result": "fail", "level": 2, "popup": "알림창이 이름을 가림"}] * 3
        self.call("/api/collector/experience", {"name": "learn02", "records": recs})
        reviewed = []
        def fake_ai(ai, model, system, user):
            reviewed.append(ai)
            return {"verdict": "fix", "problems": [{"route_id": "r1", "step": 2, "problem": "대기 시간 짧음"}], "note": "n"}
        self.c.learning.CALL_AI = fake_ai
        self.assertEqual(self.admin("/api/admin/learning/build", {"site_id": "iros"})[0], 200)
        self.assertEqual(self.admin("/api/admin/learning/build", {"site_id": "iros"})[0], 400)   # already running
        self.assertEqual(self.call("/api/bot/learning", headers={"X-Bot-Token": "no"})[0], 403)
        jobs = self.call("/api/bot/learning", headers={"X-Bot-Token": "bot"})[1]["jobs"]
        job = [j for j in jobs if j["site_id"] == "iros"][0]
        self.assertEqual(job["stage"], "write")
        st = {s["step"]: s for s in job["input"]["step_stats"]}
        self.assertEqual(st[1]["suggested"], "easy")
        self.assertEqual(st[2]["problems"], {"알림창이 이름을 가림": 3})
        draft = {"summary": "s", "changes": ["c"], "steps": [
            {"route_id": "r1", "step": 1, "screen": "a", "difficulty": "easy", "hint": "h", "wait_seconds": 2},
            {"route_id": "r1", "step": 2, "screen": "b", "difficulty": "hard", "hint": "h", "wait_seconds": 5,
             "obstacles": [{"sign": "알림창", "response": "닫기 먼저"}]}]}
        self.assertEqual(self.call("/api/bot/learning/" + job["id"], {"stage": "review", "result": {}},
                                   {"X-Bot-Token": "bot"})[0], 403)    # reviewer is ChatGPT, not the bot
        self.assertEqual(self.call("/api/bot/learning/" + job["id"], {"stage": "write", "result": draft},
                                   {"X-Bot-Token": "bot"})[0], 200)
        self._wait_job("iros", ("ready",))
        self.assertEqual(reviewed, ["chatgpt"])
        books = [b for b in self.admin("/api/admin/state")[1]["learning"]["books"] if b["site_id"] == "iros"]
        self.assertEqual((books[0]["version"], books[0]["status"], books[0]["review"]["verdict"]), (1, "draft", "fix"))
        self.assertEqual(books[0]["body"]["steps"][0]["start_level"], 1)
        self.assertEqual(books[0]["body"]["steps"][1]["start_level"], 2)
        # draft is not sent
        self.assertIsNone(self.call("/api/collector/visit", {"name": "learn02", "site_id": "iros"})[1]["book"])
        # trial on learn02 only
        self.assertEqual(self.admin("/api/admin/learning/book", {"site_id": "iros", "version": 1, "action": "trial",
                                                                 "collector": "learn02"})[0], 200)
        v = self.call("/api/collector/visit", {"name": "learn02", "site_id": "iros"})[1]
        self.assertEqual((v["version"], v["trial"]), (1, True))
        self.assertIsNone(self.call("/api/collector/visit", {"name": "learn03", "site_id": "iros"})[1]["book"])
        # live for all; same version -> unchanged
        self.admin("/api/admin/learning/book", {"site_id": "iros", "version": 1, "action": "live"})
        v = self.call("/api/collector/visit", {"name": "learn03", "site_id": "iros", "version": 1})[1]
        self.assertTrue(v.get("unchanged"))
        self.assertNotIn("book", v)

    def test_13_choose_any_of_five_ais_and_api_failure_is_shown(self):
        r = self.admin("/api/admin/learning/settings", {"writer": "claude", "reviewer": "deepseek",
                                                        "ai": {"claude": {"model": "claude-opus-4-1"}}})
        self.assertEqual(r[0], 200)
        self.assertEqual(self.admin("/api/admin/learning/settings", {"writer": "nobody"})[0], 400)
        self.assertEqual(self.admin("/api/admin/learning/settings", {"ai": {"chatgpt": {"mode": "bot"}}})[0], 400)
        # key is stored (owner-only file) and never shown
        self.assertEqual(self.admin("/api/admin/learning/key", {"ai": "claude", "key": "sk-test-xyz"})[1]["has_key"], True)
        st = self.admin("/api/admin/state")[1]
        self.assertNotIn("sk-test-xyz", json.dumps(st))
        self.assertEqual(oct(os.stat(os.path.join(self.tmp.name, "ai_keys.json")).st_mode & 0o777), "0o600")
        self.register("learn04")
        self.call("/api/collector/experience", {"name": "learn04", "records": [{"site_id": "onbid", "result": "ok"}]})
        calls = []
        def boom(ai, model, system, user):
            calls.append((ai, model))
            raise RuntimeError("quota")
        self.c.learning.CALL_AI = boom
        self.admin("/api/admin/learning/build", {"site_id": "onbid"})
        j = self._wait_job("onbid", ("failed",))
        self.assertIn("quota", j["error"])
        self.assertEqual(calls, [("claude", "claude-opus-4-1")])
        self.admin("/api/admin/learning/settings", {"writer": "grok", "reviewer": "chatgpt"})

    def test_15_wallet_pairing_and_every_request_auth(self):
        from pathlib import Path
        import key_wallet
        import collector_key_wallet_T_v0_2 as keys

        wallet_file = Path(self.tmp.name) / "key-wallet.json"
        output = Path(self.tmp.name) / "pairing-out"
        admin_password = "TEST-PROVISIONING-ADMIN-12345"
        vault, master = key_wallet.new_wallet(admin_password)
        key_wallet.write_private(wallet_file, vault, create=True)
        key_wallet.erase(master)
        keys.provision_batch(wallet_file, self.c.DB_PATH, [("PAIR-NODE-01", "control")],
                             output, admin_password)
        pair = json.loads((output / "PAIR-NODE-01.pair.json").read_text())
        self.assertEqual(self.call("/api/collector/connect", {"name": "PAIR-NODE-01"})[0], 403)
        self.assertEqual(self.call("/api/collector/key/claim",
                                   {"name": "OTHER-NODE", "claim_code": pair["claim_code"]})[0], 403)
        status, result = self.call("/api/collector/key/claim",
                                   {"name": "PAIR-NODE-01", "claim_code": pair["claim_code"]})
        self.assertEqual(status, 200)
        token = result["collector_token"]
        self.assertEqual(self.call("/api/collector/key/claim",
                                   {"name": "PAIR-NODE-01", "claim_code": pair["claim_code"]})[0], 403)
        self.assertEqual(self.call("/api/collector/connect", {"name": "PAIR-NODE-01"},
                                   {"X-Collector-Token": token})[0], 200)
        self.assertEqual(self.call("/api/collector/heartbeat", {"name": "PAIR-NODE-01"},
                                   {"X-Collector-Token": ""})[0], 403)
        self.assertEqual(self.call("/api/collector/heartbeat", {"name": "PAIR-NODE-01"},
                                   {"X-Collector-Token": token})[0], 200)
        self.assertEqual(self.call("/api/policy?name=PAIR-NODE-01",
                                   headers={"X-Collector-Token": token})[0], 200)

    def test_14_admin_page_has_learning_tab(self):
        with urlopen(self.base + "/admin") as r:
            self.assertIn("학습서", r.read().decode())


if __name__ == "__main__":
    unittest.main()
