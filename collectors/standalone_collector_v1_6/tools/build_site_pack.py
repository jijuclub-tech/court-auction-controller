# -*- coding: utf-8 -*-
"""site_pack 만들기: 기존 collector_kb(snippets·sites·errors·knowhow·rules)와 shots 를 사이트별 암호화 묶음으로 바꾼다.
비밀값(이름·내용 검사)은 빼고, manifest.json·SHA256SUMS.txt·build_report.json 을 만든다.

사용(상자·노트북 모두):
  python tools/build_site_pack.py --kb collector_kb --site www.courtauction.go.kr --shots D:\\BIDGUARD\\court\\shots --out packs\\www.courtauction.go.kr
  옵션: --grade A|B|C|D (기본: 법원경매 A, 그 밖 C) --version 문자열
암호 키: 설정 crypto(기본 시험 키 'test' — KEY-UPGRADE-AT-2026-12-01T00:00+09:00) 를 따른다.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from standalone import config as C          # noqa: E402
from standalone.crypto import key_from_config  # noqa: E402
from standalone.site_pack import build      # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default=os.path.join(HERE, "collector_kb"))
    ap.add_argument("--site", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shots", default=None)
    ap.add_argument("--grade", default=None)
    ap.add_argument("--version", default=None)
    a = ap.parse_args(argv)
    cfg = C.load()
    key = key_from_config(cfg)          # 'dpapi' 면 DATA_KEY 가 필요 → 수집기 keys 로 먼저 입력
    rep = build(a.kb, a.site, a.out, shots_dir=a.shots, grade=a.grade, version=a.version, key=key, cfg=cfg)
    print(json.dumps({k: rep[k] for k in ("site_id", "grade", "files", "used_bytes", "limit_bytes")}, ensure_ascii=False))
    print("excluded_secret=%d removed=%d" % (len(rep["excluded_secret"]), rep["prune"]["removed_count"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
