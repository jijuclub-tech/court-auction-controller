# -*- coding: utf-8 -*-
"""로컬 관제기 + 수집기 v1.6 연결 시험(상자, loopback 만). 실행: python tests/test_local_control.py"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COL = os.path.join(os.path.dirname(HERE), "standalone_collector_v1_6")
sys.path.insert(0, HERE)
sys.path.insert(0, COL)
sys.path.insert(0, os.path.join(COL, "tests"))

import local_control_server as LCS          # noqa: E402
from helpers import tmp_cfg, FakeSecrets     # noqa: E402
from standalone.kb_sync import KBSync        # noqa: E402
from standalone.app import prepare_online    # noqa: E402
from standalone import site_pack as SP       # noqa: E402
from PIL import Image                        # noqa: E402

U, P = "ctl-user", "ctl-pass-1"


class T(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lcs_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        kb = os.path.join(self.tmp, "kb")
        os.makedirs(os.path.join(kb, "snippets", "www.courtauction.go.kr"))
        Image.new("RGB", (120, 28), (20, 90, 200)).save(os.path.join(kb, "snippets", "www.courtauction.go.kr", "b.png"))
        sh = os.path.join(self.tmp, "shots")
        os.makedirs(sh)
        Image.effect_noise((1280, 720), 60).convert("RGB").save(os.path.join(sh, "home.png"))
        self.packs = os.path.join(self.tmp, "packs")
        SP.build(kb, "www.courtauction.go.kr", os.path.join(self.packs, "www.courtauction.go.kr"), shots_dir=sh, version="v1")
        self.srv, self.st = LCS.serve(self.packs, U, P, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.shutdown)
        self.url = "http://127.0.0.1:%d" % self.srv.server_address[1]

    def kb(self, pw=P):
        cfg, _d = tmp_cfg()
        cs = {"url": self.url, "timeout_sec": 3, "allow_http": True, "host": "127.0.0.1", "verify_tls": True}
        cfg["kb_server"] = cs
        return KBSync(cfg["kb_dir"], cs, FakeSecrets({"KB_SERVER_USER": U, "KB_SERVER_PASSWORD": pw}), "1.6", "pc"), cfg

    def test_collector_loads_from_local_control(self):
        k, cfg = self.kb()
        got = prepare_online("court_x", {"site": "www.courtauction.go.kr"}, cfg, k, lambda *a, **kw: None, lambda *a, **kw: None)
        self.assertIsNotNone(got)
        client, pack = got
        self.assertEqual(pack.grade, "A")
        self.assertTrue(any(m.get("kind") == "gallery" for m in pack.meta.values()))
        pack.put("snippets/www.courtauction.go.kr/new.png", b"\x89PNG-new", kind="snippet")
        rep = client.finish(pack)
        d = os.path.join(self.packs, "www.courtauction.go.kr")
        self.assertTrue(os.path.exists(os.path.join(d, "reports.jsonl")))
        inc = os.path.join(d, "incoming", "snippets", "www.courtauction.go.kr", "new.png")
        self.assertTrue(open(inc, "rb").read().startswith(b"BGX1"))      # 올린 자료는 암호문
        self.assertEqual(rep["site_id"], "www.courtauction.go.kr")

    def test_wrong_password_and_missing_pack_stop(self):
        k, cfg = self.kb(pw="wrong")
        self.assertIsNone(prepare_online("court_x", {"site": "www.courtauction.go.kr"}, cfg, k, lambda *a, **kw: None, lambda *a, **kw: None))
        k2, cfg2 = self.kb()
        self.assertIsNone(prepare_online("map_x", {"site": "no.such.site"}, cfg2, k2, lambda *a, **kw: None, lambda *a, **kw: None))
        self.assertIn("server_unreachable", open(os.path.join(cfg2["stop_dir"], "STOP_map_x"), encoding="utf-8").read())

    def test_account_lock(self):
        k, cfg = self.kb()
        self.assertTrue(k.account_lock("tag1", "a"))
        self.assertFalse(k.account_lock("tag1", "b"))
        k.account_unlock("tag1", "a")
        self.assertTrue(k.account_lock("tag1", "b"))


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=2).result
    print("RESULT %d run, %d fail" % (r.testsRun, len(r.failures) + len(r.errors)))
    sys.exit(0 if r.wasSuccessful() else 1)
