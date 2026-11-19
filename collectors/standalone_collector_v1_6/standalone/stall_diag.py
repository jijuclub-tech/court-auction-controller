# -*- coding: utf-8 -*-
"""화면이 멈췄을 때 '왜 멈췄나' 원인 진단 (한영님 지시 2026-11-19 14:50).

AI 없이 프로그램으로 순서대로 본다(딥시크는 애매할 때만 1회 요약).
  (A) 내 컴퓨터가 멈춤  : 피코로 마우스를 빈 곳에서 몇 px 움직여 보고, 커서 위치/작업표시줄 시계가
                         바뀌는지 본다. 안 바뀌면 PC_FROZEN(사진·기록 후 멈춤).
  (B) 자료는 오는 중    : 크롬 쪽 받은 바이트가 5초 동안 늘고 있으면 DATA_FLOWING → 더 기다림(상한 늘림).
  (C) 보냈는데 응답 없음 : 받은 바이트가 안 늘면 ①기본 게이트웨이 ping ②DNS 조회
                         ③중립 사이트 연결(브라우저 안 염). 결과로 나눔:
        - 셋 다 됨          → SITE_NO_RESPONSE (그 사이트 문제: 5xx처럼 30분 쉬고 1번 재시도)
        - 게이트웨이O·바깥X → ISP_DOWN
        - 게이트웨이X        → LOCAL_NET_DOWN (와이파이/선)
      ISP/LOCAL 은 5분 간격으로 확인하며 대기, 복구되면 그 화면부터 다시.
법원·iros 주소로는 절대 직접 요청하지 않는다(규칙). 중립 사이트만 쓴다.
진단 결과는 사진과 함께 기록하고 사이트별 통계를 KB(stall_stats_local.json)에 쌓는다.
"""
from __future__ import annotations

import json
import os

# 브라우저를 열지 않는 '중립' 연결 확인용 주소(법원·iros 아님). 하나만 되면 바깥 인터넷 OK.
NEUTRAL_URLS = ("http://www.msftconnecttest.com/connecttest.txt",
                "http://connectivitycheck.gstatic.com/generate_204",
                "http://www.gstatic.com/generate_204")

CODES = ("PC_FROZEN", "PC_HICCUP", "DATA_FLOWING", "SITE_NO_RESPONSE", "ISP_DOWN", "LOCAL_NET_DOWN", "UNKNOWN")


def _default_net_bytes():
    try:
        import psutil
        c = psutil.net_io_counters()
        return int(c.bytes_recv)
    except Exception:  # noqa: BLE001
        return None


def _default_ping(host, timeout=2):
    if not host:
        return False
    import subprocess
    import sys
    flag = "-n" if sys.platform == "win32" else "-c"
    try:
        return subprocess.run(["ping", flag, "1", "-w", str(int(timeout * 1000)) if sys.platform == "win32" else str(timeout), host],
                              capture_output=True, timeout=timeout + 2).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _default_dns(host="www.gstatic.com"):
    import socket
    try:
        socket.getaddrinfo(host, 80)
        return True
    except Exception:  # noqa: BLE001
        return False


def _default_neutral(urls=NEUTRAL_URLS, timeout=3):
    import urllib.request
    for u in urls:
        try:
            with urllib.request.urlopen(u, timeout=timeout) as r:
                if r.status in (200, 204):
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _default_gateway():
    try:
        import subprocess
        import sys
        if sys.platform == "win32":
            out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=4).stdout
            for line in out.splitlines():
                if "Gateway" in line or "게이트웨이" in line:
                    ip = line.split(":")[-1].strip()
                    if ip and ip[0].isdigit():
                        return ip
            return ""
        out = subprocess.run(["ip", "route"], capture_output=True, text=True, timeout=4).stdout
        for line in out.splitlines():
            if line.startswith("default via "):
                return line.split()[2]
    except Exception:  # noqa: BLE001
        pass
    return ""


class StallDiag:
    def __init__(self, *, move_mouse, grab, read_clock, cursor_pos=None,
                 net_bytes=_default_net_bytes, ping=_default_ping, dns=_default_dns,
                 neutral=_default_neutral, gateway=_default_gateway,
                 now=None, sleep=None, log=lambda *a, **k: None, deepseek=None,
                 stats_path=None, net_window_sec=5.0, net_min_delta=20000, ntp=None, site_head=None):
        import time
        self.move_mouse, self.grab, self.read_clock, self.cursor_pos = move_mouse, grab, read_clock, cursor_pos
        self.net_bytes, self.ping, self.dns, self.neutral, self.gateway = net_bytes, ping, dns, neutral, gateway
        self.now = now or time.time
        self.sleep = sleep or time.sleep
        self.log, self.deepseek, self.stats_path = log, deepseek, stats_path
        self.net_window_sec, self.net_min_delta = net_window_sec, net_min_delta
        self.ntp = ntp              # NtpChecker (없어도 됨)
        self.site_head = site_head  # 상대 서버 HEAD 확인(호출자가 '허용된 사이트에만' 만들어 넘김. 법원·iros 금지)

    # ---- 통계 ----
    def _bump(self, site, code):
        if not self.stats_path:
            return
        try:
            d = json.load(open(self.stats_path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            d = {}
        key = (site or "?")
        e = d.get(key) or {}
        e[code] = int(e.get(code, 0)) + 1
        d[key] = e
        try:
            os.makedirs(os.path.dirname(self.stats_path) or ".", exist_ok=True)
            tmp = self.stats_path + ".tmp"
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, self.stats_path)
        except OSError:
            pass

    # ---- (A) 내 컴퓨터 멈춤 ----
    @staticmethod
    def _thumb(im):
        try:
            return list(im.convert("L").resize((32, 18)).getdata())
        except Exception:  # noqa: BLE001
            return None

    def _img_changed(self, a, b):
        ta, tb = self._thumb(a), self._thumb(b)
        if ta is None or tb is None or len(ta) != len(tb):
            return False
        return sum(abs(x - y) for x, y in zip(ta, tb)) / len(ta) > 6.0

    def _pc_state(self):
        """돌려줌 'alive' | 'frozen_suspect' | 'defer' (검수 R1).
        살아있다는 신호(커서 이동·시계 변화·화면 변화)가 하나라도 있으면 alive.
        커서를 못 보면 화면 동일만으론 단정하지 않고 defer(→ B·C 로)."""
        im0 = self.grab()
        clock0 = (self.read_clock() or "").strip()
        cur0 = self.cursor_pos() if self.cursor_pos else None
        self.move_mouse(7, 3)          # 빈 곳으로 몇 px (클릭 아님)
        self.sleep(0.8)
        self.move_mouse(-5, -2)
        self.sleep(1.2)
        im1 = self.grab()
        clock1 = (self.read_clock() or "").strip()
        cur1 = self.cursor_pos() if self.cursor_pos else None
        if (cur0 is not None and cur1 is not None and cur0 != cur1) \
           or (clock0 and clock1 and clock0 != clock1) \
           or self._img_changed(im0, im1):
            return "alive"
        if cur0 is None:
            return "defer"            # 커서 확인 불가 → PC_FROZEN 단정 금지
        return "frozen_suspect"

    # ---- (B) 자료 흐름 ----
    def _data_flowing(self):
        b0 = self.net_bytes()
        if b0 is None:
            return None            # 못 재면 판단 보류
        t0 = self.now()
        loops = 0
        while self.now() - t0 < self.net_window_sec and loops < int(self.net_window_sec) + 2:
            self.sleep(1.0)
            loops += 1             # 시계가 안 움직여도 무한대기 방지
        b1 = self.net_bytes()
        if b1 is None:
            return None
        return (b1 - b0) >= self.net_min_delta

    # ---- (C) 연결 확인 ----
    def _connectivity(self):
        gw = self.gateway() if callable(self.gateway) else self.gateway
        gw_ok = self.ping(gw) if gw else None
        dns_ok = self.dns()
        out_ok = self.neutral()
        if out_ok:
            return "SITE_NO_RESPONSE", {"gateway": gw, "gw_ok": gw_ok, "dns": dns_ok, "out": True}
        if gw_ok:
            return "ISP_DOWN", {"gateway": gw, "gw_ok": True, "dns": dns_ok, "out": False}
        if gw_ok is False:
            return "LOCAL_NET_DOWN", {"gateway": gw, "gw_ok": False, "dns": dns_ok, "out": False}
        return "UNKNOWN", {"gateway": gw, "gw_ok": gw_ok, "dns": dns_ok, "out": False}

    def diagnose(self, site=None):
        """돌려줌 (code, detail). code 는 CODES 중 하나. (A) 분기에 NTP 확인 포함."""
        # (A) 컴퓨터 멈춤: NTP 한 바퀴(시계 점프·선로) + 마우스/화면/커서 변화.
        sweep = None
        if self.ntp is not None:
            try:
                sweep = self.ntp.sweep()        # UDP 만, 화면·브라우저 안 건드림
            except Exception as e:  # noqa: BLE001
                self.log("ntp_err", err=type(e).__name__)
        hiccup = bool(sweep and sweep.get("hiccup"))
        pc = self._pc_state()
        if pc == "frozen_suspect" and hiccup:
            # 화면이 마우스 이동 전후로 똑같고 + NTP 시계 점프까지 → 확실할 때만 PC_FROZEN (R1)
            self._bump(site, "PC_FROZEN")
            return "PC_FROZEN", {"step": "A", "ntp": (sweep or {}).get("code")}
        if hiccup:
            self._bump(site, "PC_HICCUP")       # 노트북이 잠깐 멈췄다 깨어남(시계 점프)
            return "PC_HICCUP", {"step": "A", "hiccup": sweep["hiccup"], "ntp": sweep.get("code")}
        # 커서 확인 불가·살아있음 → A 보류하고 B·C 로
        # (B) 자료 흐름
        flow = self._data_flowing()
        if flow:
            self._bump(site, "DATA_FLOWING")
            return "DATA_FLOWING", {"step": "B"}
        # (C) 보냈는데 응답 없음 — NTP 결과가 있으면 그것으로 선로/인터넷 판단
        if sweep is not None:
            nc = sweep.get("code")
            gw = self.gateway() if callable(self.gateway) else self.gateway
            if nc in ("ALL_DOWN", "REGION_BLOCKED"):
                # R5: UDP 123 만 막혔을 수 있으니 중립 사이트로 한 번 더 확인한 뒤에만 ISP/LOCAL.
                if self.neutral():
                    self._bump(site, "SITE_NO_RESPONSE")
                    return "SITE_NO_RESPONSE", {"step": "C", "ntp": nc, "out": True}
                if nc == "ALL_DOWN":
                    code = "ISP_DOWN" if (gw and self.ping(gw)) else "LOCAL_NET_DOWN"
                    self._bump(site, code)
                    return code, {"step": "C", "ntp": nc, "gateway": gw}
                self._bump(site, "ISP_DOWN")
                return "ISP_DOWN", {"step": "C", "ntp": nc, "down_regions": sweep.get("down_regions")}
            # nc OK/SERVER_DOWN → 인터넷은 되는데 이 사이트 응답 없음
            alive = None
            if self.site_head is not None:
                try:
                    alive = bool(self.site_head())
                except Exception as e:  # noqa: BLE001
                    self.log("site_head_err", err=type(e).__name__)
                    alive = None
            self._bump(site, "SITE_NO_RESPONSE")
            return "SITE_NO_RESPONSE", {"step": "C", "ntp": nc, "server_alive": alive}
        code, detail = self._connectivity()
        if code == "UNKNOWN" and self.deepseek:
            # 애매할 때만 딥시크로 기록 요약 판단(최대 1회). 비밀값·사이트 주소는 보내지 않음.
            try:
                guess = (self.deepseek({"gw_ok": detail.get("gw_ok"), "dns": detail.get("dns"),
                                        "out": detail.get("out")}) or "").strip().upper()
                if guess in CODES:
                    code = guess
                    detail["by_ai"] = True
            except Exception as e:  # noqa: BLE001
                self.log("stall_ai_err", err=type(e).__name__)
        detail["step"] = "C"
        self._bump(site, code)
        return code, detail
