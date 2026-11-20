# -*- coding: utf-8 -*-
"""지역별 공개 NTP(SNTP) 확인 (한영님 지시 2026-11-19 14:52, 음성).

목적: '(A) 내 컴퓨터 멈춤' 분기를 넓혀, 화면·브라우저를 건드리지 않고 UDP 로만
선로·인터넷 상태와 노트북 시계 점프(잠깐 멈춤)를 본다. 법원·iros 로는 절대 요청하지 않는다.

- 지역 묶음(설정 JSON, '늘리기'만): 국내→일본→동남아→미국→유럽, 지역마다 2~3대.
- 한 대만 무응답   = 서버 한 대 장애 (SERVER_DOWN, 가벼움)
- 그 지역 전부 무응답 = 그 방향 선로 차단 (REGION_BLOCKED)
- 모든 지역 무응답   = 우리 쪽 인터넷 끊김 (ALL_DOWN)
- UDP 123 SNTP 48바이트 1회, 서버당 2초 제한, 응답 유무 + 왕복시간만 본다. 연속 요청 금지.
- 평소 10분마다 1지역씩 순환, 멈춤 의심 때 즉시 한 바퀴.
- 받은 NTP 시각과 노트북 monotonic 경과 차이가 갑자기 크게(기본 30초) 벌어지면 PC_HICCUP.
"""
from __future__ import annotations

import json
import os
import struct
import time

NTP_UNIX_DELTA = 2208988800   # 1900→1970 초

DEFAULT_REGIONS = [
    ["KR", ["time.kriss.re.kr", "time.bora.net", "kr.pool.ntp.org"]],
    ["JP", ["ntp.nict.jp", "jp.pool.ntp.org"]],
    ["SEA", ["sg.pool.ntp.org", "asia.pool.ntp.org"]],
    ["US", ["time.google.com", "time.cloudflare.com", "us.pool.ntp.org"]],
    ["EU", ["europe.pool.ntp.org", "de.pool.ntp.org"]],
]


def merge_regions(cfg_regions, on_ban=None):
    """설정으로 '늘리기'만: 기본 지역·서버에 더하기만 하고 빼지 않는다.
    on_ban(server): 금지 호스트를 뺄 때마다 불림(v1.5 거부 기록 POLICY_REFUSED)."""
    out = [[name, list(servers)] for name, servers in DEFAULT_REGIONS]
    idx = {name: i for i, (name, _s) in enumerate(out)}
    BAN = ("courtauction", "iros", "scourt.go.kr", ".go.kr")   # 법원·iros 로는 NTP 도 보내지 않음 (R5)
    for item in (cfg_regions or []):
        try:
            name = item[0]
            servers = []
            for sv in item[1]:
                if any(b in str(sv).lower() for b in BAN):
                    if on_ban is not None:
                        try:
                            on_ban(str(sv))
                        except Exception:  # noqa: BLE001
                            pass
                    continue
                servers.append(sv)
        except Exception:  # noqa: BLE001
            continue
        if name in idx:
            for sv in servers:
                if sv not in out[idx[name]][1]:
                    out[idx[name]][1].append(sv)
        else:
            out.append([name, servers])
            idx[name] = len(out) - 1
    return out


def _resolve(host, timeout):
    """host→IP 를 제한시간 안에만(스레드). 못 하면 None. getaddrinfo 는 자체 timeout 이 없어서 감싼다 (R5)."""
    import socket
    import threading
    out = {}
    def run():
        try:
            out["ip"] = socket.getaddrinfo(host, 123, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
        except Exception:  # noqa: BLE001
            out["ip"] = None
    th = threading.Thread(target=run, daemon=True)
    th.start(); th.join(timeout)
    return out.get("ip")


def udp_sntp(server, timeout=2.0):
    """UDP 123 으로 SNTP 1회. 돌려줌 (rtt_sec, ntp_unix_time) 또는 None(무응답). 브라우저 안 염."""
    import socket
    ip = _resolve(server, timeout)
    if not ip:
        return None
    pkt = b"\x1b" + b"\x00" * 47
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t0 = time.time()
        s.sendto(pkt, (ip, 123))
        data, _ = s.recvfrom(48)
        rtt = time.time() - t0
        if len(data) < 48:
            return None
        secs = struct.unpack("!I", data[40:44])[0]
        return rtt, secs - NTP_UNIX_DELTA
    except Exception:  # noqa: BLE001
        return None
    finally:
        s.close()


class NtpChecker:
    def __init__(self, regions=None, query=udp_sntp, now=time.time, monotonic=time.monotonic,
                 log=lambda *a, **k: None, stats_path=None, timeout=2.0, hiccup_sec=30.0, on_fail=None):
        self.regions = regions or [list(r) for r in DEFAULT_REGIONS]
        self.query, self.now, self.monotonic, self.log = query, now, monotonic, log
        self.stats_path, self.timeout, self.hiccup_sec = stats_path, timeout, hiccup_sec
        self._last_mono = None
        self._last_ntp = None
        self._cursor = 0          # 10분 순환용 지역 포인터
        self.on_fail = on_fail    # on_fail(server, reason): 무응답 서버 기록(v1.5 거부 기록, REMOTE)

    def _probe_region(self, servers):
        """지역 한 묶음. 돌려줌 (responded, total, best_rtt, ntp_time|None)."""
        ok, best, ntp = 0, None, None
        for sv in servers:
            r = self.query(sv, self.timeout)
            if r is None and self.on_fail is not None:
                try:
                    self.on_fail(sv, "NTP_NO_RESPONSE")
                except Exception:  # noqa: BLE001
                    pass
            if r is not None:
                ok += 1
                rtt, t = r
                if best is None or rtt < best:
                    best, ntp = rtt, t
        return ok, len(servers), best, ntp

    def sweep(self):
        """한 바퀴(멈춤 의심 때). 돌려줌 dict: code, regions, ntp_time, hiccup."""
        results = {}
        any_ok = False
        region_all_down = []
        some_server_down = False
        ntp_time = None
        best_overall = None
        for name, servers in self.regions:
            ok, total, best, t = self._probe_region(servers)
            results[name] = {"ok": ok, "total": total, "best_rtt": best}
            if ok:
                any_ok = True
                if best_overall is None or (best is not None and best < best_overall):
                    best_overall, ntp_time = best, t
            if ok == 0:
                region_all_down.append(name)
            elif ok < total:
                some_server_down = True
        if not any_ok:
            code = "ALL_DOWN"
        elif region_all_down:
            code = "REGION_BLOCKED"
        elif some_server_down:
            code = "SERVER_DOWN"
        else:
            code = "OK"
        hiccup = self._check_hiccup(ntp_time)
        self._bump(code)
        self.log("ntp_sweep", code=code, down_regions=region_all_down, hiccup=round(hiccup, 1) if hiccup else 0)
        return {"code": code, "regions": results, "ntp_time": ntp_time,
                "down_regions": region_all_down, "hiccup": hiccup}

    def _check_hiccup(self, ntp_time):
        """노트북 시계가 잠깐 멈춤(점프)했는지. NTP 경과와 monotonic 경과 차이."""
        mono = self.monotonic()
        if ntp_time is None:
            self._last_mono, self._last_ntp = mono, ntp_time
            return 0.0
        jump = 0.0
        if self._last_mono is not None and self._last_ntp is not None:
            d_mono = mono - self._last_mono
            d_ntp = ntp_time - self._last_ntp
            jump = abs(d_ntp - d_mono)
        self._last_mono, self._last_ntp = mono, ntp_time
        return jump if jump >= self.hiccup_sec else 0.0

    def next_region_probe(self):
        """평소 10분 순환용: 한 지역만 본다(연속 요청 금지)."""
        if not self.regions:
            return None
        name, servers = self.regions[self._cursor % len(self.regions)]
        self._cursor += 1
        ok, total, best, t = self._probe_region(servers)
        self._check_hiccup(t if ok else None)
        return {"region": name, "ok": ok, "total": total, "best_rtt": best, "ntp_time": t}

    def _bump(self, code):
        if not self.stats_path:
            return
        try:
            d = json.load(open(self.stats_path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            d = {}
        d[code] = int(d.get(code, 0)) + 1
        try:
            os.makedirs(os.path.dirname(self.stats_path) or ".", exist_ok=True)
            tmp = self.stats_path + ".tmp"
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, self.stats_path)
        except OSError:
            pass
