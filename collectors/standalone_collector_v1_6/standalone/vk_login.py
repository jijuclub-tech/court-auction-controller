# -*- coding: utf-8 -*-
"""가상키패드 비밀번호 입력 (검수 H3, 규칙 court_vk_login_success_20261117).

iros·법원처럼 가상키패드가 필요한 사이트는 비밀번호를 **키보드로 치지 않는다**. 화면 키패드의
글쇠를 피코2로 눌러 넣고, 별표(●/*) 수를 세어 비밀번호 길이와 맞을 때만 제출한다.
- 모두지움 뒤 별표 0 확인.
- 글쇠당 1번, 2자마다 별표 수 확인, 수가 안 맞으면 제출 안 하고 멈춤(사람 호출).
- 글쇠를 못 찾으면(배열이 다름 등) 멈춤(짐작 클릭 금지).
- 비밀번호 글자는 기록·화면사진·예외 글에 남기지 않는다(글자 수만).
화면 조작·OCR·별표세기·글쇠찾기는 모두 넘겨받은 함수로 한다(시험 가능).
"""
from __future__ import annotations

SHIFT_LABELS = ("shift", "Shift", "SHIFT", "⇧", "대문자")


def _needs_shift(ch):
    return ch.isalpha() and ch.isupper()


def _key_label(ch):
    """글쇠 라벨(소문자/대문자 구분 없이 글쇠는 보통 대문자 표기). None 이면 특수문자 등 라벨 추정 불가."""
    if ch.isalpha():
        return ch.upper()
    if ch.isdigit():
        return ch
    return ch     # 기호는 그 글자 그대로 라벨에서 찾아봄


class VKResult:
    def __init__(self, status, entered=0, expected=0, why=""):
        self.status, self.entered, self.expected, self.why = status, entered, expected, why

    @property
    def ok(self):
        return self.status == "ok"


def type_password(pw, *, click_key, count_stars, clear, find_key, log=lambda *a, **k: None,
                  on_mismatch=None, settle=lambda: None, check_every=2,
                  same_char_pause=lambda: None, jitter_px=5):
    """pw: Secret(reveal()) 또는 str. 함수들:
      clear() : '모두지움' 누르고 True/False
      count_stars() -> int : 현재 별표 수
      find_key(label) -> (x,y) | None : 가상키패드에서 글쇠 라벨 위치
      click_key(x,y,note) : 그 자리 누르기
    반환 VKResult. status: ok | stop. 'ok' 뒤 제출은 호출자가 한다(별표 수 확인 끝난 상태)."""
    s = pw.reveal() if hasattr(pw, "reveal") else str(pw)
    n = len(s)
    if not clear():
        return VKResult("stop", 0, n, "clear_not_found")
    settle()
    if count_stars() != 0:
        return VKResult("stop", 0, n, "not_cleared")
    entered = 0
    shift_on = False
    import random
    prev = None
    for i, ch in enumerate(s):
        label = _key_label(ch)
        if _needs_shift(ch) and not shift_on:
            sk = next((find_key(l) for l in SHIFT_LABELS if find_key(l)), None)
            if not sk:
                return VKResult("stop", entered, n, "shift_not_found")
            click_key(sk[0], sk[1], "vk shift")
            shift_on = True
            settle()
        pos = find_key(label)
        if not pos:
            return VKResult("stop", entered, n, "key_not_found")   # 글자는 기록 안 함
        x, y = pos[0], pos[1]
        if ch == prev:
            # 같은 글자가 이어지면: 잠깐 쉬고 살짝 비켜 눌러 '한 번 더 눌림'으로 인식되게(검수 H3)
            same_char_pause()
            if jitter_px:
                # 최소 4px 은 움직이게(0px 비켜누르기 방지). 기본 4~7px.
                lo = min(4, jitter_px)
                x += random.choice((-1, 1)) * random.randint(lo, max(lo, jitter_px))
                y += random.choice((-1, 1)) * random.randint(lo, max(lo, jitter_px))
        click_key(x, y, "vk key")
        prev = ch
        entered += 1
        settle()
        if _needs_shift(ch):           # 대부분 키패드는 한 글자 뒤 shift 해제
            shift_on = False
        if entered % check_every == 0 or entered == n:
            seen = count_stars()
            log("vk_star_check", entered=entered, stars=seen)
            if seen != entered:
                if on_mismatch:
                    on_mismatch(entered, seen)
                return VKResult("stop", entered, n, "star_mismatch:%d!=%d" % (seen, entered))
    if count_stars() != n:
        return VKResult("stop", entered, n, "final_star_mismatch")
    return VKResult("ok", entered, n)
