# -*- coding: utf-8 -*-
"""사이트 거부 기록 (v1.5). site_alive(HEAD)·NTP·거부 호스트를 JSONL 로 '덧붙이기만' 한다.

한 줄 = 한 건:
  ts_kst        시각(KST, +09:00)
  host          대상 호스트(주소 없이 호스트만)
  kind          site_alive | ntp | kb_server | other
  actor         POLICY(우리 규칙이 요청 자체를 막음) | REMOTE(보냈는데 상대/망이 거절·무응답)
  reason        이유 코드 (POLICY_REFUSED, HTTP_4XX, HTTP_5XX, TIMEOUT, CONN_REFUSED, DNS_FAIL, NO_RESPONSE, SOCKET_ERROR ...)
  http_status   HTTP 상태(없으면 null)
  socket_error  소켓 오류 이름(없으면 null)
  attempt       같은 (kind, host) 의 몇 번째 시도인지(1부터)
  next_allowed_kst  다음에 다시 보내도 되는 시각(KST). POLICY 는 null(영구 금지)
법원·iros 는 요청을 보내지 않고 POLICY_REFUSED 로만 적는다. 비밀값·주소의 경로/쿼리는 적지 않는다.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import socket
import threading
import time

KST = _dt.timezone(_dt.timedelta(hours=9))
BANNED_HOST_PARTS = ("courtauction", "iros", "scourt.go.kr")


def kst(ts):
    return _dt.datetime.fromtimestamp(float(ts), KST).isoformat(timespec="seconds")


def host_of(url_or_host):
    s = str(url_or_host or "").strip().lower()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    return s.split(":", 1)[0]


def is_banned(url_or_host, extra=()):
    h = host_of(url_or_host)
    return any(b in h for b in tuple(BANNED_HOST_PARTS) + tuple(extra))


def classify_exc(e):
    """예외 → (reason, http_status, socket_error)."""
    code = getattr(e, "code", None)
    if isinstance(code, int):
        return ("HTTP_4XX" if 400 <= code < 500 else "HTTP_5XX" if code >= 500 else "HTTP_%d" % code), code, None
    r = getattr(e, "reason", None)
    inner = r if isinstance(r, BaseException) else e
    name = type(inner).__name__
    if isinstance(inner, socket.timeout) or "timeout" in name.lower() or "timed out" in str(inner).lower():
        return "TIMEOUT", None, name
    if isinstance(inner, ConnectionRefusedError):
        return "CONN_REFUSED", None, name
    if isinstance(inner, ConnectionResetError):
        return "CONN_RESET", None, name
    if isinstance(inner, socket.gaierror):
        return "DNS_FAIL", None, name
    if isinstance(inner, OSError):
        return "SOCKET_ERROR", None, name
    return "OTHER_ERROR", None, name


class RefusalLog:
    def __init__(self, path, now=time.time):
        self.path, self.now = path, now
        self._attempts = {}
        self._lock = threading.Lock()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)

    def record(self, host, kind, actor, reason, http_status=None, socket_error=None, next_allowed=None,
               attempt=None, **extra):
        h = host_of(host)
        with self._lock:
            k = (kind, h)
            self._attempts[k] = self._attempts.get(k, 0) + 1
            n = attempt if attempt is not None else self._attempts[k]
            rec = {"ts_kst": kst(self.now()), "host": h, "kind": kind,
                   "actor": "POLICY" if str(actor).upper().startswith("POL") else "REMOTE",
                   "reason": str(reason), "http_status": http_status, "socket_error": socket_error,
                   "attempt": int(n), "next_allowed_kst": (kst(next_allowed) if next_allowed else None)}
            for kk, v in extra.items():
                if kk not in rec and isinstance(v, (str, int, float, bool, type(None))):
                    rec[kk] = v
            with open(self.path, "a", encoding="utf-8") as f:      # append-only
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            return rec

    def policy_refused(self, host, kind, why="POLICY_REFUSED"):
        return self.record(host, kind, "POLICY", why if why.startswith("POLICY") else "POLICY_REFUSED", why=why)

    def remote_fail(self, host, kind, exc=None, http_status=None, retry_sec=60, reason=None):
        if exc is not None:
            r, st, se = classify_exc(exc)
        else:
            r, st, se = (reason or ("HTTP_4XX" if http_status and 400 <= http_status < 500 else
                                    "HTTP_5XX" if http_status and http_status >= 500 else "NO_RESPONSE")), http_status, None
        return self.record(host, kind, "REMOTE", reason or r, http_status=st, socket_error=se,
                           next_allowed=self.now() + float(retry_sec))

    def read(self):
        try:
            return [json.loads(l) for l in open(self.path, encoding="utf-8") if l.strip()]
        except FileNotFoundError:
            return []
