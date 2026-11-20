# -*- coding: utf-8 -*-
"""배터리 20% 미만·충전기 빠짐 경고 (문제 12). Windows GetSystemPowerStatus(ctypes, 무료)."""
from __future__ import annotations

import sys


def read_power():
    """(퍼센트 또는 None, 충전기 꽂힘 True/False/None)."""
    if sys.platform != "win32":
        return None, None
    import ctypes
    from ctypes import wintypes

    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
                    ("BatteryLifePercent", wintypes.BYTE), ("SystemStatusFlag", wintypes.BYTE),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]
    s = SPS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return None, None
    pct = None if s.BatteryLifePercent == 255 else int(s.BatteryLifePercent)
    ac = {0: False, 1: True}.get(int(s.ACLineStatus) & 0xFF)
    return pct, ac


class PowerWatch:
    """check() -> 경고 목록. 같은 경고는 상태가 바뀔 때만 다시 낸다. 10% 미만이면 'stop'."""

    def __init__(self, cfg_power, reader=read_power, log=lambda *a, **k: None, alert=print):
        self.c, self.read, self.log, self.alert = cfg_power, reader, log, alert
        self.last = set()

    def check(self):
        pct, ac = self.read()
        now = set()
        if ac is False and self.c.get("warn_unplugged", True):
            now.add("unplugged")
        if pct is not None and pct < self.c.get("battery_min_pct", 20):
            now.add("battery_low")
        if pct is not None and pct < 10 and ac is False:
            now.add("stop")
        for w in sorted(now - self.last):
            msg = {"unplugged": "충전기가 빠졌어요. 꽂아 주세요.",
                   "battery_low": "배터리가 %s%% 예요(20%% 미만). 충전기를 꽂아 주세요." % pct,
                   "stop": "배터리가 10% 미만이라 이번 회차를 마치고 멈춰요."}[w]
            self.log("power_warn", what=w, pct=pct, ac=ac)
            self.alert(msg)
        self.last = now
        return sorted(now)
