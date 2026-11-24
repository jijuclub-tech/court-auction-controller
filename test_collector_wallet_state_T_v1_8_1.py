"""Wallet status check: no real keys, no device registration, no secrets returned."""
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import collector_wallet_state_T_v1_8_1 as state


class WalletStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.execute(
            "CREATE TABLE collectors(name TEXT, status TEXT, token_hash TEXT)"
        )

    def test_missing_wallet_is_not_claimed_active(self):
        outcome = state.status(self.root, self.conn, env={})
        self.assertFalse(outcome["encrypted_wallet_file_present"])
        self.assertFalse(outcome["wallet_active_confirmed"])
        self.assertFalse(outcome["nas_credential_leasing_active"])
        self.assertFalse(outcome["key_values_returned"])

    def test_encrypted_wallet_file_only_reports_metadata(self):
        wallet = self.root / "key-wallet.json"
        wallet.write_text(json.dumps({
            "format": "bidguard.wallet.v1",
            "wrapped_master": {"n": "synthetic"},
            "kdf": {"name": "scrypt"},
        }), encoding="utf-8")
        if os.name != "nt":
            wallet.chmod(0o600)
        self.conn.execute(
            "INSERT INTO collectors VALUES('NODE_A','approved','fake-verifier')"
        )
        outcome = state.status(self.root, self.conn, env={})
        self.assertTrue(outcome["encrypted_wallet_file_present"])
        self.assertTrue(outcome["wallet_format_recognized"])
        self.assertEqual(outcome["approved_collector_tokens_registered"], 1)
        self.assertNotIn("synthetic", str(outcome))
        self.assertNotIn("fake-verifier", str(outcome))
        self.assertFalse(outcome["wallet_active_confirmed"])


if __name__ == "__main__":
    unittest.main()
