# -*- coding: utf-8 -*-
"""v1.6(t845u-3): 시간 예산.

판단(AI·사람) 때문에 늦어진 초를 회차 운영시간에 합산해 보고한다. 누적 지연이 deficit_sec 를 넘으면
'여유 대기'(적응 대기로 계산한 클릭 뒤 쉼)만 slack_factor 배로 줄여 만회한다(바닥 아래로는 안 줄임).
안전 간격(429 간격·RateGate·시간당 휴식·계정 교대·사이트 휴식)은 이 모듈을 거치지 않으므로 줄지 않는다.
"""
from __future__ import annotations


class TimeBudget:
    def __init__(self, now, deficit_sec=20.0, slack_factor=0.6):
        self.now = now
        self.t0 = now()
        self.deficit_sec, self.slack = float(deficit_sec), float(slack_factor)
        self.decision = {}       # 담당별 {n, sec}
        self.decision_sec = 0.0
        self.saved_sec = 0.0
        self.waits = 0

    def add_decision(self, owner, sec):
        d = self.decision.setdefault(owner, {"n": 0, "sec": 0.0})
        d["n"] += 1
        d["sec"] = round(d["sec"] + max(0.0, float(sec)), 3)
        if owner != "PROGRAM":
            self.decision_sec += max(0.0, float(sec))

    def deficit(self):
        return max(0.0, self.decision_sec - self.saved_sec)

    def slack_wait(self, sec, floor):
        """여유 대기 sec 를 (지연이 쌓였으면) 줄여 돌려줌. 바닥 floor 아래로는 안 줄임."""
        self.waits += 1
        if self.deficit() <= self.deficit_sec or sec <= floor:
            return sec
        new = max(floor, sec * self.slack)
        self.saved_sec += sec - new
        return new

    def report(self):
        return {"run_sec": round(self.now() - self.t0, 3), "decision_sec": round(self.decision_sec, 3),
                "saved_sec": round(self.saved_sec, 3), "deficit_sec": round(self.deficit(), 3),
                "by_owner": self.decision, "slack_waits": self.waits}
