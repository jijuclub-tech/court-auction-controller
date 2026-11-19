# -*- coding: utf-8 -*-
"""법원 홈 공지 팝업 닫기 (문제 5, 2호 대전 수정 c2_close_popup.py 방식).

팝업마다 '오늘 하루 열지 않음(보지 않기)' 체크 → 같은 줄(세로 80px 안)의 '닫기'. 팝업 2개면 2번.
금지 글자(인증서·결제·발급·설치·로그인·즐겨찾기) 줄은 누르지 않는다. AI 없음.
"""
from __future__ import annotations

BAD = ("인증서", "인증", "결제", "발급", "설치", "로그인", "즐겨찾기")
TODAY = ("오늘하루", "오늘은그만", "열지않", "보지않")
CLOSE = ("닫기", "닫기X", "닫기x", "X닫기", "[닫기]", "창닫기")


def _ns(s):
    return "".join(str(s or "").split())


def plan(lines):
    """lines: [(글자, x, y)] → 누를 순서 [(x, y, note), ...] (팝업 하나 분량) 또는 []."""
    today = [(t, x, y) for t, x, y in lines
             if any(w in _ns(t) for w in TODAY) and not any(b in _ns(t) for b in BAD)]
    if not today:
        return []
    t, x, y = sorted(today, key=lambda r: (r[2], r[1]))[0]
    out = [(x, y, "popup today check")]
    near = sorted((abs(cy - y), abs(cx - x), cx, cy) for ct, cx, cy in lines
                  if _ns(ct) in CLOSE and abs(cy - y) <= 80)
    if near:
        out.append((near[0][2], near[0][3], "popup close same row"))
    else:
        out.append(("ESC", None, "popup esc (no close btn)"))
    return out


def close_all(grab, ocr_lines, click, key, log=lambda *a, **k: None, max_popups=3, settle=lambda: None):
    """팝업이 없어질 때까지(최대 3개). 남으면 False."""
    for i in range(max_popups + 1):
        steps = plan(ocr_lines(grab()))
        if not steps:
            log("popup_done", closed=i)
            return True
        if i == max_popups:
            break
        for x, y, note in steps:
            if x == "ESC":
                key("ESC", note)
            else:
                click(x, y, note)
            settle()
    log("popup_left", tries=max_popups)
    return False
