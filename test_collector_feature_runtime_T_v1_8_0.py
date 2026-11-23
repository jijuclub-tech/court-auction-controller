"""#50 runtime hook tests using a fake legacy collector; no OS input or network."""
import tempfile
import unittest
from pathlib import Path

from collector_feature_runtime_T_v1_8_0 import CollectorFeatureRuntime


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config = {
            "upload_queue_db": str(self.dir / "queue.db"),
            "collector_name": "NODE1",
            "approved_controller_url": "https://controller.example",
            "remote_guard_pause_flag": str(self.dir / "pause.flag"),
            "lock_recovery_journal": str(self.dir / "lock.json"),
        }

    def test_original_no_flags_preserves_arguments_and_does_not_create_outbox(self):
        seen = []
        runtime = CollectorFeatureRuntime(
            argv=["--mode", "ordinary"], config=self.config,
            token_supplier=lambda *_: self.fail("no wallet calls"),
        )
        result = runtime.run(lambda args, hooks: seen.append((args, hooks.options)) or 7)
        self.assertEqual(result, 7)
        self.assertEqual(seen[0][0], ["--mode", "ordinary"])
        self.assertFalse((self.dir / "queue.db").exists())

    def test_remote_guard_pauses_only_pico_actions(self):
        runtime = CollectorFeatureRuntime(argv=[], config=self.config, token_supplier=lambda *_: None)
        self.assertTrue(runtime.pico_input_permitted())
        (self.dir / "pause.flag").write_text("pause")
        self.assertFalse(runtime.pico_input_permitted())
        (self.dir / "pause.flag").unlink()
        self.assertTrue(runtime.pico_input_permitted())

    def test_upload_hook_only_accepts_verified_files_when_enabled(self):
        f = self.dir / "document.pdf"
        f.write_bytes(b"%PDF-1.7\nresult")
        runtime = CollectorFeatureRuntime(
            argv=["/upload"], config=self.config,
            token_supplier=lambda *_: "x" * 32,
        )
        hx = runtime.on_verified_document(f, "appraisal")
        self.assertEqual(len(hx), 64)
        self.assertEqual(len(runtime.queue.pending()), 1)

    def test_unlock_does_not_launch_legacy(self):
        runtime = CollectorFeatureRuntime(
            argv=["/unlock"], config=self.config,
            token_supplier=lambda *_: self.fail("no key needed"),
        )
        self.assertTrue(runtime.run(lambda *_: self.fail("legacy must not launch"))["ok"])


if __name__ == "__main__":
    unittest.main()
