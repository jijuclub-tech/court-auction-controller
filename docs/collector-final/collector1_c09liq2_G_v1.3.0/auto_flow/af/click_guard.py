# -*- coding: utf-8 -*-
"""t747u/t749u 인증서 클릭 차단(하드 가드). 모든 피코2 클릭 직전에 목표 둘레 글자를 OCR로 읽는다.

막는 것(되돌릴 수 없음, 호출자가 safe=False 를 줘도 막음):
  1) 목표 둘레(가로 +-60, 세로 +-16 px) 글자에 '인증서/공동인증/금융인증/간편인증/결제/발급' 이 있고 '아니요' 가 없을 때
  2) 목표 글자가 '예' 하나뿐이고 넓은 둘레에 '인증서' 가 있을 때(인증서 프로그램 설치 '예')
  3) t753u 로그인 화면 허용목록: 인증서 탭이 켜져 있으면 '아이디/비밀번호' 탭과 '아니요'만, 설치 창이 떠 있으면 '아니요'만,
     아이디/비밀번호 탭이 켜진 뒤에는 로그인 카드(아이디칸·비번칸·가상키패드·로그인 버튼) 안만 누름
     (예전 로그인 스크립트의 '비번칸(900,666)' 은 인증서 탭에서는 파란 '로그인' = 인증서 로그인 버튼이었다)
막히면: 기록 ev=blocked_cert_click, 화면 사진 저장, 수집기 폴더에 STOP 파일, CertClickBlocked 예외.
OCR 이 실패하면 클릭하지 않는다(fail closed).
'열람'은 감정평가서 '열람하기' 흐름에 꼭 필요해서 이 가드에서는 막지 않는다(조각그림 금지목록에서만 표시).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time

FORBIDDEN = ("인증서", "공동인증", "금융인증", "간편인증", "결제", "발급")
ALLOW_IF = ("아니요", "아니오")
NEAR_W, NEAR_H = 60, 16
WIDE_W, WIDE_H = 320, 200

# 통합로그인(PSP101M01) 1920x1080 100%. 스크롤바 유무로 몇 px 밀려서 여유를 둔다.
CERT_TAB_BOX = (620, 312, 950, 350)
IDPW_TAB_BOX = (960, 308, 1310, 356)
TITLE_BOX = (800, 230, 1110, 280)

DEFAULT_KH = r"D:\BIDGUARD\local_data\collector_knowhow"
DEFAULT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CertClickBlocked(RuntimeError):
    pass


def compact(s):
    return "".join(str(s or "").split())


def _mean(img, box):
    c = img.convert("RGB").crop(box).resize((24, 6))
    px = [c.getpixel((i, j)) for j in range(c.height) for i in range(c.width)]
    n = len(px) or 1
    return tuple(sum(p[i] for p in px) / n for i in range(3))


def _dark_navy(m):
    return m[0] < 80 and m[1] < 90 and 50 <= m[2] < 130 and m[2] > m[0] + 20


def _light(m):
    return min(m) > 200


LOGIN_CARD_BOX = (600, 300, 1320, 1000)      # 아이디/비밀번호 탭이 켜진 뒤: 아이디칸·비번칸·가상키패드·로그인 버튼


def login_state(img):
    """통합로그인 화면 상태: 'cert'(인증서 탭 켜짐) | 'idpw'(아이디/비밀번호 탭 켜짐) | 'modal'(창 때문에 어두움) | None."""
    try:
        c = _mean(img, (640, 318, 700, 345))
        i = _mean(img, (980, 318, 1060, 345))
    except Exception:  # noqa: BLE001
        return None
    if _dark_navy(c) and _light(i):
        return "cert"
    if _dark_navy(i) and _light(c):
        return "idpw"
    if 60 <= c[0] <= 115 and 70 <= c[1] <= 120 and 95 <= c[2] <= 150 and c[2] > c[0] + 15 and 215 <= min(i) and max(i) <= 242:
        return "modal"
    return None


def cert_tab_active(img, ocr=None):
    return login_state(img) == "cert"


def idpw_tab_active(img):
    return login_state(img) == "idpw"


def _in(box, x, y, pad=0):
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


def decide(near_text, wide_text="", state=None, x=None, y=None):
    """순수 판정 (ok, why). t753u 허용목록: 로그인 화면에서는 정해진 곳만 누른다."""
    n = compact(near_text)
    w = compact(wide_text)
    has_no = any(a in n for a in ALLOW_IF)
    if state is True:
        state = "cert"
    if state in ("cert", "modal", "idpw") and x is not None:
        if state == "modal":
            return (True, "cert_prompt_no") if has_no else (False, "login_modal_only_no")
        if state == "cert":
            if _in(IDPW_TAB_BOX, x, y) and ("아이디" in n or "비밀번호" in n):
                return True, "idpw_tab"
            if has_no:
                return True, "cert_prompt_no"
            return False, "login_whitelist_only_idpw_tab"
        # idpw: 로그인 카드 안(아이디칸·비번칸·가상키패드·로그인 버튼)만, 금지 글자는 그대로 막음
        if not _in(LOGIN_CARD_BOX, x, y):
            return False, "login_whitelist_outside_card"
    for f in FORBIDDEN:
        if f in n and not has_no:
            return False, "forbidden_text:" + f
    if n in ("예", "예.", "[예]") and ("인증서" in w or "설치하시겠" in w):
        return False, "cert_install_yes"
    return True, "ok"


def _boxes(x, y, size):
    W, H = size
    nb = (max(0, x - NEAR_W), max(0, y - NEAR_H), min(W, x + NEAR_W), min(H, y + NEAR_H))
    wb = (max(0, x - WIDE_W), max(0, y - WIDE_H), min(W, x + WIDE_W), min(H, y + WIDE_H))
    return nb, wb


def winocr_text(img, box, scale=2):
    import winocr  # type: ignore
    c = img.crop(box)
    c = c.resize((c.width * scale, c.height * scale))
    r = winocr.recognize_pil_sync(c, "ko")
    return r.get("text", "") if isinstance(r, dict) else ""


class ClickGuard:
    """grab() -> PIL 화면, ocr(img, box) -> 글자. log(rec) 선택."""

    def __init__(self, grab, ocr=None, log=None, kh_dir=None, stop_root=None, write_stop=True):
        self.grab = grab
        self.ocr = ocr or winocr_text
        self.log = log or (lambda rec: None)
        self.kh = kh_dir or os.environ.get("BG_KH_DIR") or DEFAULT_KH
        self.stop_root = stop_root or os.environ.get("BG_GUARD_STOP_ROOT") or DEFAULT_ROOT
        self.write_stop = write_stop
        self.last_ok = None            # (x, y, t) 방금 통과한 클릭(아래층 중복 검사 생략용)
        self.checks = 0

    def check(self, x, y, note=""):
        x, y = int(x), int(y)
        self.checks += 1
        try:
            img = self.grab()
            nb, wb = _boxes(x, y, img.size)
            near = self.ocr(img, nb)
            ct = login_state(img)
            wide = self.ocr(img, wb) if compact(near) in ("예", "예.", "[예]") else ""
        except CertClickBlocked:
            raise
        except Exception as e:  # noqa: BLE001
            self._block(None, x, y, note, "guard_ocr_error:%s" % type(e).__name__, "", stop=False)
            return
        ok, why = decide(near, wide, ct, x, y)
        if not ok:
            self._block(img, x, y, note, why, near)
        self.last_ok = (x, y, time.time())
        return why

    def recently_ok(self, x, y, tol=10, sec=8.0):
        lo = self.last_ok
        return bool(lo) and abs(lo[0] - x) <= tol and abs(lo[1] - y) <= tol and time.time() - lo[2] <= sec

    def _block(self, img, x, y, note, why, near, stop=True):
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        shot = None
        try:
            if img is not None:
                d = os.path.join(self.kh, "screens")
                os.makedirs(d, exist_ok=True)
                shot = os.path.join(d, "blocked_cert_click_%s.png" % ts)
                img.save(shot)
        except Exception:  # noqa: BLE001
            shot = None
        rec = {"ev": "blocked_cert_click", "why": why, "x": x, "y": y, "note": str(note)[:60],
               "near_text": compact(near)[:40], "shot": shot, "t": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        try:
            os.makedirs(self.kh, exist_ok=True)
            with open(os.path.join(self.kh, "blocked_clicks.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass
        if stop and self.write_stop:
            try:
                open(os.path.join(self.stop_root, "STOP"), "a", encoding="utf-8").write(
                    "%s blocked_cert_click %s\n" % (rec["t"], why))
            except Exception:  # noqa: BLE001
                pass
        try:
            self.log(dict(rec, action="blocked_cert_click"))
        except Exception:  # noqa: BLE001
            pass
        raise CertClickBlocked("blocked_cert_click: %s at %d,%d (%s)" % (why, x, y, note))
