"""Synthetic tests only. Do NOT interpret as live NAS, Drive, or PC proof."""
import tempfile
import unittest
from pathlib import Path

from equipment.storage_failover_T_v0_1 import StorageSpool, safe_relative
from equipment.pc_inventory_T_v0_1 import EquipmentRegistry


class StorageFailoverTests(unittest.TestCase):
    def test_local_first_and_no_false_nas(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            src = root / "original.pdf"
            src.write_bytes(b"synthetic_document")
            queue = StorageSpool(root / "local", root / "false_nas_local_folder")
            target, checksum = queue.stage(src, "case/valuation.pdf")
            self.assertTrue(target.exists())
            self.assertFalse(queue.copy_to_nas("case/valuation.pdf"))
            self.assertTrue(
                queue.upload_to_drive(
                    "case/valuation.pdf",
                    lambda _path, _rel, digest: {
                        "verified": True, "sha256": digest
                    },
                )
            )
            self.assertFalse(
                queue.upload_to_drive(
                    "case/valuation.pdf",
                    lambda _path, _rel, digest: {
                        "verified": False, "sha256": digest
                    },
                )
            )
            self.assertTrue(src.exists())
            self.assertTrue(target.exists())
            self.assertEqual(checksum, queue.stage(src, "case/valuation.pdf")[1])

    def test_path_traversal_rejected(self):
        with self.assertRaises(ValueError):
            safe_relative("../sensitive.txt")


class EquipmentTests(unittest.TestCase):
    def test_registered_and_unknown_state(self):
        with tempfile.TemporaryDirectory() as name:
            db = EquipmentRegistry(Path(name) / "equipment.sqlite")
            db.register("BG-PC-003", "Samsung 3", "collector", "NT300E5Q-LD34")
            self.assertEqual("UNKNOWN", db.list_status()[0]["connection"])
            db.heartbeat("BG-PC-003", {
                "nas_state": "DOWN", "job_state": "LOCAL_ONLY",
                "password": "this_must_be_dropped",
            })
            state = db.list_status()[0]
            self.assertEqual("ONLINE", state["connection"])
            self.assertNotIn("password", state["health"])
            with self.assertRaises(PermissionError):
                db.heartbeat("BG-PC-999", {"job_state": "running"})


if __name__ == "__main__":
    unittest.main()
