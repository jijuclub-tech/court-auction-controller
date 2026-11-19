# -*- coding: utf-8 -*-
"""같은 법원 계정을 두 PC가 동시에 쓰지 않게 (검수 H5).

세 겹:
1) 로컬 잠금: 이 PC가 쓰는 동안 state 에 잠금 파일. 하트비트가 끊기면 저절로 풀림.
2) 서버 잠금: DB 서버가 있으면 서버에 계정 잠금을 요청(SERVER_API_KO: /account/lock). 서버가 답하면 그게 우선.
3) 시간대: 서버도 못 쓰면 설정의 court_account_window(이 수집기가 계정을 써도 되는 시간대)만 허용.
계정 식별은 평문으로 두지 않으려고 아이디의 SHA-256 앞 12자리만 쓴다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time


def account_tag(uid_secret):
    v = uid_secret.reveal() if hasattr(uid_secret, "reveal") else str(uid_secret or "")
    return hashlib.sha256(("court:" + v).encode("utf-8")).hexdigest()[:12] if v else "none"


def in_window(windows, now=time.time):
    """windows: [["00:00","11:00"], ...]. 비면 항상 허용(True). now 기준 KST."""
    if not windows:
        return True
    t = dt.datetime.fromtimestamp(now(), dt.timezone(dt.timedelta(hours=9)))
    cur = t.hour * 60 + t.minute
    for w in windows:
        try:
            a = int(w[0][:2]) * 60 + int(w[0][3:5])
            b = int(w[1][:2]) * 60 + int(w[1][3:5])
        except Exception:  # noqa: BLE001
            continue
        if a <= b:
            if a <= cur < b:
                return True
        elif cur >= a or cur < b:    # 자정 넘김
            return True
    return False


class AccountLock:
    def __init__(self, state_dir, collector_id, tag, windows=None, lock_min=180, kb=None,
                 now=time.time, heartbeat_stale_sec=900):
        self.path = os.path.join(state_dir, "court_account_%s.lock" % tag)
        self.cid, self.tag, self.windows = collector_id, tag, windows or []
        self.lock_min, self.kb, self.now, self.stale = lock_min, kb, now, heartbeat_stale_sec
        self.held = False
        self.server = False

    def _read(self):
        try:
            return json.load(open(self.path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def acquire(self):
        """(True, '') 또는 (False, 이유). 순서: 서버 잠금 → 로컬 잠금 → 시간대."""
        if self.kb is not None:
            r = self._server_lock()
            if r is not None:
                if r:
                    self.server = self.held = True
                    return True, "server_lock"
                return False, "server_lock_held_elsewhere"
        cur = self._read()
        if cur and cur.get("collector_id") != self.cid:
            if self.now() - float(cur.get("heartbeat", 0)) < self.stale:
                return False, "local_lock_held:" + str(cur.get("collector_id"))
        if not in_window(self.windows, self.now):
            return False, "outside_account_window"
        self._write()
        self.held = True
        return True, "local_lock" if self.windows else "local_lock_no_window"

    def _write(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        json.dump({"collector_id": self.cid, "tag": self.tag, "heartbeat": self.now(),
                   "since": dt.datetime.fromtimestamp(self.now()).strftime("%Y-%m-%d %H:%M:%S")},
                  open(tmp, "w", encoding="utf-8"))
        os.replace(tmp, self.path)

    def heartbeat(self):
        if self.held and not self.server:
            self._write()

    def _server_lock(self):
        # None = 서버 못 씀(주소 없음·죽음) → 로컬 잠금+시간대로 넘어감. True/False 만 서버 의견.
        try:
            r = self.kb.account_lock(self.tag, self.cid)
            return None if r is None else bool(r)
        except Exception:  # noqa: BLE001
            return None

    def release(self):
        if self.server and self.kb is not None:
            try:
                self.kb.account_unlock(self.tag, self.cid)
            except Exception:  # noqa: BLE001
                pass
        elif self.held:
            cur = self._read()
            if cur and cur.get("collector_id") == self.cid:
                try:
                    os.remove(self.path)
                except OSError:
                    pass
        self.held = False
