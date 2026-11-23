"""Issue #50: additive v1.8.0 hooks for the existing v1.7_4p collector.

The base collector must call these hooks from its actual saved-document and
HID action paths. This file does not replace or silently monkeypatch the base.
"""
from __future__ import annotations

import contextlib
import threading
from pathlib import Path

from collector_launch_options_T_v1_8_0 import UploadQueue, split_options
from collector_safe_windows_T_v1_8_0 import (
    DedicatedChromeKiosk, recover, supervised_input_lock,
)


class CollectorFeatureRuntime:
    def __init__(self, *, argv, base_args=None, config, token_supplier,
                 kiosk_factory=DedicatedChromeKiosk, upload_queue_factory=UploadQueue):
        self.options, self.base_args = split_options(argv)
        self.config = dict(config or {})
        self.token_supplier = token_supplier
        self.kiosk_factory = kiosk_factory
        self.queue = upload_queue_factory(self.config["upload_queue_db"]) if self.options.upload else None
        self.kiosk = None
        self.stop = threading.Event()
        self.monitor = None
        self.upload_monitor = None
        self.upload_failure_count = 0

    def on_verified_document(self, file_path, document_type="other"):
        """Call ONLY after the base collector verified and committed the source file."""
        if self.queue is not None:
            return self.queue.enqueue(file_path, document_type)
        return None

    def retry_online(self):
        """Registered controller HTTPS URL only; wallet secret stays in memory."""
        if self.queue is None:
            return []
        node = self.config.get("collector_name", "")
        token = self.token_supplier(node, "controller_upload")
        if not token:
            raise RuntimeError("approved collector upload token unavailable")
        try:
            return self.queue.flush(
                self.config.get("approved_controller_url", ""), node, token, limit=5
            )
        finally:
            del token

    def pico_input_permitted(self):
        """remote_guard pause.flag is a PAUSE, never a reason to discard a job."""
        marker = self.config.get("remote_guard_pause_flag")
        return not marker or not Path(marker).is_file()

    def unlock_from_remote(self):
        return recover(self.config["lock_recovery_journal"])

    def _start_kiosk(self):
        if not self.options.kiosk:
            return
        self.kiosk = self.kiosk_factory(
            self.config["chrome_exe"], self.config["chrome_profile"],
            self.config["allowed_sites"],
        )
        self.kiosk.start(self.config["start_url"])
        self.stop.clear()

        def supervise():
            while not self.stop.wait(2):
                result = self.kiosk.check_once()
                if not result.get("ok"):
                    # Kiosk can no longer assert only approved tabs; report to
                    # host and stop this dedicated browser, not other Chrome.
                    self.stop.set()
                    self.kiosk.stop()
                    break

        self.monitor = threading.Thread(target=supervise, name="bidguard-kiosk", daemon=True)
        self.monitor.start()

    def _start_upload_retry(self):
        if self.queue is None:
            return

        def retry_loop():
            while not self.stop.is_set():
                try:
                    self.retry_online()
                except Exception:
                    # No token/URL/path in logs; keep original file + pending row.
                    self.upload_failure_count += 1
                if self.stop.wait(60):
                    break

        self.upload_monitor = threading.Thread(
            target=retry_loop, name="bidguard-upload", daemon=True
        )
        self.upload_monitor.start()

    def run(self, legacy_main):
        """legacy_main(original_arguments, hooks) runs the ORIGINAL collector."""
        if self.options.unlock:
            return self.unlock_from_remote()
        if not callable(legacy_main):
            raise TypeError("real v1.7_4p entrypoint is required")
        guard = contextlib.nullcontext()
        if self.options.lock:
            guard = supervised_input_lock(
                physical_device_ids=self.config["approved_physical_hids"],
                pico_device_ids=self.config["detected_pico2_hids"],
                journal_path=self.config["lock_recovery_journal"],
                watchdog_armed=self.config.get("independent_watchdog_verified") is True,
                unlock_channel_tested=self.config.get("remote_unlock_verified") is True,
                remote_session_tested=self.config.get("rdp_access_verified") is True,
            )
        try:
            with guard:
                self._start_kiosk()
                self._start_upload_retry()
                # No flags: invoke legacy_main with unchanged arguments and no
                # side effects from upload, kiosk or HID lock.
                return legacy_main(self.base_args, self)
        finally:
            self.stop.set()
            if self.monitor is not None:
                self.monitor.join(timeout=3)
            if self.upload_monitor is not None:
                self.upload_monitor.join(timeout=3)
            if self.kiosk is not None:
                self.kiosk.stop()
