"""Offline end-to-end performance API tests (fake documents only).

Run: python3 -m unittest test_performance -v
No network APIs, Actions, collector device, PDF data, or real credentials needed.
"""
import importlib
import json
import os
import socket
import tempfile
import threading
import unittest
from datetime import timedelta
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class PerformanceAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["CONTROLLER_DATA"] = cls.tmp.name
        os.environ["CONTROLLER_ADMIN_TOKEN"] = "fake-admin-test-token"
        os.environ.pop("CONTROLLER_AI", None)
        os.environ.pop("NOTIFY_URL", None)  # never send notifications during offline tests
        import controller
        cls.c = importlib.reload(controller)
        cls.c.init()
        cls.server = ThreadingHTTPServer(("127.0.0.1", free_port()), cls.c.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, path, body=None, token=None, admin=False):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Collector-Token"] = token
        if admin:
            headers["X-Admin-Token"] = "fake-admin-test-token"
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        req = Request(self.base + path, data=data, headers=headers,
                      method="POST" if body is not None else "GET")
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def register(self, name, token):
        code, _ = self.call("/api/admin/collector", {"name": name, "status": "approved",
                           "type": "control"}, admin=True)
        self.assertEqual(code, 200)
        # Synthetic verifier in isolated test DB. Real keys are issued by the wallet CLI.
        with self.c.db() as con:
            con.execute("UPDATE collectors SET token_hash=? WHERE name=?",
                        (self.c.th(token), name))

    def sample(self, name="서울1호기", event_id="evt100", **kw):
        from performance import _today
        b = {"name": name, "event_id": event_id, "work_date": _today().isoformat(),
             "court": "서울중앙", "document_type": "감정평가서", "computer": "jijuclub5",
             "outcome": "success", "verification_status": "VERIFIED", "case_no": "2025타경12345",
             "source_sha256": "a" * 64, "duration_seconds": 21}
        b.update(kw)
        return b

    def test_01_requires_authorized_collector_token(self):
        self.register("서울1호기", "test-token")
        b = self.sample()
        self.assertEqual(self.call("/api/collector/performance", b)[0], 403)
        self.assertEqual(self.call("/api/collector/performance", b, "incorrect")[0], 403)
        self.assertEqual(self.call("/api/collector/performance", b, "test-token")[0], 200)
        self.assertEqual(self.call("/api/admin/performance")[0], 403)

    def test_02_idempotent_and_tampered_retry_rejected(self):
        self.register("retry-collector", "t2")
        b = self.sample(name="retry-collector", event_id="retryA")
        status, first = self.call("/api/collector/performance", b, "t2")
        self.assertTrue(first["recorded"])
        status, second = self.call("/api/collector/performance", b, "t2")
        self.assertTrue(second["duplicate_delivery"])
        self.assertFalse(second["recorded"])
        self.assertEqual(self.call("/api/collector/performance", dict(b, outcome="failure"), "t2")[0], 400)

    def test_03_verified_success_only(self):
        self.register("verify-collector", "t3")
        b = self.sample(name="verify-collector", event_id="proof01",
                        verification_status="CASE_UNVERIFIED")
        self.assertEqual(self.call("/api/collector/performance", b, "t3")[0], 400)
        b.pop("source_sha256")
        b["verification_status"] = "VERIFIED"
        self.assertEqual(self.call("/api/collector/performance", b, "t3")[0], 400)

    def test_04_count_categories_and_filters(self):
        self.register("metrics-collector", "t4")
        for code, outcome in (("a1", "success"), ("a2", "failure"), ("a3", "duplicate")):
            b = self.sample(name="metrics-collector", event_id=code, outcome=outcome)
            self.assertEqual(self.call("/api/collector/performance", b, "t4")[0], 200)
        day = self.sample()["work_date"]
        status, q = self.call("/api/admin/performance?period=day&date="+day+
                              "&collector=metrics-collector", admin=True)
        self.assertEqual(status, 200)
        self.assertEqual([q["totals"][k] for k in ("successes", "failures", "duplicates")], [1, 1, 1])
        self.assertEqual(q["by_court"][0]["label"], "서울중앙")
        status, q = self.call("/api/admin/performance?period=week&date="+day+
                              "&document_type=현황조사서", admin=True)
        self.assertEqual(q["status"], "no_reports")
        self.assertEqual(q["totals"]["successes"], 0)

    def test_05_close_late_event_and_reconcile_difference(self):
        from performance import _today
        self.register("late-collector", "t5")
        day = (_today()-timedelta(days=1)).isoformat()
        b = self.sample(name="late-collector", event_id="before", work_date=day)
        self.assertEqual(self.call("/api/collector/performance", b, "t5")[0], 200)
        code, closed = self.call("/api/admin/performance/close", {"work_date": day}, admin=True)
        self.assertEqual((code, closed["closure"]["successes"] >= 1), (200, True))
        self.assertTrue(self.call("/api/admin/performance/close", {"work_date": day},
                                  admin=True)[1]["already_closed"])
        b["event_id"] = "after"
        code, late = self.call("/api/collector/performance", b, "t5")
        self.assertTrue(late["late_after_close"])
        q = self.call("/api/admin/performance?period=day&date="+day, admin=True)[1]
        self.assertGreaterEqual(q["late_events"], 1)
        code, r = self.call("/api/admin/performance/reconcile",
                            {"work_date": day, "court": "서울중앙",
                             "document_type": "감정평가서", "collector": "late-collector",
                             "computer": "jijuclub5", "external_count": 11}, admin=True)
        self.assertEqual(code, 200)
        self.assertFalse(r["matched"])
        self.assertEqual(r["difference"], 11-r["stored"])

    def test_06_incidents_idempotent_and_safe_question_topics(self):
        self.register("incident-collector", "t6")
        b = {"name": "incident-collector", "event_id": "incident1",
             "code": "POWER_DISCONNECTED", "computer": "jijuclub5", "battery_percent": 82}
        code, first = self.call("/api/collector/incident", b, "t6")
        self.assertEqual(code, 200)
        self.assertTrue(first["recorded"])
        self.assertFalse(self.call("/api/collector/incident", b, "t6")[1]["recorded"])
        q = self.call("/api/admin/performance/incidents", admin=True)[1]
        self.assertTrue(any(i["code"] == "POWER_DISCONNECTED" for i in q["incidents"]))
        self.assertEqual(self.call("/api/admin/performance/question", {"topic":"daily_count"},admin=True)[0], 200)
        topics = self.call("/api/admin/performance/questions", admin=True)[1]["questions"]
        self.assertTrue(any(q["topic"] == "daily_count" for q in topics))
        self.assertEqual(self.call("/api/admin/performance/question", {"topic":"my password=secret"},admin=True)[0],400)

    def test_07_automatic_closure_and_legacy_report_unchanged(self):
        from performance import _today, auto_close
        day = (_today()-timedelta(days=2)).isoformat()
        self.register("auto-collector", "t7")
        b = self.sample(name="auto-collector", event_id="old", work_date=day)
        self.assertEqual(self.call("/api/collector/performance", b, "t7")[0], 200)
        with self.c.db() as con:
            self.assertGreaterEqual(auto_close(con), 1)
            self.assertEqual(auto_close(con), 0)
        legacy = self.call("/api/collector/report",
                           {"name": "auto-collector", "case_no": "2025타경1", "success": True}, "t7")
        self.assertEqual(legacy[0], 200)


if __name__ == "__main__":
    unittest.main()
