"""Synthetic checks only; real NAS/Google Drive/phones/PCs require field tests.

Run: python3 -m unittest discover -s equipment/tests -p 'test_equipment_monitor_T_v0_3.py'
"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from equipment import equipment_monitor_T_v0_3 as mon


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        mon.init(Path(self.temp.name) / "monitor.sqlite")
        self.env = patch.dict(os.environ, {"NOTIFY_URL": ""}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_registered_only_and_metrics_are_filtered(self):
        secret = mon.register("BG-PC-003", "three", "collector")
        self.assertFalse(mon.heartbeat("BG-PC-003", "wrong", {}))
        self.assertTrue(mon.heartbeat("BG-PC-003", secret, {
            "job_state":"RUNNING", "battery_percent":75,
            "password":"do_not_store", "ssh_key":"do_not_store"}))
        node = next(n for n in mon.snapshot()["nodes"] if n["node_id"]=="BG-PC-003")
        self.assertEqual(node["status"], "ONLINE")
        self.assertEqual(node["metrics"]["job_state"], "RUNNING")
        self.assertNotIn("password", node["metrics"])
        self.assertNotIn("token_sha", node)
        with self.assertRaises(ValueError):
            mon.register("BG-PC-003", "duplicate", "pc")

    def test_resource_alarm_and_recovery(self):
        secret = mon.register("BG-PC-004", "game", "pc")
        mon.heartbeat("BG-PC-004", secret, {"disk_free_bytes":100})
        self.assertEqual(next(x for x in mon.snapshot()["nodes"]
                              if x["node_id"]=="BG-PC-004")["status"], "DEGRADED")
        mon.heartbeat("BG-PC-004", secret, {"disk_free_bytes":20*1024**3})
        events = [x for x in mon.snapshot()["events"] if x["node_id"]=="BG-PC-004"]
        self.assertEqual([x["severity"] for x in events], ["recovered", "warning"])
        mon.acknowledge(events[1]["id"])
        self.assertTrue(any(x["acknowledged_at"] for x in mon.snapshot()["events"]
                            if x["id"]==events[1]["id"]))

    def test_nas_lying_local_folder_rejected(self):
        self.assertFalse(mon._remote_mount_ok(Path(self.temp.name)))

    def test_first_configured_failure_alerts(self):
        target_file = Path(self.temp.name) / "targets.json"
        target_file.write_text('[{"node_id":"BG-ST-001","host":"127.0.0.1",'
                               '"port":0,"kind":"nas"}]',encoding="utf-8")
        with patch.dict(os.environ, {"CONTROLLER_MONITOR_TARGETS_FILE":str(target_file)}):
            mon.check_once()
            mon.check_once()
        node = next(n for n in mon.snapshot()["nodes"] if n["node_id"]=="BG-ST-001")
        self.assertEqual(node["status"], "OFFLINE")
        self.assertTrue(any(e["node_id"]=="BG-ST-001" and e["severity"]=="critical"
                            for e in mon.snapshot()["events"]))

    def test_unconfigured_devices_never_fake_online(self):
        nodes = {x["node_id"]:x for x in mon.snapshot()["nodes"]}
        self.assertEqual(nodes["BG-ST-001"]["status"], "UNKNOWN")
        self.assertIsNone(nodes["BG-ST-001"]["last_seen"])


if __name__ == "__main__":
    unittest.main()
