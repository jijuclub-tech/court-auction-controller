"""Synthetic, offline-only wallet and log encryption tests — never real secrets."""
import json
import os
from pathlib import Path
import tempfile
import unittest

import key_wallet as w


class WalletTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'keys' / 'key-wallet.json'
        self.admin = 'test-admin-passphrase-very-long'
        self.staff = 'test-staff-passphrase-very-long'
        self.vault, master = w.new_wallet(self.admin)
        w.erase(master)
        w.write_private(self.path, self.vault, create=True)

    def test_master_random_256bit_and_encrypted_on_disk(self):
        second, key2 = w.new_wallet(self.admin)
        key1 = w.unlock(self.vault, self.admin)
        try:
            self.assertEqual(len(key1), 32)
            self.assertNotEqual(key1, key2)
            self.assertNotIn(bytes(key1).hex().encode(), self.path.read_bytes())
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
            self.assertNotEqual(self.vault['master_fingerprint'], second['master_fingerprint'])
        finally:
            w.erase(key1)
            w.erase(key2)

    def test_password_must_authenticate(self):
        with self.assertRaises(Exception):
            w.unlock(self.vault, 'wrong-password')

    def test_each_log_separate_session_and_dual_wrapping(self):
        first = w.create_log(self.vault, self.admin, 'worker01', self.staff, b'synthetic log 1')
        second = w.create_log(self.vault, self.admin, 'worker01', self.staff, b'synthetic log 1')
        self.assertNotEqual(first['payload'], second['payload'])
        self.assertNotEqual(first['master_wrap'], second['master_wrap'])
        self.assertEqual(w.decrypt_log(first, self.admin, self.vault), b'synthetic log 1')
        self.assertEqual(w.decrypt_log(first, self.staff), b'synthetic log 1')
        self.assertNotIn('staff_password', json.dumps(first))
        with self.assertRaises(Exception):
            w.decrypt_log(first, 'wrong-password')

    def test_tampering_rejected(self):
        doc = w.create_log(self.vault, self.admin, 'worker01', self.staff, b'protected')
        doc['staff_id'] = 'worker02'
        with self.assertRaises(Exception):
            w.decrypt_log(doc, self.admin, self.vault)

    def test_encrypted_backup_and_recovery(self):
        destination = Path(self.tmp.name) / 'second' / 'key-wallet.json'
        sha = w.copy_encrypted(self.path, destination)
        self.assertEqual(sha, w.hashlib.sha256(destination.read_bytes()).hexdigest())
        self.assertEqual(self.path.read_bytes(), destination.read_bytes())
        key = w.unlock(self.vault, self.admin)
        try:
            repaired = w.replace_admin_password(self.vault, key, 'different-long-administrator-password')
        finally:
            w.erase(key)
        with self.assertRaises(Exception):
            w.unlock(repaired, self.admin)
        recovered_key = w.unlock(repaired, 'different-long-administrator-password')
        self.assertEqual(w.fingerprint(recovered_key), self.vault['master_fingerprint'])
        w.erase(recovered_key)

    def test_no_replace_of_existing_wallet(self):
        with self.assertRaises(FileExistsError):
            w.write_private(self.path, self.vault, create=True)


if __name__ == '__main__':
    unittest.main()