# -*- coding: utf-8 -*-
"""저장 (문제 11): NAS 먼저 → 안 되면 D: → (D: 없으면 C:) 에 두고 pending_to_nas.jsonl 에 적어 나중에 NAS 로 옮긴다.
2호: X: 가 안 보이면 설정의 subst 대상으로 한 번 연결해 본다(subst 는 Windows 기본 명령, 노트북 로컬).
옮길 때 SHA-256 이 같아야 원본을 지운다(같지 않으면 둘 다 둠).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _writable(root):
    try:
        os.makedirs(root, exist_ok=True)
        t = os.path.join(root, ".bg_write_test_%d" % os.getpid())
        open(t, "w").write("x")
        os.remove(t)
        return True
    except OSError:
        return False


class Storage:
    def __init__(self, cfg_storage, state_dir, log=lambda *a, **k: None, run=subprocess.run, platform=sys.platform):
        self.c, self.log, self.run, self.platform = cfg_storage, log, run, platform
        self.pending = os.path.join(state_dir, "pending_to_nas.jsonl")
        os.makedirs(state_dir, exist_ok=True)
        self._subst_tried = False

    def nas_ok(self):
        root = self.c["nas_root"]
        if _writable(root):
            return True
        s = self.c.get("nas_subst") or {}
        if self.platform == "win32" and s.get("target") and not self._subst_tried:
            self._subst_tried = True
            try:
                self.run(["subst", s["drive"], s["target"]], check=False, capture_output=True, timeout=10)
                self.log("nas_subst", drive=s["drive"])
            except Exception as e:  # noqa: BLE001
                self.log("nas_subst_fail", err=type(e).__name__)
            return _writable(root)
        return False

    def roots(self):
        return [r for r in (self.c.get("local_root"), self.c.get("fallback_root")) if r]

    def save_bytes(self, rel, data: bytes):
        """rel: 'dongbu/2025타경123_1/감정평가서.pdf'. (저장 경로, 'nas'|'local') 돌려줌."""
        if self.nas_ok():
            p = os.path.join(self.c["nas_root"], rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(data)
            return p, "nas"
        for root in self.roots():
            if _writable(root):
                p = os.path.join(root, rel)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "wb").write(data)
                self._queue(p, rel)
                return p, "local"
        raise OSError("저장할 곳 없음 (NAS·D:·C: 모두 실패)")

    def adopt_file(self, path, rel):
        """크롬이 받은 파일(내려받기 폴더)을 같은 규칙으로 옮긴다."""
        data = open(path, "rb").read()
        dst, where = self.save_bytes(rel, data)
        if sha256(dst) == sha256(path) and os.path.abspath(dst) != os.path.abspath(path):
            os.remove(path)
        return dst, where

    def _queue(self, path, rel):
        with open(self.pending, "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": path, "rel": rel, "sha256": sha256(path), "t": int(time.time())},
                               ensure_ascii=False) + "\n")

    def flush_to_nas(self):
        """회차 끝·시작 때 부름. 옮긴 개수."""
        if not os.path.exists(self.pending) or not self.nas_ok():
            return 0
        left, moved = [], 0
        for ln in open(self.pending, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            src = r["path"]
            if not os.path.exists(src):
                continue
            dst = os.path.join(self.c["nas_root"], r["rel"])
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                if sha256(dst) == r["sha256"]:
                    os.remove(src)
                    moved += 1
                else:
                    left.append(r)
            except OSError:
                left.append(r)
        with open(self.pending + ".tmp", "w", encoding="utf-8") as f:
            for r in left:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(self.pending + ".tmp", self.pending)
        self.log("nas_flush", moved=moved, left=len(left))
        return moved
