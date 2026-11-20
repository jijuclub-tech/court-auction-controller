"""Synthetic tests for collector wallet (no live keys, hardware or paid calls)."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import key_wallet as wallet
import collector_key_wallet_T_v0_2 as keys


class CollectorKeysTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.wallet = root / "vault" / "key-wallet.json"
        self.db = root / "controller.db"
        self.handoff = root / "handoff"
        self.password = "TEST-ADMIN-ONLY-12345"
        doc, master = wallet.new_wallet(self.password)
        wallet.write_private(self.wallet, doc, create=True)
        wallet.erase(master)
        with sqlite3.connect(self.db) as con:
            con.execute("CREATE TABLE collectors (name TEXT PRIMARY KEY, type TEXT, status TEXT, "
                        "token_hash TEXT NOT NULL DEFAULT '',first_seen TEXT,last_seen TEXT)")
            keys.init_schema(con)

    def issue(self, name="COL-NODE-01", rotate=False):
        return keys.provision_batch(self.wallet, self.db, [(name, "control")],
                                    self.handoff, self.password, rotate=rotate)

    def test_unique_keys_encrypted_in_wallet_and_single_use(self):
        keys.provision_batch(self.wallet, self.db,
                             [("COL-NODE-01", "control"), ("COL-NODE-02", "supply")],
                             self.handoff, self.password)
        packages = [json.loads((self.handoff / (n + ".pair.json")).read_text())
                    for n in ("COL-NODE-01", "COL-NODE-02")]
        self.assertNotEqual(packages[0]["claim_code"], packages[1]["claim_code"])
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            first, _ = keys.redeem(con, "COL-NODE-01", packages[0]["claim_code"])
            second, _ = keys.redeem(con, "COL-NODE-02", packages[1]["claim_code"])
            self.assertNotEqual(first, second)
            self.assertIsNone(keys.redeem(con, "COL-NODE-01", packages[0]["claim_code"])[0])
            self.assertEqual(keys.token_hash(first),
                             con.execute("SELECT token_hash FROM collectors WHERE name='COL-NODE-01'").fetchone()[0])
        raw = self.wallet.read_text()
        self.assertNotIn(first, raw)
        self.assertNotIn(second, raw)

    def test_wrong_name_wrong_code_and_replay_all_denied(self):
        self.issue()
        p = json.loads((self.handoff / "COL-NODE-01.pair.json").read_text())
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            self.assertIsNone(keys.redeem(con, "COL-NODE-02", p["claim_code"])[0])
            self.assertIsNone(keys.redeem(con, "COL-NODE-01", "bad" + p["claim_code"])[0])
            self.assertTrue(keys.redeem(con, "COL-NODE-01", p["claim_code"])[0])
            self.assertIsNone(keys.redeem(con, "COL-NODE-01", p["claim_code"])[0])

    def test_expiry_and_revocation(self):
        self.issue()
        p = json.loads((self.handoff / "COL-NODE-01.pair.json").read_text())
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            self.assertIsNone(keys.redeem(con, "COL-NODE-01", p["claim_code"],
                                          clock=p["expires_at"] + 1)[0])
        keys.revoke(self.db, "COL-NODE-01")
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            self.assertIsNone(keys.redeem(con, "COL-NODE-01", p["claim_code"])[0])

    def test_repeat_issues_do_not_break_existing_keys(self):
        self.issue()
        rows = self.issue()
        self.assertEqual(rows[0]["result"], "kept-existing")
        self.assertEqual(len(list(self.handoff.glob("*.pair.json"))), 1)

    def test_duplicate_names_and_short_password_rejected(self):
        with self.assertRaises(ValueError):
            keys.provision_batch(self.wallet, self.db,
                                 [("COL-NODE-01", "control"), ("COL-NODE-01", "supply")],
                                 self.handoff, self.password)
        with self.assertRaises(Exception):
            keys.provision_batch(self.wallet, self.db, [("A", "control")],
                                 self.handoff, "incorrect password")


if __name__ == "__main__":
    unittest.main()
