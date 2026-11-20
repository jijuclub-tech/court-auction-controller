# -*- coding: utf-8 -*-
"""위치 찾기 순서 (문제 4, 9): ① 조각그림(순위 순, 넓은 띠) ② 화면 글자(OCR 단어) ③ AI(딥시크→제미나이, 최대 2라운드).
찾으면 그 자리 글자가 금지어(인증서·결제·발급 등)가 아닌지 확인한다. ①②로 찾은 위치는 조각그림으로 배워 다음엔 ①로 간다.
"""
from __future__ import annotations

FORBID_DEFAULT = ("인증서", "공동인증", "금융인증", "간편인증", "결제", "발급")


def _ns(s):
    return "".join(str(s or "").split())


def rbox(box_r, size):
    W, H = size
    return (int(box_r[0] * W), int(box_r[1] * H), int(box_r[2] * W), int(box_r[3] * H))


class Locator:
    def __init__(self, bank, ocr_words_fn, ai=None, log=lambda *a, **k: None, router=None, budget=None, now=None):
        """ocr_words_fn(im, box) -> [(글자, x, y), ...] 화면 좌표.
        v1.6(t845u): router(판단 담당표)·budget(시간 예산)이 있으면 담당 순서대로 보내고 걸린 시간을 기록."""
        import time as _t
        self.bank, self.ocr_words, self.ai, self.log = bank, ocr_words_fn, ai, log
        self.router, self.budget, self.now = router, budget, now or _t.time
        self.last_event = None

    def find(self, im, el, words=(), box_r=(0.0, 0.05, 1.0, 0.5), avoid=(), forbid=FORBID_DEFAULT,
             goal=None, use_ai=True, learn=True):
        """(x, y, 방법) 또는 None. 판단 담당표 기록을 감싼다."""
        ev = "locate:" + str(el)
        self.last_event = ev
        t0 = self.now()
        self._tried = []
        r = self._find(im, el, words, box_r, avoid, forbid, goal, use_ai, learn, ev)
        sec = self.now() - t0
        how = r[2] if r else ""
        solved = "PROGRAM" if how.startswith(("snip:", "ocr:")) else ({"ai:local": "LOCAL_LLM", "ai:deepseek": "DEEPSEEK",
                                                                       "ai:gemini": "GEMINI"}.get(how))
        failed = (["PROGRAM"] if solved != "PROGRAM" else []) + [
            {"local": "LOCAL_LLM", "deepseek": "DEEPSEEK", "gemini": "GEMINI"}[a] for a in self._tried
            if {"local": "LOCAL_LLM", "deepseek": "DEEPSEEK", "gemini": "GEMINI"}[a] != solved]
        if self.router is not None:
            self.router.record(ev, solved, sec, failed)
        if self.budget is not None:
            self.budget.add_decision(solved or "UNSOLVED", sec)
        return r

    def _find(self, im, el, words, box_r, avoid, forbid, goal, use_ai, learn, ev):
        box = rbox(box_r, im.size)
        # ① 조각그림
        hit = self.bank.find(im, el, box) if self.bank else None
        if hit:
            near = _ns(" ".join(w[0] for w in self.ocr_words(im, (hit[0] - 60, hit[1] - 16, hit[0] + 60, hit[1] + 16))))
            fb = self._forbidden_near(im, hit[0], hit[1])
            if not any(f in near for f in forbid) and not fb:
                return hit[0], hit[1], "snip:" + hit[2]
            if fb:
                self.log("snip_forbidden_image", el=el, file=fb)
            self.log("snip_forbidden", el=el, near=near[:30])
        # ② 화면 글자 (단어 단위, 위에서부터)
        ws = self.ocr_words(im, box) or []
        cands = []
        for t, x, y in ws:
            n = _ns(t)
            if any(f in n for f in forbid) or any(_ns(a) in n for a in avoid):
                continue
            for pri, w in enumerate(words):
                if _ns(w) and _ns(w) in n:
                    cands.append((pri, len(n), y, x, t))
                    break
        if cands:
            cands.sort()
            _, _, y, x, t = cands[0]
            fb = self._forbidden_near(im, x, y)
            if fb:                                       # v1.5: 금지 그림(인증서 등) 근처면 거부
                self.log("ocr_forbidden_image", el=el, file=fb)
                return None
            if learn and self.bank:
                self.bank.learn(im, el, x, y)
            return x, y, "ocr:" + t
        # ③ AI
        if use_ai and self.ai and ws:
            safe = [w for w in ws if not any(f in _ns(w[0]) for f in forbid)]
            crop = im.crop(box)                      # 사진은 찾는 상자만 보냄 (검수 M6)
            off = [(t, x - box[0], y - box[1]) for t, x, y in safe]
            order = None
            if self.router is not None:                     # 담당표: 담당 아래 모델 단계는 건너뜀
                from .decision_routing import API_OF
                order = [API_OF[t] for t in self.router.plan(ev) if t in API_OF]
            r = self.ai.pick(goal or el, off, crop, order=order, tried=self._tried)
            if r:
                r = (r[0], r[1] + box[0], r[2] + box[1]) + tuple(r[3:])
                if learn and self.bank:
                    self.bank.learn(im, el, r[1], r[2])
                return r[1], r[2], "ai:" + r[3]
        if learn and self.bank and hasattr(self.bank, "note_fail"):
            try:
                self.bank.note_fail(el)                    # v1.5: 실패 많은 단계는 큰 맥락 그림을 더 저장
            except Exception:  # noqa: BLE001
                pass
        return None

    def _forbidden_near(self, im, x, y):
        fn = getattr(self.bank, "forbidden_near", None) if self.bank else None
        if fn is None:
            return None
        try:
            return fn(im, x, y)
        except Exception:  # noqa: BLE001
            return None
