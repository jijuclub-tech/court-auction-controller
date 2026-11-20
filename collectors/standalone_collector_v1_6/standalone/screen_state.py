# -*- coding: utf-8 -*-
"""화면 멈춤 판단 (한영님 지시 2026-11-19 14:45).

매 단계 뒤 화면을 3가지로 나눈다.
  (1) 로딩 중  : 더 기다린다 (사이트별 상한, 법원 기본 60초)
  (2) 다 됨    : 화면이 2~3번 연속 거의 같고 기대 글자/조각그림이 보이면 진행
  (3) 멈춤     : 로딩 표시 없이 N초(기본 20초) 그대로인데 기대 글자가 없음 → 멈춤 판정

로딩으로 보는 신호:
  - '처리중/조회중/잠시만/로딩/Loading/검색중' 같은 글자
  - 화면이 계속 바뀜(축소 회색 화면 차이)
  - 빈 하얀 화면/반만 그려진 화면(내용이 거의 없음)
완료로 보는 신호:
  - 화면이 settle 횟수만큼 연속 거의 같음 + 기대 글자/조각그림 보임
  - 목록은 '표 줄 수'가 2번 연속 같아야 완료
광고 움짤 등 작은 영역만 깜빡이는 건 완료를 막지 않게, 변화량이 아주 작으면 '같음'으로 본다.

기준값은 사이트 JSON screen.* 로 '늘리기만' 할 수 있다(더 오래 기다리는 쪽).
기다린 시간은 기록해 사이트·단계별 평균/최대를 쌓고(timing_path), 다음엔 그 값 아래로는 멈춤 판정하지 않는다.
"""
from __future__ import annotations

import json
import os

LOADING_WORDS = ("처리중", "처리 중", "조회중", "조회 중", "잠시만", "로딩", "로드중", "불러오는",
                 "검색중", "검색 중", "loading", "please wait", "기다려")

DEFAULTS = {
    "load_max_sec": 60.0,     # 로딩 최대 대기(법원 기본). 넘으면 멈춤쪽으로.
    "frozen_sec": 20.0,       # 로딩 표시 없이 이만큼 그대로 + 기대 없음 → 멈춤
    "settle_sec": 1.5,        # 완료로 보려면 이 시간 동안 연속 같아야
    "settle_frames": 3,       # 연속 '같음' 프레임 수
    "interval": 0.5,          # 표본 간격
    "min_content": 0.012,     # 내용 비율이 이보다 낮으면 빈/반 그려진 화면(로딩)
    "same_eps": 6.0,          # 축소 회색 프레임 평균차가 이 이하면 '같음'(광고 깜빡임 흡수)
}

# 사이트 JSON 으로는 '늘리기만' 허용(더 오래/더 엄격하게 기다림)
_INC_ONLY = ("load_max_sec", "frozen_sec", "settle_sec", "settle_frames", "min_content")


def merge_caps(site_screen):
    out = dict(DEFAULTS)
    for k, v in (site_screen or {}).items():
        if k in _INC_ONLY:
            try:
                out[k] = max(DEFAULTS[k], float(v)) if k != "settle_frames" else max(DEFAULTS[k], int(v))
            except (TypeError, ValueError):
                pass
        elif k == "interval":
            pass   # 간격은 바꾸지 않음
    return out


def _thumb(im):
    g = im.convert("L").resize((32, 18))
    return list(g.getdata())


def _mean_abs_diff(a, b):
    if not a or not b or len(a) != len(b):
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _content_ratio(thumb):
    # 흰색(≥245)·검정 테두리 빼고 '내용'으로 보이는 픽셀 비율
    n = len(thumb) or 1
    return sum(1 for v in thumb if 20 < v < 245) / n


class ScreenState:
    def __init__(self, grab, ocr_text, now, sleep, log=lambda *a, **k: None,
                 guard_check=None, bank_find=None, timing_path=None):
        self.grab, self.ocr_text, self.now, self.sleep, self.log = grab, ocr_text, now, sleep, log
        self.guard_check = guard_check
        self.bank_find = bank_find
        self.timing_path = timing_path

    # ---- 타이밍 기억 ----
    def _timing(self):
        try:
            from .crypto import load_json
            return load_json(self.timing_path)                   # v1.6(t850u): 암호화 저장
        except Exception:  # noqa: BLE001
            return {}

    def typical(self, key):
        t = self._timing().get(key)
        if not t or not t.get("count"):
            return 0.0
        return float(t["sum"]) / float(t["count"])

    def record(self, key, waited):
        if not self.timing_path or not key:
            return
        d = self._timing()
        e = d.get(key) or {"count": 0, "sum": 0.0, "max": 0.0}
        e["count"] += 1
        e["sum"] = float(e["sum"]) + float(waited)
        e["max"] = max(float(e["max"]), float(waited))
        d[key] = e
        try:
            os.makedirs(os.path.dirname(self.timing_path) or ".", exist_ok=True)
            from .crypto import save_json
            save_json(self.timing_path, d)
        except OSError:
            pass

    # ---- 판단 ----
    def _expect_seen(self, im, expect, box, bank_els):
        if expect:
            txt = self.ocr_text(im, box) if box else self.ocr_text(im, (0, 0) + im.size)
            ns = txt.replace(" ", "")
            if any(w.replace(" ", "") in ns for w in expect):
                return True
        if bank_els and self.bank_find:
            for el in bank_els:
                if self.bank_find(im, el):
                    return True
        if not expect and not bank_els:
            return True          # 기대값 없으면 '안정'만으로 완료 판단
        return False

    def _rows(self, im, rows_fn):
        try:
            return len(rows_fn(im)) if rows_fn else None
        except Exception:  # noqa: BLE001
            return None

    def settle(self, expect=None, box=None, bank_els=None, caps=None, rows_fn=None, key=""):
        """돌려줌 (state, waited, info). state: done | loading_timeout | frozen.

        caller 는 frozen/loading_timeout 이면 page_guard 재확인·멈춤을 처리한다."""
        c = caps or DEFAULTS
        t0 = self.now()
        floor = min(self.typical(key) * 1.3, c["load_max_sec"]) if key else 0.0   # 전에 걸린 평균 아래론 멈춤 판정 안 함
        last = None
        stable = 0
        last_rows = None
        rows_stable = 0
        last_change = t0
        waited = 0.0
        while True:
            im = self.grab()
            thumb = _thumb(im)
            text = self.ocr_text(im, (0, 0) + im.size) or ""
            low = text.lower().replace(" ", "")
            loading_word = any(w.replace(" ", "").lower() in low for w in LOADING_WORDS)
            # 빈/반 그려진 화면: 내용 픽셀도 적고 글자도 거의 없을 때만(로딩). 글자가 많으면 로딩 아님.
            blank_like = _content_ratio(thumb) < c["min_content"] and len(text.strip()) < 3
            changed = last is not None and _mean_abs_diff(thumb, last) > c["same_eps"]
            if changed:
                last_change = self.now()
            last = thumb
            stable = stable + 1 if not changed else 0
            rc = self._rows(im, rows_fn)
            if rc is not None:
                rows_stable = rows_stable + 1 if rc == last_rows else 0
                last_rows = rc
            seen = self._expect_seen(im, expect, box, bank_els)
            waited = self.now() - t0
            still = self.now() - last_change        # 마지막 변화 뒤 흐른 시간

            # (2) 다 됨
            rows_ok = (rows_fn is None) or (rows_stable >= 1)      # 줄 수 2번 연속 같음
            if seen and stable + 1 >= c["settle_frames"] and still >= c["settle_sec"] and rows_ok and not blank_like:
                self.record(key, waited)
                self.log("screen_done", key=key, waited=round(waited, 1))
                return "done", waited, {"rows": last_rows}

            # (1) 로딩 중 → 더 기다림(상한까지)
            loading = loading_word or blank_like or changed or still < c["settle_sec"]
            if loading and waited < c["load_max_sec"]:
                self.sleep(c["interval"])
                continue
            if loading and waited >= c["load_max_sec"]:
                self.log("screen_loading_timeout", key=key, waited=round(waited, 1))
                return "loading_timeout", waited, {"rows": last_rows}

            # (3) 멈춤 후보: 로딩 표시 없이 그대로인데 기대 없음
            if still >= max(c["frozen_sec"], floor) and not seen:
                self.log("screen_frozen_suspect", key=key, waited=round(waited, 1), still=round(still, 1))
                return "frozen", waited, {"rows": last_rows}

            if waited >= c["load_max_sec"]:
                return ("done", waited, {"rows": last_rows}) if seen else ("frozen", waited, {"rows": last_rows})
            self.sleep(c["interval"])
