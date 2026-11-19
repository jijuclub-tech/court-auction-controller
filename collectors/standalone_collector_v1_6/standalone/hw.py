# -*- coding: utf-8 -*-
"""손(피코2)과 눈(화면 사진·OCR·창 제목).

규칙: 마우스·키보드 입력은 피코2(펌웨어 1.0.x, COM8 기본)로만. Playwright·CDP·JS 클릭·법원 직접 HTTP 없음.
모든 클릭은 ClickGuard(인증서·결제·발급 차단, 로그인 화면 허용목록)를 먼저 지난다(문제 7, 13).
FakeHW 는 오프라인 시험용(실제 입력 없음).
"""
from __future__ import annotations

import os
import random
import sys
import time

_SHIFT_DIGIT = {'!': 'SHIFT+1', '@': 'SHIFT+2', '#': 'SHIFT+3', '$': 'SHIFT+4', '%': 'SHIFT+5',
                '^': 'SHIFT+6', '&': 'SHIFT+7', '*': 'SHIFT+8', '(': 'SHIFT+9', ')': 'SHIFT+0'}
_PUNCT = {'-': 'MINUS', '_': 'SHIFT+MINUS', '=': 'EQUAL', '+': 'SHIFT+EQUAL', '[': 'LEFTBRACKET',
          '{': 'SHIFT+LEFTBRACKET', ']': 'RIGHTBRACKET', '}': 'SHIFT+RIGHTBRACKET', '\\': 'BACKSLASH',
          '|': 'SHIFT+BACKSLASH', ';': 'SEMICOLON', ':': 'SHIFT+SEMICOLON', "'": 'QUOTE', '"': 'SHIFT+QUOTE',
          ',': 'COMMA', '<': 'SHIFT+COMMA', '.': 'DOT', '>': 'SHIFT+DOT', '/': 'SLASH', '?': 'SHIFT+SLASH',
          '`': 'BACKQUOTE', '~': 'SHIFT+BACKQUOTE'}


def char_to_key(ch):
    if ch == ' ':
        return 'SPACE'
    if 'a' <= ch <= 'z':
        return ch.upper()
    if 'A' <= ch <= 'Z':
        return 'SHIFT+' + ch
    if '0' <= ch <= '9':
        return ch
    return _SHIFT_DIGIT.get(ch) or _PUNCT.get(ch)


class InputRefused(RuntimeError):
    pass


def _ver_tuple(v):
    import re
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


class BaseHW:
    is_real = False   # 실기기(피코)만 True — 배경 NTP 등 바깥 요청은 실기기에서만
    """공통: OCR 단어, 창 제목. 하위 클래스가 grab/_click/_key/_hover/_move 를 채운다."""

    def __init__(self, log=lambda *a, **k: None):
        self.log = log
        self.before_input = None
        self.click_guard = None   # ClickGuard (runner 가 붙임)
        self.actions = []

    # ---- 눈 ----
    def ocr_words(self, im, box=None, scale=2):
        raise NotImplementedError

    def ocr_text(self, im, box):
        return " ".join(w[0] for w in self.ocr_words(im, box))

    def ocr_lines(self, im, box=None):
        return self.ocr_words(im, box)

    def title(self):
        return ""

    # ---- 손 (안전 검사 뒤에만) ----
    def click(self, x, y, note="", jitter=(3, 2)):
        x = int(x) + random.randint(-jitter[0], jitter[0])
        y = int(y) + random.randint(-jitter[1], jitter[1])
        if self.before_input:
            self.before_input("click", note)
        if self.click_guard is not None:
            self.click_guard.check(x, y, note)      # 막히면 CertClickBlocked
        self.actions.append(("click", x, y, note))
        self._click(x, y, note)

    def hover(self, x, y, ms=700, note=""):
        if self.before_input:
            self.before_input("hover", note)
        self.actions.append(("hover", int(x), int(y), note))
        self._hover(int(x), int(y), ms)

    def move(self, x, y, note=""):
        if self.before_input:
            self.before_input("move", note)
        self.actions.append(("move", int(x), int(y), note))
        self._move(int(x), int(y))

    def wheel(self, x, y, ticks, note=""):
        """마우스를 (x,y) 로 옮긴 뒤 휠 |ticks| 칸(음수=아래). 피코2 WHEEL 만 씀(v1.5 C8)."""
        t = max(-10, min(10, int(ticks)))
        if self.before_input:
            self.before_input("wheel", note)
        self.actions.append(("wheel", int(x), int(y), t, note))
        self._wheel(int(x), int(y), t)

    def _wheel(self, x, y, ticks):
        raise NotImplementedError

    def key(self, combo, note=""):
        if self.before_input:
            self.before_input("key", note)
        self.actions.append(("key", combo, note))
        self._key(combo)

    def type_text(self, text, note="", secret=False):
        s = str(text)
        keys = []
        for ch in s:
            k = char_to_key(ch)
            if not k:
                raise InputRefused("입력할 수 없는 글자(기록 안 함)")
            keys.append(k)
        if self.before_input:
            self.before_input("type", note)
        self.actions.append(("type", "***" if secret else s, note))
        for i, k in enumerate(keys):
            if self.before_input:
                self.before_input("char", note)
            self._key(k)
            if i + 1 < len(keys):
                time.sleep(random.uniform(0.07, 0.18) if not getattr(self, "fast", False) else 0)
        return len(keys)

    def close(self):
        pass


class FakeHW(BaseHW):
    """시험용. screens: [(PIL 이미지, [(글자,x,y)...], 창제목)]; next_screen() 으로 넘김."""
    fast = True

    def __init__(self, screens, log=lambda *a, **k: None, advance_on_click=False):
        super().__init__(log)
        self.screens, self.i, self.adv = list(screens), 0, advance_on_click
        self.keys = []
        self.on_input = None      # 시험 각본: on_input(hw, kind, note)

    def cur(self):
        return self.screens[min(self.i, len(self.screens) - 1)]

    def next_screen(self):
        self.i += 1

    def grab(self):
        return self.cur()[0]

    def ocr_words(self, im, box=None, scale=2):
        ws = self.cur()[1]
        if box is None:
            return list(ws)
        return [w for w in ws if box[0] <= w[1] <= box[2] and box[1] <= w[2] <= box[3]]

    def title(self):
        return self.cur()[2] if len(self.cur()) > 2 else ""

    def _click(self, x, y, note):
        if self.adv:
            self.next_screen()
        if self.on_input:
            self.on_input(self, "click", note)

    def _hover(self, x, y, ms):
        pass

    def _move(self, x, y):
        pass

    def _wheel(self, x, y, ticks):
        if self.on_input:
            self.on_input(self, "wheel", ticks)

    def _key(self, k):
        self.keys.append(k)
        if self.on_input:
            self.on_input(self, "key", k)


class PicoHW(BaseHW):  # pragma: no cover - 노트북 전용(실기기)
    is_real = True
    """노트북: 피코2 + PIL.ImageGrab + winocr + 창 제목(user32)."""

    def __init__(self, cfg_pico, log=lambda *a, **k: None, base_dir=None):
        super().__init__(log)
        base = base_dir or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if base not in sys.path:
            sys.path.insert(0, base)
        from core.pico_fw1_link import PicoLinkFW1
        class PrivatePicoLink(PicoLinkFW1):
            def _audit(self, kind, result, detail=''):
                # 장치 응답도 KEY,A 등을 담는다. 모든 KEY/TYPE 응답은 값 대신 고정 글자만 기록.
                if kind in ("KEY", "TYPE", "SERIAL_UNMATCHED", "HEARTBEAT"):
                    detail = "[redacted]"
                return super()._audit(kind, result, detail)
        self.link = PrivatePicoLink(port_fallback=cfg_pico.get("fallback") or "COM8")
        self.link.connect(cfg_pico.get("port") or "AUTO")
        need = cfg_pico.get("min_build", "1.0.7")
        if _ver_tuple(self.link.version) < _ver_tuple(need):
            self.link.disconnect()
            raise RuntimeError("피코2 펌웨어 %s 는 %s 보다 낮아요" % (self.link.version, need))
        self.log("pico_connected", port=self.link.port, how=self.link.port_how, build=self.link.version)
        for _ in range(2):
            self.link.arm()
            t0 = time.monotonic()
            while not self.link.armed and time.monotonic() - t0 < 4:
                time.sleep(0.1)
            if self.link.armed:
                break
            time.sleep(1.2)
        if not self.link.armed:
            raise RuntimeError("피코2 ARM 실패")
        from PIL import ImageGrab
        self._grab = ImageGrab.grab
        import winocr  # noqa: F401
        self._winocr = winocr

    def grab(self):
        return self._grab()

    def ocr_words(self, im, box=None, scale=2):
        box = box or (0, 0) + im.size
        box = (max(0, int(box[0])), max(0, int(box[1])), min(im.size[0], int(box[2])), min(im.size[1], int(box[3])))
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            return []
        c = im.crop(box)
        c = c.resize((c.width * scale, c.height * scale))
        r = self._winocr.recognize_pil_sync(c, "ko")
        out = []
        for ln in r.get("lines", []) if isinstance(r, dict) else []:
            for w in ln.get("words", []):
                b = w["bounding_rect"]
                out.append((w.get("text", ""), box[0] + (b["x"] + b["width"] / 2) / scale,
                            box[1] + (b["y"] + b["height"] / 2) / scale))
            # 줄 전체도 하나 넣는다(여러 단어 문구 찾기용)
            ws = ln.get("words", [])
            if len(ws) > 1:
                xs = [box[0] + (w["bounding_rect"]["x"] + w["bounding_rect"]["width"] / 2) / scale for w in ws]
                ys = [box[1] + (w["bounding_rect"]["y"] + w["bounding_rect"]["height"] / 2) / scale for w in ws]
                out.append((ln.get("text", ""), sum(xs) / len(xs), sum(ys) / len(ys)))
        return out

    def title(self):
        import ctypes
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        return b.value

    def _move(self, x, y):
        self.link.move_to(x, y)

    def cursor_pos(self):
        import ctypes
        pt = ctypes.wintypes.POINT() if hasattr(ctypes, "wintypes") else None
        try:
            from ctypes import wintypes
            pt = wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            return (pt.x, pt.y)
        except Exception:  # noqa: BLE001
            return None

    def _hover(self, x, y, ms):
        self.link.move_to(x, y)
        self.link.hover(ms)

    def _click(self, x, y, note):
        self.link.move_to(x, y)
        self.link.click_dwell(random.randint(120, 260))

    def _key(self, k):
        self.link.key(k)

    def _wheel(self, x, y, ticks):
        self.link.move_to(x, y)
        self.link.wheel(ticks)

    def close(self):
        try:
            self.link.disarm()
            time.sleep(0.3)
            self.link.disconnect()
        except Exception:  # noqa: BLE001
            pass
