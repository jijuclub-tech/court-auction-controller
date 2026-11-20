# -*- coding: utf-8 -*-
"""요청 간격·휴식·429 상태 (문제 1, 3).

RateState 는 state/rate_state.json 에 남는다(프로그램을 다시 켜도 쉬는 시간·간격 배수가 이어짐).
모든 시계·잠자기는 넘겨받아 오프라인 시험이 가능하다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import time

from .page_guard import RateGate


class RateState:
    def __init__(self, path, now_fn=time.time, rest_min_on_corrupt=120, log=lambda *a, **k: None):
        self.path, self.now, self.log = path, now_fn, log
        self.d = {"rest_until": 0.0, "rest_why": "", "gap_mult": 1.0, "hits_by_day": {}, "day_over": "", "last_429_day": "", "last_429_ts": 0.0}
        self.corrupt = False
        if os.path.exists(path):
            try:
                loaded = json.load(open(path, encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError("not dict")
                self.d.update(loaded)
            except Exception as e:  # noqa: BLE001  (M3: 깨지면 보수적으로 쉬고 시작)
                self.corrupt = True
                self.d["rest_until"] = self.now() + rest_min_on_corrupt * 60
                self.d["rest_why"] = "rate_state_corrupt"
                self.log("rate_state_corrupt", err=type(e).__name__, rest_min=rest_min_on_corrupt)
                try:
                    os.replace(path, path + ".corrupt_%d" % int(self.now()))
                except OSError:
                    pass
                self.save()

    def today(self):
        return dt.datetime.fromtimestamp(self.now()).strftime("%Y-%m-%d")

    def effective_mult(self, cap=4.0):
        """마지막 429 뒤 24시간이 지나야 배수를 1로 되돌린다 (검수 N7). 상한도 건다 (H1)."""
        m = float(self.d.get("gap_mult", 1.0) or 1.0)
        last_ts = float(self.d.get("last_429_ts", 0) or 0)
        if not last_ts:
            # 옛 상태 파일(v1.1 이전)엔 ts 가 없음 → last_429_day 23:59 를 기준으로 (검수 R9)
            day = self.d.get("last_429_day") or ""
            if day and m > 1.0:
                try:
                    base = dt.datetime.strptime(day + " 23:59", "%Y-%m-%d %H:%M")
                    last_ts = base.timestamp()
                    self.d["last_429_ts"] = last_ts
                except ValueError:
                    pass
        if last_ts and (self.now() - last_ts) >= 86400:
            m = 1.0
            if self.d.get("gap_mult", 1.0) != 1.0:
                self.d["gap_mult"] = 1.0
                self.save()
        return max(1.0, min(cap, m))

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        json.dump(self.d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def can_start(self):
        """(True, '') 또는 (False, 이유)."""
        if self.d.get("day_over") == self.today():
            return False, "day_over_429_twice"
        left = float(self.d.get("rest_until") or 0) - self.now()
        if left > 0:
            return False, "rest_%dmin_%s" % (int(left // 60) + 1, self.d.get("rest_why", ""))
        return True, ""

    def rest(self, minutes, why):
        until = self.now() + minutes * 60
        if until > float(self.d.get("rest_until") or 0):
            self.d["rest_until"], self.d["rest_why"] = until, why
        self.save()
        return until

    def on_429(self, rest_min=120, mult=2.0, max_per_day=1, gap_mult_max=4.0):
        """429 한 번 맞음. 돌려줌: 'rest'(쉬고 간격 배수) 또는 'day_over'(같은 날 2번째)."""
        day = self.today()
        n = int(self.d["hits_by_day"].get(day, 0)) + 1
        self.d["hits_by_day"][day] = n
        base = self.effective_mult(gap_mult_max)                 # 지난 날 배수는 먼저 1로 되돌린 값
        self.d["gap_mult"] = min(gap_mult_max, base * mult)      # 상한(기본 4배)에서 멈춤 — 무한 누적 금지
        self.d["last_429_day"] = day
        self.d["last_429_ts"] = self.now()
        self.rest(rest_min, "HTTP_429")
        if n > max_per_day:
            self.d["day_over"] = day
            self.save()
            return "day_over"
        self.save()
        return "rest"


class Pacer:
    """물건 사이 60~120초, 시간당 20건, 회차당 40건, 50~55분 일하면 5~10분 쉼."""

    def __init__(self, pacing: dict, state: RateState, now_fn=time.time, sleep_fn=time.sleep, rand_fn=random.uniform):
        self.p, self.state, self.now, self.sleep, self.rand = pacing, state, now_fn, sleep_fn, rand_fn
        self.gate = RateGate(gap_sec=tuple(pacing["item_gap_sec"]), per_hour=pacing["max_items_per_hour"],
                             per_run=pacing["max_items_per_run"],
                             mult=state.effective_mult(pacing.get("gap_mult_max", 4.0)),
                             now_fn=now_fn, sleep_fn=sleep_fn, rand_fn=rand_fn)
        self.work_start = now_fn()
        self.next_break = self.work_start + 60 * rand_fn(*pacing["work_min_before_break"])
        self.breaks = 0

    def maybe_break(self, log=lambda *a, **k: None):
        """매시간 5~10분 쉼. 쉰 초를 돌려줌."""
        if self.now() < self.next_break:
            return 0.0
        sec = 60 * self.rand(*self.p["break_min"])
        log("hour_break", sec=int(sec))
        self.sleep(sec)
        self.breaks += 1
        self.next_break = self.now() + 60 * self.rand(*self.p["work_min_before_break"])
        return sec

    def before_item(self, log=lambda *a, **k: None):
        """다음 물건 전에 부름. None 이면 회차 상한(그만)."""
        self.maybe_break(log)
        return self.gate.wait_before_item()

    def limits(self):
        return self.gate.limits()
