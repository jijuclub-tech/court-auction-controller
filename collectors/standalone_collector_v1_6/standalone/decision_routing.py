# -*- coding: utf-8 -*-
"""v1.6(t845u-2): 이벤트별 판단 담당표(decision_routing).

담당: PROGRAM(조각그림·화면글자) / LOCAL_LLM(ollama, 무료·선택, 없으면 건너뜀) / DEEPSEEK / GEMINI(상위) / HUMAN.
- 처음 보는 이벤트: 아래 단계부터 차례로 올라간다. 최종으로 푼 담당·걸린 시간을 기록해 표를 자동 갱신.
- 같은 이벤트가 다시 생기면: 표의 담당으로 바로 보낸다(그 아래 '모델' 단계는 건너뜀. GEMINI 담당이면 LOCAL_LLM·DEEPSEEK 생략).
  PROGRAM 확인(무료·화면 안에서만)은 늘 먼저 한 번 해 본다 — 강등 판단에 필요.
- 담당 모델이 그 이벤트에서 promote_after(N)번 실패하면 한 단계 승격. PROGRAM 이 demote_after 번 연속 맞추면 한 단계 강등.
- 딥시크는 한 번의 판단에서 최대 1회(애매할 때만 1회 규칙과 같음).
- 단독판 사다리: PROGRAM → LOCAL_LLM → DEEPSEEK → GEMINI (그 뒤는 기존대로 기록·알림·STOP).
  보급형 사다리: ... → GEMINI → HUMAN.
- 표는 관제기 참고자료(site_pack/<site>/decision_routing.json)로 공유. 이벤트 이름·횟수·초만 담고 비밀값은 넣지 않는다.
"""
from __future__ import annotations

import re

TIERS = ("PROGRAM", "LOCAL_LLM", "DEEPSEEK", "GEMINI", "HUMAN")
LADDER_STANDALONE = ("PROGRAM", "LOCAL_LLM", "DEEPSEEK", "GEMINI")
LADDER_POPULAR = ("PROGRAM", "LOCAL_LLM", "DEEPSEEK", "GEMINI", "HUMAN")
API_OF = {"LOCAL_LLM": "local", "DEEPSEEK": "deepseek", "GEMINI": "gemini"}
TIER_OF = {v: k for k, v in API_OF.items()}
_SAFE_EVENT = re.compile(r"^[0-9A-Za-z가-힣_:.@/\-]{1,80}$")
_SECRETISH = re.compile(r"(sk-|AIza|token|password|passwd|secret|bearer)", re.I)


def safe_event(ev):
    ev = str(ev or "").strip()
    if not _SAFE_EVENT.match(ev) or _SECRETISH.search(ev):
        return None
    return ev


class DecisionRouter:
    def __init__(self, table=None, ladder=LADDER_STANDALONE, promote_after=2, demote_after=3, log=lambda *a, **k: None):
        self.ladder = tuple(ladder)
        self.t = table if isinstance(table, dict) else {}
        self.promote_after, self.demote_after, self.log = int(promote_after), int(demote_after), log
        self.counts = {}           # 이번 회차 담당별 {n, sec}

    def owner(self, event):
        e = self.t.get(event)
        return e["owner"] if e and e.get("owner") in self.ladder else None

    def plan(self, event):
        """시도할 담당 순서. 처음이면 사다리 전체, 아니면 PROGRAM + 담당부터 위로."""
        own = self.owner(event)
        if own is None:
            return list(self.ladder)
        i = self.ladder.index(own)
        return ["PROGRAM"] + [t for t in self.ladder[i:] if t != "PROGRAM"]

    def _next(self, tier, d=1):
        i = self.ladder.index(tier) + d
        return self.ladder[max(0, min(len(self.ladder) - 1, i))]

    def record(self, event, solved_by, sec, failed=()):
        """solved_by: 푼 담당 또는 None. failed: 이번에 실패한 담당들(순서대로)."""
        ev = safe_event(event)
        if ev is None:
            return None
        known = ev in self.t
        e = self.t.setdefault(ev, {"owner": "PROGRAM", "n": 0, "fails": {}, "prog_streak": 0,
                                   "solved": {}, "sec_sum": 0.0})
        e["n"] += 1
        e["sec_sum"] = round(float(e["sec_sum"]) + float(sec or 0), 3)
        for t in failed:
            if t == "PROGRAM":
                continue
            e["fails"][t] = int(e["fails"].get(t, 0)) + 1
        if solved_by:
            e["solved"][solved_by] = int(e["solved"].get(solved_by, 0)) + 1
            c = self.counts.setdefault(solved_by, {"n": 0, "sec": 0.0})
            c["n"] += 1
            c["sec"] = round(c["sec"] + float(sec or 0), 3)
        old = e["owner"]
        if solved_by == "PROGRAM":
            e["prog_streak"] = int(e["prog_streak"]) + 1
            if e["prog_streak"] >= self.demote_after and old != "PROGRAM":
                e["owner"] = self._next(old, -1)
                e["prog_streak"] = 0
        else:
            e["prog_streak"] = 0
            if solved_by and (not known or self.ladder.index(solved_by) > self.ladder.index(old)):
                e["owner"] = solved_by                                   # 처음 푼 담당 / 더 위가 풀었으면 그쪽으로
        if e["owner"] != "PROGRAM" and int(e["fails"].get(e["owner"], 0)) >= self.promote_after and e["owner"] != self.ladder[-1]:
            e["fails"][e["owner"]] = 0
            e["owner"] = self._next(e["owner"], 1)                       # 담당 모델 N회 실패 → 승격
        if e["owner"] != old:
            self.log("routing_change", event=ev, frm=old, to=e["owner"])
        return e["owner"]

    def export(self):
        """관제기 공유용(비밀값 없음): 이벤트 이름·담당·횟수·초만."""
        out = {}
        for ev, e in self.t.items():
            if safe_event(ev) is None:
                continue
            out[ev] = {k: e.get(k) for k in ("owner", "n", "fails", "prog_streak", "solved", "sec_sum")}
        return out
