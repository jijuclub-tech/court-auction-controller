# -*- coding: utf-8 -*-
"""모든 오프라인 시험 실행: python tests/run_all.py  (사이트·피코2 접속 없음)"""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
suite = unittest.defaultTestLoader.discover(HERE, pattern="test_standalone.py")
r = unittest.TextTestRunner(verbosity=2).run(suite)
leg = subprocess.run([sys.executable, os.path.join(HERE, "test_legacy_page_guard.py")], capture_output=True, text=True)
last = leg.stdout.strip().splitlines()[-1] if leg.stdout.strip() else leg.stderr[-300:]
print("legacy page_guard:", last)
ok = r.wasSuccessful() and leg.returncode == 0
print("ALL PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
