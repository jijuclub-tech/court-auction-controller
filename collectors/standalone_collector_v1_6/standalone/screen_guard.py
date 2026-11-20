# -*- coding: utf-8 -*-
"""화면이 바뀔 때마다 부르는 확인기 (문제 1, 2, 6, 7).

AI 없이 error_codes.json 표만 본다. 결과는 Decision 으로 돌려주고,
실제 행동(로그아웃·쉼·멈춤)은 runner 가 한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .page_guard import PageGuard

# 숫자 캡차(보안문자)와 그림 고르기 캡차를 나눈다.
NUMBER_CAPTCHA_WORDS = ("보안문자", "자동입력방지", "자동입력 방지", "문자를 입력", "숫자를 입력")
ROBOT_CHECK_WORDS = ("로봇이 아닙니다", "로봇이아닙니다", "not a robot", "캡차를 체크", "reCAPTCHA")
IMAGE_CAPTCHA_WORDS = ("이미지를 모두 선택", "이미지를모두선택", "Select all images", "그림문제")
# 즉시 멈춤 (문제 7). 표에도 있지만 표가 바뀌어도 꼭 멈추게 한 번 더 확인.
DANGER_WORDS = ("공동인증서", "금융인증서", "인증서 선택", "인증서 암호", "결제하기", "카드결제", "수수료 결제",
                "발급하기", "발급 신청", "열람발급", "인증서 로그인")

# 로그인 화면에는 '인증서 로그인'·'공동인증서' 탭 글자가 늘 보인다. 로그인 단계에서는 탭 글자만으로 멈추지 않고
# 실제 인증서 창(선택·암호)·결제·발급만 멈춘다. 탭 클릭은 ClickGuard 가 따로 막는다(문제 13).
LOGIN_TAB_OK = ("인증서 로그인", "공동인증서", "금융인증서")
LOGIN_DANGER = tuple(w for w in DANGER_WORDS if w not in LOGIN_TAB_OK)


def _ns(s):
    return "".join(str(s or "").split())


@dataclass
class Decision:
    kind: str                 # ok | stop | rate_limit | rest | relogin | back | captcha_number | captcha_human | wait_queue | network
    code: str = ""
    action: str = ""
    why: str = ""
    logout: bool = False
    rest_min: float = 0.0
    write_stop: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def ok(self):
        return self.kind == "ok"


class ScreenGuard:
    def __init__(self, errors_json, site="court"):
        self.pg = PageGuard(errors_json)
        self.site = site
        self.counts = {}          # 연속 횟수(같은 회차)
        self.no_save_in_row = 0

    def _bump(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def reset_consecutive(self):
        for k in ("PAGE_SKIP", "CLIENT_ERROR_BACK"):
            self.counts.pop(k, None)

    def check_text(self, text, title=None, ignore=(), login_phase=False):
        ns = _ns(text) + _ns(title)
        if login_phase and not any(_ns(w) in ns for w in ("인증서 선택", "인증서 암호")):
            ignore = tuple(ignore) + ("CERT_LOGIN_PAGE",)
        # 7) 인증서·결제·발급 화면: 무조건 멈춤
        for w in (LOGIN_DANGER if login_phase else DANGER_WORDS):
            if _ns(w) in ns:
                return Decision("stop", "DANGER_SCREEN", "STOP_REPORT", "danger:" + w, write_stop=True)
        # 6) 다른 봇 확인(체크박스 reCAPTCHA·Cloudflare·그림 고르기)은 풀지 않고 멈춤.
        if any(_ns(w).lower() in ns.lower() for w in ROBOT_CHECK_WORDS):
            return Decision("captcha_human", "RECAPTCHA_CHECKBOX", "HUMAN_REQUIRED", "robot_check", write_stop=True)
        if any(_ns(w) in ns for w in IMAGE_CAPTCHA_WORDS):
            return Decision("captcha_human", "CAPTCHA_IMAGE", "HUMAN_REQUIRED", "image_captcha", write_stop=True)
        hits = self.pg.scan_text(text, title, self.site, ignore)
        if not hits:
            return Decision("ok")
        code, action, why = hits[0]
        if code == "CAPTCHA_IMAGE":
            # 숫자 그림 캡차(보안문자)는 t761u 규칙대로 풀어 본다. 그림 고르기는 위에서 이미 멈춤.
            if any(_ns(w) in ns for w in NUMBER_CAPTCHA_WORDS):
                return Decision("captcha_number", code, "CAPTCHA_NUMBER", why)
            return Decision("captcha_human", code, "HUMAN_REQUIRED", why, write_stop=True)
        return self.decide(code, action, why)

    def decide(self, code, action, why=""):
        a = self.pg.plan(action)
        if action == "RATE_LIMIT":
            return Decision("rate_limit", code, action, why, logout=True, rest_min=a.get("rest_min", 120))
        if action in ("STOP_REPORT", "FORCED_LOGOUT"):
            return Decision("stop", code, action, why, write_stop=True)
        if action == "BLOCK_STOP_REST":
            return Decision("stop", code, action, why, write_stop=True, rest_min=a.get("rest_min", 180))
        if action == "QUEUE_WAIT":
            return Decision("wait_queue", code, action, why, extra={"wait_max_min": a.get("wait_max_min", 10)})
        if action in ("SERVER_ERROR_WAIT", "MAINTENANCE_WAIT"):
            n = self._bump("SERVER")
            if n > 1:
                return Decision("stop", code, action, why + " (두번째)", logout=True)
            return Decision("rest", code, action, why, logout=True, rest_min=a.get("rest_min", 30))
        if action == "NETWORK_WAIT":
            n = self._bump("NETWORK")
            secs = a.get("rest_sec", [60, 300])
            if n > len(secs):
                return Decision("stop", code, action, why + " (반복)")
            return Decision("network", code, action, why, extra={"wait_sec": secs[n - 1]})
        if action == "SESSION_RELOGIN":
            n = self._bump("RELOGIN")
            if n > 1:
                return Decision("stop", code, action, why + " (재로그인 반복)")
            return Decision("relogin", code, action, why)
        if action in ("PAGE_SKIP", "CLIENT_ERROR_BACK"):
            n = self._bump(action)
            lim = a.get("consecutive_stop", 3 if action == "PAGE_SKIP" else 2)
            if n >= lim:
                return Decision("stop", code, action, why + " (연속%d)" % n)
            return Decision("back", code, action, why)
        return Decision("ok", code, action, why)      # KNOWN_HANDLER / INFO

    def check(self, im, ocr_text_fn, title=None, ignore=(), boxes=None, login_phase=False):
        W, H = getattr(im, "size", (1920, 1080))
        boxes = boxes or [(0, 0, W, int(H * 0.22)), (int(W * 0.08), int(H * 0.15), int(W * 0.92), int(H * 0.92))]
        text = "\n".join(ocr_text_fn(im, b) or "" for b in boxes)
        return self.check_text(text, title, ignore, login_phase)

    # 문제 2: 저장 창이 안 뜸
    def on_save_result(self, appeared: bool, suspect_in_row=2):
        """저장 창 결과. 돌려줌: 'ok' | 'suspect_rest'(2번 연속) | 'rate_limit'(3번째도 실패)."""
        if appeared:
            self.no_save_in_row = 0
            return "ok"
        self.no_save_in_row += 1
        if self.no_save_in_row > suspect_in_row:
            return "rate_limit"
        if self.no_save_in_row == suspect_in_row:
            return "suspect_rest"
        return "skip"


def default_errors_json(kb_dir):
    return os.path.join(kb_dir, "errors", "error_codes.json")
