"""Mock-only supervised lock tests; MUST NOT run pnputil on a real computer."""
import json
import tempfile
import unittest
from pathlib import Path

import collector_safe_windows_T_v1_8_0 as windows


class DeviceRecorder:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def disable(self, device):
        self.calls.append(("disable", device))
        if self.fail_on == device:
            raise RuntimeError("mock device disable failed")

    def enable(self, device):
        self.calls.append(("enable", device))


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = Path(self.tmp.name) / "lock.json"
        self.devices = ["HID\\VID_1234&PID_0001", "HID\\VID_1235&PID_0002"]
        self.params = dict(
            physical_device_ids=self.devices,
            pico_device_ids=["HID\\VID_2E8A&PID_0005"],
            journal_path=self.journal,
            watchdog_armed=True,
            unlock_channel_tested=True,
            remote_session_tested=True,
            require_admin=False,
            clock=lambda: 100,
        )

    def test_normal_exit_restores_every_device(self):
        backend = DeviceRecorder()
        with windows.supervised_input_lock(**self.params, backend=backend):
            self.assertTrue(self.journal.is_file())
        self.assertEqual(backend.calls[:2], [("disable", d) for d in self.devices])
        self.assertEqual(backend.calls[2:], [("enable", d) for d in self.devices])
        self.assertFalse(self.journal.exists())

    def test_failing_second_disable_restores_all(self):
        backend = DeviceRecorder(fail_on=self.devices[1])
        with self.assertRaisesRegex(RuntimeError, "mock device"):
            with windows.supervised_input_lock(**self.params, backend=backend):
                self.fail("must not reach collection")
        self.assertEqual(backend.calls[2:], [("enable", d) for d in self.devices])
        self.assertFalse(self.journal.exists())

    def test_missing_watchdog_does_not_touch_devices(self):
        backend = DeviceRecorder()
        with self.assertRaisesRegex(RuntimeError, "RECOVERY_WATCHDOG_REQUIRED"):
            with windows.supervised_input_lock(
                **{**self.params, "watchdog_armed": False}, backend=backend
            ):
                pass
        self.assertEqual(backend.calls, [])
        self.assertFalse(self.journal.exists())

    def test_remote_unlock_and_timer(self):
        backend = DeviceRecorder()
        windows.write_journal(self.journal, self.devices, expiry=100)
        self.assertTrue(windows.watchdog_check(self.journal, backend, clock=lambda: 99)["armed"])
        self.assertTrue(windows.watchdog_check(self.journal, backend, clock=lambda: 100)["ok"])
        self.assertEqual(backend.calls, [("enable", d) for d in self.devices])
        self.assertFalse(self.journal.exists())
        self.assertTrue(windows.recover(self.journal, backend)["already_unlocked"])

    def test_pico_cannot_be_disabled(self):
        backend = DeviceRecorder(run=lambda *a, **k: self.fail("pnputil must not run"))
        with self.assertRaises(ValueError):
            backend.disable("HID\\VID_2E8A&PID_0005")

    def test_debug_port_is_safely_parsed(self):
        profile = Path(self.tmp.name)
        self.assertIsNone(windows.chrome_debug_port(profile))
        (profile / "DevToolsActivePort").write_text("12345\n/devtools/browser/abc")
        self.assertEqual(windows.chrome_debug_port(profile), 12345)
        (profile / "DevToolsActivePort").write_text("not-a-port")
        self.assertIsNone(windows.chrome_debug_port(profile))


if __name__ == "__main__":
    unittest.main()
