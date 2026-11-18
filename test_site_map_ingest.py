"""Synthetic-only regression tests. Run locally; never upload real S-1.0 maps."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
import site_map_ingest as maps


class Fake:
    def __init__(self, dbpath):
        self.dbpath = dbpath
        self.messages = []
        self.client_address = ("203.0.113.10", 23456)
        self.headers = {}
        self.connection = object()

    def _site_map_db(self):
        c = sqlite3.connect(self.dbpath)
        c.row_factory = sqlite3.Row
        return c

    def _json(self, code, value):
        self.messages.append((code, value))


class MapTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = str(Path(self.dir.name) / "test.db")
        self.handler = Fake(self.db)
        with self.handler._site_map_db() as con:
            maps.init(con, Path(self.dir.name) / "data")

    def batch(self):
        payload = {"screen": "search", "steps": [{"button": "검색", "next": "result"}],
                   "revisit_hours": 24}
        return {"protocol": maps.PROTOCOL, "site_id": "demo-court",
                "batch_id": "batch-001", "map_version": "v1",
                "captured_at": "2026-11-18T10:00:00+09:00",
                "source_url": "https://example.org/court/search",
                "payload": payload, "payload_sha256": maps.digest(maps.canonical(payload)),
                "evidence": []}

    def test_store_duplicate_and_conflict(self):
        b = self.batch()
        code, r = maps.receive(self.handler, b)
        self.assertEqual(code, 201)
        self.assertEqual(r["status"], "stored")
        self.assertEqual(r["backup_status"], "pending")
        code2, r2 = maps.receive(self.handler, b)
        self.assertEqual((code2, r2["duplicate"], r2["sha256"]),
                         (200, True, r["sha256"]))
        b["payload"]["screen"] = "different"
        b["payload_sha256"] = maps.digest(maps.canonical(b["payload"]))
        code3, _ = maps.receive(self.handler, b)
        self.assertEqual(code3, 409)
        with self.handler._site_map_db() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM site_map_batches").fetchone()[0], 1)

    def test_reject_modified_content(self):
        b = self.batch()
        b["payload"]["screen"] = "tampered"
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            maps.validate(b)

    def test_reject_secret_and_invalid_url(self):
        b = self.batch()
        b["payload"]["password"] = "no"
        with self.assertRaises(ValueError):
            maps.validate(b)
        b = self.batch()
        b["source_url"] = "https://example.org/?token=secret"
        with self.assertRaises(ValueError):
            maps.validate(b)

    def test_reject_path_traversal(self):
        for name in ("../../outside.png", "/tmp/file.jpg", r"a\b.png", "a//b.png"):
            with self.assertRaises(ValueError):
                maps.safe_path(name)

    def test_reject_bad_image_hash(self):
        b = self.batch()
        raw = b"\x89PNG\r\n\x1a\nsynthetic"
        b["evidence"] = [{"path": "shots/synthetic.png",
                          "sha256": "0" * 64,
                          "content_b64": __import__("base64").b64encode(raw).decode()}]
        with self.assertRaisesRegex(ValueError, "evidence hash mismatch"):
            maps.validate(b)

    def test_http_remote_rejected(self):
        self.assertTrue(maps.route(self.handler, "POST", "/api/v1/site-maps/batches", {}))
        self.assertEqual(self.handler.messages[-1][0], 403)

    def test_admin_route_does_not_bypass_auth(self):
        self.handler._admin = lambda: False
        maps.route(self.handler, "GET", "/api/admin/site-maps/state", {})
        self.assertEqual(self.handler.messages[-1][0], 403)


if __name__ == "__main__":
    unittest.main()
