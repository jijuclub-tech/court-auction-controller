"""Offline regression tests for #50; no real HID, browser, NAS or network."""
import hashlib
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import collector_file_ingest_T_v1_8_0 as server
import collector_launch_options_T_v1_8_0 as client


class OptionTests(unittest.TestCase):
    def test_legacy_is_unchanged(self):
        opts, legacy = client.split_options(["--route", "a", "--debug"])
        self.assertEqual(opts, client.Options())
        self.assertEqual(legacy, ["--route", "a", "--debug"])

    def test_four_explicit_flags(self):
        opts, legacy = client.split_options(["/lock", "/upload", "/kiosk", "other"])
        self.assertEqual(opts, client.Options(lock=True, upload=True, kiosk=True))
        self.assertEqual(legacy, ["other"])
        self.assertTrue(client.split_options(["/unlock"])[0].unlock)
        with self.assertRaises(ValueError):
            client.split_options(["/unlock", "/lock"])

    def test_https_only_and_policy_hosts(self):
        self.assertEqual(client.url_from_policy("https://example.org"),
                         "https://example.org/api/v1/collector/upload")
        with self.assertRaises(ValueError):
            client.url_from_policy("http://example.org")
        with self.assertRaises(ValueError):
            client.url_from_policy("https://bob:secret@example.org")
        with self.assertRaises(ValueError):
            client.url_from_policy("https://example.org/other")
        self.assertTrue(client.allowed_site("https://www.courtauction.go.kr/search",
                                            ["courtauction.go.kr"]))
        self.assertFalse(client.allowed_site("https://courtauction.go.kr.evil.test",
                                             ["courtauction.go.kr"]))
        self.assertFalse(client.allowed_site("http://courtauction.go.kr", ["courtauction.go.kr"]))

    def test_kiosk_closes_only_disallowed_tabs(self):
        policy = client.KioskPolicy(["courtauction.go.kr"])
        closed, events = [], []
        tabs = [
            {"id": "a", "type": "page", "url": "https://courtauction.go.kr"},
            {"id": "b", "type": "page", "url": "https://courtauction.go.kr.evil.test"},
            {"id": "c", "type": "other", "url": "about:blank"},
        ]
        self.assertEqual(policy.enforce(tabs, closed.append, lambda *a, **k: events.append(a)), 1)
        self.assertEqual(closed, ["b"])

    def test_lock_fail_closed_and_pico_excluded(self):
        base = dict(pico_device_ids=["HID\\VID_2E8A&PID_0005"],
                    physical_device_ids=["HID\\VID_1234&PID_5678"],
                    watchdog_armed=True, unlock_channel_tested=True,
                    remote_session_tested=True)
        self.assertEqual(client.lock_preflight(**base)[0], True)
        self.assertEqual(client.lock_preflight(**{**base, "watchdog_armed": False})[0], False)
        self.assertEqual(client.lock_preflight(**{
            **base, "physical_device_ids": ["HID\\VID_2E8A&PID_0005"]
        })[0], False)
        self.assertEqual(client.lock_preflight(**{
            **base, "remote_session_tested": False
        })[0], False)


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.root = Path(self.work.name)
        self.con = sqlite3.connect(":memory:")
        server.init(self.con)
        self.addCleanup(self.con.close)

    def test_integrity_and_duplicate_receipt(self):
        blob = b"%PDF-1.7\\n" + b"safe-test-content"
        hx = hashlib.sha256(blob).hexdigest()
        first = server.store_stream(
            self.con, io.BytesIO(blob), collector="COLLECTOR-1", size=len(blob),
            expected_hash=hx, document_type="appraisal", root=self.root,
        )
        second = server.store_stream(
            self.con, io.BytesIO(blob), collector="COLLECTOR-1", size=len(blob),
            expected_hash=hx, document_type="appraisal", root=self.root,
        )
        self.assertTrue(first["ok"])
        self.assertFalse(first["backup_verified"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM collector_uploads_T_v1_8_0").fetchone()[0], 1)
        self.assertTrue((self.root / "COLLECTOR-1" / hx[:2] / hx).is_file())

    def test_bad_hash_does_not_leave_a_partial_file(self):
        with self.assertRaises(ValueError):
            server.store_stream(
                self.con, io.BytesIO(b"wrong"), collector="NODE-1", size=5,
                expected_hash="0" * 64, document_type="other", root=self.root,
            )
        self.assertFalse(list(self.root.rglob(".part-*")))
        self.assertEqual(self.con.execute(
            "SELECT COUNT(*) FROM collector_uploads_T_v1_8_0").fetchone()[0], 0)

    def test_outbox_retry_then_receipt_no_file_delete(self):
        source = self.root / "original.pdf"
        source.write_bytes(b"%PDF-1.7\nproof")
        out = client.UploadQueue(self.root / "db" / "queue.db")
        hx = out.enqueue(source, "appraisal")
        out.enqueue(source, "appraisal")
        self.assertEqual(len(out.pending()), 1)

        def offline(*args):
            raise ConnectionError("test unavailable")
        self.assertEqual(out.flush("https://example.org", "NODE1", "a" * 32, send=offline)[0]["status"],
                         "PENDING")
        self.assertEqual(len(out.pending()), 1)

        def verified(url, name, token, item):
            self.assertEqual(item["content_sha256"], hx)
            self.assertNotIn(token, str(item))
            return {"ok": True, "status": "controller_stored",
                    "sha256": hx, "size": source.stat().st_size}
        self.assertEqual(out.flush("https://example.org", "NODE1", "a" * 32, send=verified)[0]["status"],
                         "CONTROLLER_VERIFIED")
        self.assertEqual(len(out.pending()), 0)
        self.assertTrue(source.exists())

    def test_wrong_ack_is_not_accepted(self):
        f = self.root / "blob.txt"
        f.write_text("abc")
        out = client.UploadQueue(self.root / "out.db")
        out.enqueue(f)
        result = out.flush("https://example.org", "NODE1", "a" * 32,
                           send=lambda *args: {"ok": True, "status": "controller_stored",
                                               "sha256": "a" * 64, "size": 3})
        self.assertEqual(result[0]["status"], "PENDING")
        self.assertEqual(len(out.pending()), 1)

    def test_source_change_never_uploads_old_hash(self):
        f = self.root / "blob.txt"
        f.write_text("abc")
        out = client.UploadQueue(self.root / "out.db")
        out.enqueue(f)
        f.write_text("abcd")
        results = out.flush("https://example.org", "NODE1", "a" * 32,
                            send=lambda *args: self.fail("must not send"))
        self.assertEqual(results[0]["status"], "NEEDS_REVIEW")
        self.assertEqual(len(out.pending()), 0)


if __name__ == "__main__":
    unittest.main()
