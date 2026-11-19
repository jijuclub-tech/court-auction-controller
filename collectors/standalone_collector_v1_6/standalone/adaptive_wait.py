# -*- coding: utf-8 -*-
"""v1.6(t845u-1): 적응 대기.

- 화면 복잡도 = OCR 글자 양 · 표 줄 수 · 그림 수를 0~1 점수로. 복잡하면 대기를 늘리고, 단순하면 빨리 누른다.
- 사이트·단계별 실제 반응 시간(화면이 다 될 때까지 걸린 초)을 방문할 때마다 누적:
  지수 이동 평균(EWMA) + 최근 표본의 75% 분위수. 표본이 min_samples 보다 적으면 '보수적'(줄이지 않음, 늘리기만).
  보정값(adj, +/-)= 추정 반응시간 − 기본 대기, [-0.5×기본, +2×기본] 로 자름.
- 바닥(floor): 법원은 court_min_gap_sec, 그 밖은 min_gap_sec. 429 간격 배수(gap_mult)를 곱한 값 아래로는 절대 안 줄임.
  RateGate(물건 사이 60~120초·시간당 상한)·휴식·계정 교대는 이 모듈이 손대지 않는다(별도 경로).
- screen_state 와의 관계: screen_state 의 상한(load_max/frozen/settle)은 사이트 JSON 으로 '늘리기만' 그대로.
  여기 추정은 '클릭 뒤 첫 확인까지 쉬는 시간'만 바꾸고, 멈춤 판정 바닥(typical×1.3)은 올리는 쪽으로만 쓴다 → 충돌 없음.
"""
from __future__ import annotations

import random

DEFAULTS = {"alpha": 0.3, "min_samples": 5, "keep": 50, "simple_factor": 0.7, "complex_factor": 1.5,
            "min_gap_sec": 0.3, "court_min_gap_sec": 1.0}


def complexity(words=0, rows=0, images=0):
    """0(단순)~1(복잡). 글자 150개·표 30줄·그림 20개면 각각 가득."""
    w = min(max(words, 0) / 150.0, 1.0)
    r = min(max(rows, 0) / 30.0, 1.0)
    i = min(max(images, 0) / 20.0, 1.0)
    return round(0.5 * w + 0.3 * r + 0.2 * i, 3)


def count_images(im, grid=(16, 9), std_min=38.0):
    """대충 '그림 칸' 수: 축소 회색 화면을 칸으로 나눠 명암 편차가 큰 칸 수."""
    g = im.convert("L").resize((grid[0] * 8, grid[1] * 8))
    px = g.load()
    n = 0
    for gy in range(grid[1]):
        for gx in range(grid[0]):
            vs = [px[gx * 8 + x, gy * 8 + y] for y in range(8) for x in range(8)]
            m = sum(vs) / 64.0
            sd = (sum((v - m) ** 2 for v in vs) / 64.0) ** 0.5
            if sd >= std_min:
                n += 1
    return n


class ReactionStats:
    """{key: {"n", "ewma", "samples": [...]}}. 저장은 호출자가(메모리 참고자료 site_pack/<site>/timing.json)."""

    def __init__(self, table=None, cfg=None):
        self.c = dict(DEFAULTS, **(cfg or {}))
        self.t = table if isinstance(table, dict) else {}

    def add(self, key, sec):
        if not key or sec is None or sec < 0:
            return
        e = self.t.setdefault(key, {"n": 0, "ewma": float(sec), "samples": []})
        a = float(self.c["alpha"])
        e["ewma"] = float(sec) if e["n"] == 0 else a * float(sec) + (1 - a) * float(e["ewma"])
        e["n"] += 1
        e["samples"] = (e["samples"] + [round(float(sec), 3)])[-int(self.c["keep"]):]

    def estimate(self, key):
        """(추정초, 표본수). 추정 = max(EWMA, 75% 분위수) — 늦는 쪽으로 보수적."""
        e = self.t.get(key)
        if not e or not e.get("n"):
            return None, 0
        s = sorted(e["samples"])
        q = s[min(len(s) - 1, int(round(0.75 * (len(s) - 1))))] if s else e["ewma"]
        return max(float(e["ewma"]), float(q)), int(e["n"])


class AdaptiveWait:
    def __init__(self, stats, family="", gap_mult=1.0, cfg=None, rand=random.uniform):
        self.s, self.c, self.rand = stats, dict(DEFAULTS, **(cfg or {})), rand
        base_floor = self.c["court_min_gap_sec"] if family == "court" else self.c["min_gap_sec"]
        self.floor = float(base_floor) * max(1.0, float(gap_mult or 1.0))

    def plan(self, key, lo, hi, score=0.0):
        """돌려줌 (기다릴 초, 정보). 정보: base, est, n, adj, factor, score, floor, conservative."""
        base = float(self.rand(lo, hi if hi is not None else lo))
        est, n = self.s.estimate(key)
        factor = self.c["simple_factor"] + (self.c["complex_factor"] - self.c["simple_factor"]) * float(score)
        conservative = n < int(self.c["min_samples"])
        if conservative:
            adj = max(0.0, (est or 0.0) - base) if est is not None else 0.0     # 표본 적음: 늘리기만
            sec = (base + adj) * max(1.0, factor)                                # 단순해도 줄이지 않음
        else:
            adj = max(-0.5 * base, min(2.0 * base, est - base))
            sec = (base + adj) * factor
        sec = max(self.floor, sec)
        return sec, {"base": round(base, 3), "est": est, "n": n, "adj": round(adj, 3), "factor": round(factor, 3),
                     "score": score, "floor": self.floor, "conservative": conservative}
