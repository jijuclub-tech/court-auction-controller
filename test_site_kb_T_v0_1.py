"""Synthetic-only KB API regression tests. Never use live site knowledge or secrets."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import site_kb_T_v0_1 as kb


class Fake:
    def __init__(self, path):
        self.path = path
        self.headers = {"X-Collector-Name": "node-1", "X-Collector-Token": "synthetic"}
        self.messages = []
        self.payload = None

    def _site_map_db(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _collector_ok(self, con, name):
        if name == "node-1" and self.headers.get("X-Collector-Token") == "synthetic":
            return {"name": name, "status": "approved"}
        return None

    def _admin(self):
        return self.headers.get("X-Admin-Token") == "synthetic-admin"

    def _json(self, code, obj):
        self.messages.append((code, obj))

    def _body(self):
        return self.payload

    def call(self, path, method="GET", query=None, body=None):
        self.payload = body
        if body is not None:
            self.headers["Content-Length"] = str(len(json.dumps(body).encode("utf-8")))
        else:
            self.headers.pop("Content-Length", None)
        recognized = kb.route(self, method, path, query or {})
        assert recognized
        return self.messages[-1]


class KBTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.fake = Fake(str(Path(temp.name) / "controller.db"))
        with self.fake._site_map_db() as con:
            kb.init(con)
        patcher = patch.object(kb.site_map_ingest, "ssl_ok", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sample(self):
        return {
            "event_id": "node1-event-01", "scope": "site",
            "site": "www.courtauction.go.kr", "kind": "failure",
            "symptom": "row_gap_bad", "cause": "virtual keypad layout changed",
            "recovery": "ask operator and revalidate screen",
            "result": "operator required", "evidence_sha256": "f" * 64,
            "observed_at": "2026-11-22T15:00:00+09:00"
        }

    def test_empty_manifest_never_claims_ready(self):
        code, result = self.fake.call("/kb/manifest", query={"scope": "common"})
        self.assertEqual(code, 200)
        self.assertFalse(result["ready"])
        self.assertEqual(result["warning"], "NO_APPROVED_KNOWLEDGE")
        self.assertEqual(result["files"], [])

    def test_append_only_report_and_idempotency(self):
        body = self.sample()
        self.assertEqual(self.fake.call("/kb/report", "POST", body=body)[0], 201)
        code, data = self.fake.call("/kb/report", "POST", body=body)
        self.assertEqual(code, 200)
        self.assertTrue(data["duplicate"])
        other = dict(body, cause="changed diagnosis")
        self.assertEqual(self.fake.call("/kb/report", "POST", body=other)[0], 409)
        with self.fake._site_map_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM kb_reports").fetchone()[0], 1)

    def test_sensitive_and_extra_keys_refused(self):
        for changes in (
            {"password": "secret123"},
            {"cause": "email: person@example.com"},
            {"recovery": "Authorization: Bearer abcdef"},
            {"cause": "phone 010-1234-5678"},
        ):
            body = dict(self.sample(), **changes)
            self.assertEqual(self.fake.call("/kb/report", "POST", body=body)[0], 422)
        with self.fake._site_map_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM kb_reports").fetchone()[0], 0)

    def test_approval_is_human_only_and_versions_immutable(self):
        body = self.sample()
        self.fake.call("/kb/report", "POST", body=body)
        approval = {
            "scope": "site", "site": "www.courtauction.go.kr",
            "path": "access.md", "content": "Approved method, v1.",
            "source_event": "report:node1-event-01"
        }
        self.assertEqual(self.fake.call("/kb/admin/approve", "POST", body=approval)[0], 403)
        self.fake.headers["X-Admin-Token"] = "synthetic-admin"
        code, result = self.fake.call("/kb/admin/approve", "POST", body=approval)
        self.assertEqual(code, 201)
        self.assertEqual(result["revision"], 1)
        code, manifest = self.fake.call("/kb/manifest", query={
            "scope": "site", "site": "www.courtauction.go.kr"})
        self.assertTrue(manifest["ready"])
        oldhash = manifest["files"][0]["sha256"]
        fileq = {"scope": "site", "site": "www.courtauction.go.kr",
                 "path": "access.md", "sha256": oldhash}
        self.assertEqual(self.fake.call("/kb/file", query=fileq)[1]["content"], "Approved method, v1.")
        approval["content"] = "Approved method, v2."
        self.assertEqual(self.fake.call("/kb/admin/approve", "POST", body=approval)[1]["revision"], 2)
        self.assertEqual(self.fake.call("/kb/file", query=fileq)[1]["content"], "Approved method, v1.")
        with self.fake._site_map_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM kb_docs").fetchone()[0], 2)

    def test_unauthorized_node_denied_read_and_write(self):
        self.fake.headers["X-Collector-Token"] = "bad"
        self.assertEqual(self.fake.call("/kb/manifest", query={"scope": "common"})[0], 403)
        self.assertEqual(self.fake.call("/kb/report", "POST", body=self.sample())[0], 403)
        self.fake.headers["X-Collector-Token"] = "synthetic"
        self.fake.headers["X-Collector-Name"] = "unregistered-bot"
        self.assertEqual(self.fake.call("/kb/manifest", query={"scope": "common"})[0], 403)

    def test_https_required(self):
        with patch.object(kb.site_map_ingest, "ssl_ok", return_value=False):
            self.assertEqual(self.fake.call("/kb/manifest", query={"scope": "common"})[0], 403)

    def test_ack_dedup_and_admin_status(self):
        ack = {"event_id": "ack-01", "scope": "common", "site": "-",
               "version": "a" * 64, "outcome": "received",
               "observed_at": "2026-11-22T15:01:00+09:00"}
        self.assertEqual(self.fake.call("/kb/ack", "POST", body=ack)[0], 201)
        self.assertEqual(self.fake.call("/kb/ack", "POST", body=ack)[0], 200)
        changed = dict(ack, outcome="applied")
        self.assertEqual(self.fake.call("/kb/ack", "POST", body=changed)[0], 409)
        self.fake.headers["X-Admin-Token"] = "synthetic-admin"
        code, status = self.fake.call("/kb/admin/status")
        self.assertEqual(code, 200)
        self.assertEqual(len(status["acks"]), 1)

    def test_incorrect_site_and_path_rejected(self):
        self.assertEqual(self.fake.call("/kb/manifest", query={
            "scope": "site", "site": "../other"})[0], 422)
        self.fake.headers["X-Admin-Token"] = "synthetic-admin"
        self.assertEqual(self.fake.call("/kb/admin/approve", "POST", body={
            "scope": "common", "path": "../bad.md", "content": "safe",
            "source_event": "manual"
        })[0], 422)

    def test_old_report_remains_pending_until_approval(self):
        self.fake.call("/kb/report", "POST", body=self.sample())
        self.fake.headers["X-Admin-Token"] = "synthetic-admin"
        code, status = self.fake.call("/kb/admin/status", query={
            "site": "www.courtauction.go.kr"})
        self.assertEqual(code, 200)
        self.assertEqual(status["recent"][0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
