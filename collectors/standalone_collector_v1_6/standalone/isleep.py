# -*- coding: utf-8 -*-
"""긴 잠자기 중에도 STOP 을 보는 쪼갠 잠자기 (검수 M2).

5초 단위로 자면서 STOP_<작업>·STOP_ALL 을 확인한다. 있으면 StopRequested 를 던진다.
배터리 10% 미만+충전기 빠짐도 함께 본다(있으면 멈춤). 시계·잠자기는 넘겨받아 시험 가능.
"""
from __future__ import annotations

import time


class StopRequested(Exception):
    def __init__(self, why):
        super().__init__(why)
        self.why = why


class InterruptibleSleep:
    def __init__(self, stops, job, family=None, power=None, step=5.0, sleep_fn=time.sleep, now_fn=time.time):
        self.stops, self.job, self.family = stops, job, family
        self.power, self.step, self.sleep, self.now = power, step, sleep_fn, now_fn

    def _check(self):
        why = self.stops.check(self.job, self.family) if self.stops else None
        if why:
            raise StopRequested("stop_file:" + why)
        if self.power and "stop" in self.power.check():
            raise StopRequested("battery_low")

    def __call__(self, seconds):
        seconds = max(0.0, float(seconds))
        self._check()
        end = self.now() + seconds
        while True:
            left = end - self.now()
            if left <= 0:
                return
            self.sleep(min(self.step, left))
            self._check()
