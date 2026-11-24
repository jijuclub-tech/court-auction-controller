"""No NAS access: mock-only #50 account lease regression."""
import unittest

import collector_account_lease_T_v1_8_0 as nas


class Connector:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def connect(self, root, username, password):
        self.calls.append(("connect", root, username))
        if self.fail:
            raise OSError("synthetic")
    def disconnect(self, root):
        self.calls.append(("disconnect", root))


class Tests(unittest.TestCase):
    def test_separate_collector_folder_and_password_zeroing(self):
        lease = nas.SecretLease(username="collector_A", password=bytearray(b"test-only"), lease_id="fake-1")
        adapter = Connector()
        events = []
        with nas.scoped_nas_session(
            "COLLECTOR_A", r"\\nas\collectors",
            lambda node, purpose: lease, connector=adapter,
            audit=lambda event, **kw: events.append((event, kw))
        ) as path:
            self.assertEqual(path, r"\\nas\collectors\COLLECTOR_A")
            self.assertEqual(adapter.calls[0], ("connect", r"\\nas\collectors", "collector_A"))
        self.assertEqual(adapter.calls[1], ("disconnect", r"\\nas\collectors"))
        self.assertEqual(lease.password, bytearray(b"\0" * 9))
        self.assertEqual([e[0] for e in events], ["nas_lease_open", "nas_lease_closed"])
        self.assertNotIn("test-only", str(events))

    def test_rejected_share_and_collector(self):
        for bad in ["Z:\\", "https://nas", r"\\nas\share\other"]:
            with self.assertRaises(ValueError):
                nas.collector_unc(bad, "NODE1")
        with self.assertRaises(ValueError):
            nas.collector_unc(r"\\nas\share", "../escape")

    def test_failed_connection_still_wipes_secret(self):
        lease = nas.SecretLease("collector_B", bytearray(b"fake-key"))
        adapter = Connector(fail=True)
        with self.assertRaises(OSError):
            with nas.scoped_nas_session(
                "NODE_B", r"\\nas\share", lambda *_: lease, connector=adapter
            ):
                self.fail("must not yield")
        self.assertEqual(lease.password, bytearray(b"\0" * 8))
        self.assertEqual(len(adapter.calls), 1)


if __name__ == "__main__":
    unittest.main()
