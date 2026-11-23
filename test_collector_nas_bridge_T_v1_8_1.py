"""Offline-only tests: controller -> NAS verified mirroring and LAN/WAN hints.

No NAS, network, Pico2, key wallet, or live collector is accessed.
"""
import hashlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

import collector_file_ingest_T_v1_8_0 as intake
import collector_nas_bridge_T_v1_8_1 as mirror
import collector_upload_address_T_v1_8_1 as addresses


class NasMirrorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.stage, self.nas = base / "local-ssd", base / "fake-nas"
        self.stage.mkdir()
        self.nas.mkdir()
        self.con = sqlite3.connect(":memory:")
        intake.init(self.con)
        self.addCleanup(self.con.close)
        self.blob = b"%%PDF-1.7\n" + b"synthetic-not-real-data"
        self.digest = hashlib.sha256(self.blob).hexdigest()
        intake.store_stream(self.con, io.BytesIO(self.blob), collector="NODE-A",
                            size=len(self.blob), expected_hash=self.digest,
                            document_type="appraisal", root=self.stage)
        self.con.commit()
        self.source = self.stage / "NODE-A" / self.digest[:2] / self.digest

    def _backup(self, check):
        return mirror.mirror_pending(self.con, staging_root=self.stage,
                                     nas_mount=self.nas, mount_checker=check)

    def test_mount_missing_keeps_original_and_pending(self):
        result = self._backup(lambda path: False)
        self.assertFalse(result["nas_online"])
        self.assertTrue(self.source.is_file())
        self.assertEqual(self.con.execute(
            "SELECT backup_status FROM collector_uploads_T_v1_8_0").fetchone()[0],
            "PENDING_BACKUP")
        self.assertEqual(list(self.nas.rglob("*")), [])

    def test_online_mirror_verifies_nas_and_keeps_original(self):
        result = self._backup(lambda path: path == str(self.nas))
        self.assertEqual(result["copied"], 1)
        self.assertEqual(result["errors"], 0)
        target = self.nas / "NODE-A" / self.digest[:2] / self.digest
        self.assertEqual(target.read_bytes(), self.blob)
        self.assertEqual(mirror.hash_file(target), self.digest)
        self.assertEqual(mirror.hash_file(self.source), self.digest)
        self.assertEqual(self.con.execute(
            "SELECT backup_status FROM collector_uploads_T_v1_8_0").fetchone()[0],
            "NAS_VERIFIED")
        again = self._backup(lambda path: path == str(self.nas))
        self.assertEqual(again["copied"], 0)
        self.assertEqual(again["pending"], 0)

    def test_nas_conflict_does_not_overwrite_original(self):
        target = self.nas / "NODE-A" / self.digest[:2] / self.digest
        target.parent.mkdir(parents=True)
        target.write_bytes(b"wrong")
        result = self._backup(lambda path: path == str(self.nas))
        self.assertEqual(result["copied"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(target.read_bytes(), b"wrong")
        self.assertEqual(self.source.read_bytes(), self.blob)
        self.assertEqual(self.con.execute(
            "SELECT backup_status FROM collector_uploads_T_v1_8_0").fetchone()[0],
            "PENDING_BACKUP")

    def test_db_path_tampering_never_reads_arbitrary_file(self):
        self.con.execute("UPDATE collector_uploads_T_v1_8_0 SET stored_path='../../etc/passwd'")
        self.con.commit()
        result = self._backup(lambda path: path == str(self.nas))
        self.assertEqual(result["copied"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertFalse((self.nas / "NODE-A").exists())

    def test_missing_source_does_not_mark_verified(self):
        self.source.unlink()
        result = self._backup(lambda path: path == str(self.nas))
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["pending"], 1)

    def test_nas_path_is_never_created_if_missing(self):
        missing = Path(self.tmp.name) / "nas-offline"
        result = mirror.mirror_pending(self.con, staging_root=self.stage,
                                       nas_mount=missing, mount_checker=lambda _: False)
        self.assertFalse(result["nas_online"])
        self.assertFalse(missing.exists())


class UploadAddressTests(unittest.TestCase):
    def test_two_configured_addresses_share_single_upload_path(self):
        values = {
            "BIDGUARD_COLLECTOR_UPLOAD_LAN_URL": "https://controller.lan.example.test",
            "BIDGUARD_COLLECTOR_UPLOAD_WAN_URL": "https://controller.example.test",
        }
        result = addresses.attach_upload_policy({"existing": 123}, values)
        self.assertEqual(result["existing"], 123)
        self.assertEqual(len(result["collector_upload"]["endpoints"]), 2)
        self.assertEqual(result["collector_upload"]["endpoints"][0]["network"], "lan")
        self.assertTrue(all(x["url"].endswith("/api/v1/collector/upload")
                            for x in result["collector_upload"]["endpoints"]))
        self.assertEqual(result["collector_upload"]["receipt"], "controller_stored_only")

    def test_no_config_is_disabled_not_invented(self):
        result = addresses.attach_upload_policy({}, {})
        self.assertFalse(result["collector_upload"]["enabled"])
        self.assertEqual(result["collector_upload"]["endpoints"], [])

    def test_insecure_or_userinfo_url_rejected(self):
        for raw in ["http://controller.example.test",
                    "https://user:password@controller.example.test",
                    "https://controller.example.test/other",
                    "https://127.0.0.1",
                    "https://controller.example.test/?token=bad"]:
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    addresses.verify_upload_url(raw)


if __name__ == "__main__":
    unittest.main()
