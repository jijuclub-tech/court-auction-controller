# -*- coding: utf-8 -*-
"""시험 키('test') 사용 자리 검사 — 2026-12-01 00:00 KST(11/30→12/1 자정) 키 갱신 준비.

규칙: 시험 키 문자열 'test' 를 자료 키로 쓰는 자리마다 같은 줄(또는 바로 위·아래 2줄 안)에
      표시 KEY-UPGRADE-AT-2026-12-01T00:00+09:00 가 있어야 한다. 옛 표기 KEY-UPGRADE-BEFORE 는 위반.
잡는 자리: .py 에서 'test' 문자열이 key·KEY·crypto·passphrase·DataKey 와 같은 줄에 있거나 TEST_KEY 가 나오는 줄,
          .json 에서 "key": "test", .md/.txt/.bat/.ps1 에서 key=test 꼴.
돌려줌: 0 = 표시된 자리 1개 이상 + 표시 없는 자리 0개, 1 = 위반, 2 = 갱신 시각이 지났는데 시험 키 자리가 남음(--now 로 시험).
사용: python tools/key_upgrade_audit.py [폴더] [--include-tests] [--now 2026-12-01T00:00:01+09:00]
"""
import argparse
import datetime as dt
import os
import re
import sys

MARK = "KEY-UPGRADE-AT-2026-12-01T00:00+09:00"
OLD_MARK = "KEY-UPGRADE-BEFORE"
UPGRADE_AT = dt.datetime(2026, 12, 1, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=9)))
PY_HIT = re.compile(r"""(['"])test\1""")
PY_CTX = re.compile(r"(key|KEY|crypto|passphrase|DataKey|derive)")
JSON_HIT = re.compile(r'"key"\s*:\s*"test"')
TXT_HIT = re.compile(r"(?i)\bkey\s*[=:]\s*['\"]?test\b")
EXTS = (".py", ".json", ".md", ".txt", ".bat", ".ps1", ".cmd")


def scan(root, include_tests=False):
    marked, unmarked, old = [], [], []
    me = os.path.abspath(__file__)
    for d, ds, fs in os.walk(root):
        ds[:] = [x for x in ds if x not in ("__pycache__", ".git") and (include_tests or x != "tests")]
        for fn in sorted(fs):
            fp = os.path.join(d, fn)
            if not fn.endswith(EXTS) or os.path.abspath(fp) == me or fn == "SHA256SUMS.txt":
                continue
            try:
                lines = open(fp, encoding="utf-8").read().splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            for i, ln in enumerate(lines):
                if OLD_MARK in ln:
                    old.append((rel, i + 1, ln.strip()[:120]))
                if fn.endswith(".py"):
                    hit = (PY_HIT.search(ln) and PY_CTX.search(ln)) or re.search(r"\bTEST_KEY\b", ln)
                elif fn.endswith(".json"):
                    hit = JSON_HIT.search(ln)
                else:
                    hit = TXT_HIT.search(ln)
                if not hit:
                    continue
                near = lines[max(0, i - 2): i + 3]
                (marked if any(MARK in x for x in near) else unmarked).append((rel, i + 1, ln.strip()[:120]))
    return marked, unmarked, old


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--now", default=None)
    a = ap.parse_args(argv)
    marked, unmarked, old = scan(a.root, a.include_tests)
    for r in marked:
        print("MARKED   %s:%d  %s" % r)
    for r in unmarked:
        print("UNMARKED %s:%d  %s" % r)
    for r in old:
        print("OLD_MARK %s:%d  %s" % r)
    print("marked=%d unmarked=%d old_mark=%d" % (len(marked), len(unmarked), len(old)))
    if unmarked or old or not marked:
        return 1
    now = dt.datetime.fromisoformat(a.now) if a.now else dt.datetime.now(dt.timezone.utc)
    if now >= UPGRADE_AT:
        print("갱신 시각(%s)이 지났는데 시험 키 자리가 %d곳 남아 있어요." % (UPGRADE_AT.isoformat(), len(marked)))
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
