# -*- coding: utf-8 -*-
"""Hands. The ONLY live implementation is PicoActuator (Pico2 S-0.2.14 via the collector's PicoInput:
MOVE_TO / HOVER / CLICK_DWELL / WHEEL / KEY / TYPE; human-speed paths are made by the firmware).
No JS click, no Selenium/Playwright, no SendInput/virtual mouse, no URL typing.

Shared rules (all actuators):
  * menu-band safe path: never sweep across the blue top menu (y 302..391) left of x=1400; go via (15,180)
    and the left edge, like the manual runs (replay_item._needs_detour).
  * no-click zones (e.g. a reCAPTCHA box) are refused even if a caller asks.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path
from typing import List, Optional, Tuple

MENU_Y0, MENU_Y1 = 302, 391
LEFT_SAFE = (15, 180)
PARK_REST = (15, 800)


class RefusedAction(RuntimeError):
    pass


class CapabilityMissing(RefusedAction):
    """The connected firmware lacks a command (e.g. DRAG on 1.0.2-FIX). Raised BEFORE any input is sent."""


# US-QWERTY HID combos for printable ASCII. Names match Pico KEY,combo.
_SHIFT_DIGIT = {'!':'SHIFT+1','@':'SHIFT+2','#':'SHIFT+3','$':'SHIFT+4','%':'SHIFT+5',
                '^':'SHIFT+6','&':'SHIFT+7','*':'SHIFT+8','(':'SHIFT+9',')':'SHIFT+0'}
_PUNCT = {'-':'MINUS','_':'SHIFT+MINUS','=':'EQUAL','+':'SHIFT+EQUAL',
          '[':'LEFTBRACKET','{':'SHIFT+LEFTBRACKET',']':'RIGHTBRACKET','}':'SHIFT+RIGHTBRACKET',
          '\\':'BACKSLASH','|':'SHIFT+BACKSLASH',';':'SEMICOLON',':':'SHIFT+SEMICOLON',
          "'":'QUOTE','"':'SHIFT+QUOTE',',':'COMMA','<':'SHIFT+COMMA','.':'DOT','>':'SHIFT+DOT',
          '/':'SLASH','?':'SHIFT+SLASH','`':'BACKQUOTE','~':'SHIFT+BACKQUOTE'}


def char_to_hid_key(ch: str):
    """Return a Pico KEY combo for one printable ASCII character, or None. Never logs the char."""
    if ch == ' ':
        return 'SPACE'
    if 'a' <= ch <= 'z':
        return ch.upper()
    if 'A' <= ch <= 'Z':
        return 'SHIFT+' + ch
    if '0' <= ch <= '9':
        return ch
    return _SHIFT_DIGIT.get(ch) or _PUNCT.get(ch)


# Printable KEY names to append to the firmware host allowlist (never remove existing keys).
HID_PRINTABLE_KEYS = (
    tuple('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    + tuple('0123456789')
    + tuple('SHIFT+' + c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    + tuple('SHIFT+' + d for d in '0123456789')
    + (
        'MINUS', 'SHIFT+MINUS', 'EQUAL', 'SHIFT+EQUAL',
        'LEFTBRACKET', 'SHIFT+LEFTBRACKET', 'RIGHTBRACKET', 'SHIFT+RIGHTBRACKET',
        'BACKSLASH', 'SHIFT+BACKSLASH', 'SEMICOLON', 'SHIFT+SEMICOLON',
        'QUOTE', 'SHIFT+QUOTE', 'COMMA', 'SHIFT+COMMA', 'DOT', 'SHIFT+DOT',
        'SLASH', 'SHIFT+SLASH', 'BACKQUOTE', 'SHIFT+BACKQUOTE',
    )
)



class Actuator:
    live = False

    def __init__(self, log=None, screen_size=(1920, 1080)):
        self.log = log or (lambda rec: None)
        self.size = screen_size
        self.no_click: List[Tuple[int, int, int, int]] = []
        self.history: List[dict] = []
        self.pos = (screen_size[0] // 2, screen_size[1] // 2)
        self.pre_action = None       # callable(verb, note): raises to stop BEFORE an input is sent (STOP files)
        self.click_guard = None      # t747u: af.click_guard.ClickGuard - OCR the target before EVERY click (no bypass)

    # ------------------------------------------------ zones
    def forbid(self, box, why=''):
        self.no_click.append(tuple(int(v) for v in box))
        self._rec({'action': 'forbid_zone', 'box': list(box), 'note': why})

    def clear_forbidden(self):
        self.no_click = []

    def _check_click(self, x, y):
        for (l, t, r, b) in self.no_click:
            if l - 6 <= x <= r + 6 and t - 6 <= y <= b + 6:
                raise RefusedAction('click at %d,%d is inside a no-click zone %s' % (x, y, (l, t, r, b)))
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1]):
            raise RefusedAction('click outside the screen: %d,%d' % (x, y))
        if y >= min(1008, self.size[1] - 40):  # Windows taskbar band (only taskbar_click may go there). c09: 1366x768 -> y>=728; 1920x1080 unchanged (1008)
            raise RefusedAction('click on the taskbar band y=%d refused (use taskbar_click)' % y)

    # ------------------------------------------------ path planning (pure)
    def path_to(self, x, y) -> List[Tuple[int, int]]:
        """Waypoints to (x, y) that never sweep across the blue menu band (opens the megamenu)."""
        cx, cy = self.cursor()
        x, y = int(x), int(y)
        pts: List[Tuple[int, int]] = []
        in_band = lambda yy: MENU_Y0 <= yy <= MENU_Y1  # noqa: E731
        if in_band(y):
            # hover a top menu: come straight up/down in the target's own column
            if not in_band(cy) and abs(cx - x) > 6:
                pts.append((x, MENU_Y1 + 60 if cy > MENU_Y1 else MENU_Y0 - 50))
        elif (cy < MENU_Y0 and y > MENU_Y1) or (cy > MENU_Y1 and y < MENU_Y0) or in_band(cy):
            if min(cx, x) < 1400:
                if in_band(cy):                       # leaving the band: go straight down/up first
                    pts.append((cx, MENU_Y1 + 60 if y > MENU_Y1 else MENU_Y0 - 50))
                    cy = pts[-1][1]
                if (cy < MENU_Y0) != (y < MENU_Y0):   # must cross: at the far-left edge only
                    pts += [(LEFT_SAFE[0], cy), (LEFT_SAFE[0], y)]
        pts.append((x, y))
        out: List[Tuple[int, int]] = []
        last = (cx, cy)
        for p in pts:
            if p != last:
                out.append(p)
            last = p
        return out or [(x, y)]

    # ------------------------------------------------ public verbs
    def _pre(self, verb, note=''):
        if self.pre_action is not None:
            self.pre_action(verb, note)

    def move(self, x, y, note=''):
        self._pre('move', note)
        self._do('move', x, y, note=note)

    def hover(self, x, y, ms=600, note=''):
        self._pre('hover', note)
        self._do('hover', x, y, ms=ms, note=note)

    def click(self, x, y, hover_ms=300, note='', allow_taskbar=False, safe=True):
        self._pre('click', note)
        if not allow_taskbar:
            self._check_click(x, y)
        else:
            for (l, t, r, b) in self.no_click:
                if l <= x <= r and t <= y <= b:
                    raise RefusedAction('no-click zone')
        if self.click_guard is not None:        # t747u: 인증서/결제/발급·로그인 허용목록 (safe=False 여도 적용)
            self.click_guard.check(int(x), int(y), note)
        self._do('click', x, y, ms=hover_ms, note=note, safe=safe)

    def taskbar_click(self, x, y, note=''):
        self.click(x, y, 400, note=note, allow_taskbar=True)

    def wheel(self, x, y, ticks, note=''):
        self._pre('wheel', note)
        self._do('wheel', x, y, ticks=ticks, note=note)

    def key(self, combo, note=''):
        self._pre('key', note)
        self._do('key', None, None, combo=combo, note=note)

    def type_text(self, text, note='', secret=False):
        s = str(text)
        if any(ord(ch) < 32 or ord(ch) > 126 for ch in s):
            raise RefusedAction('TYPE accepts printable ASCII only')
        if getattr(self, 'type_via_keys', False):
            # t734u: firmware without TYPE -> one KEY per character (never logs the text when secret)
            return self.type_hid_keys(s, note=note, secret=secret)
        self._pre('type', note)
        self._do('type', None, None, text=s, note=note, secret=bool(secret))

    def type_secret(self, text, note='secret field'):
        """TYPE printable ASCII but never put the characters in logs, history, or echo."""
        return self.type_text(text, note=note, secret=True)

    def type_hid_keys(self, text, note='', secret=False, min_delay=0.07, max_delay=0.18):
        """Send one Pico KEY event per character with short human delays.

        Never uses TYPE or paste (CTRL+V). When secret=True, combo/text are redacted
        in logs and echo. Raises RefusedAction if a character has no HID mapping
        (the character itself is never included in the error).
        """
        s = str(text)
        if any(ord(ch) < 32 or ord(ch) > 126 for ch in s):
            raise RefusedAction('HID keys accept printable ASCII only')
        combos = []
        for ch in s:
            combo = char_to_hid_key(ch)
            if not combo:
                raise RefusedAction('HID key mapping missing for a character (not logged)')
            combos.append(combo)
        n = len(combos)
        self._pre('key', note)
        for i, combo in enumerate(combos):
            self._do('key', None, None, combo=combo, note=note, secret=bool(secret), hid_index=i + 1, hid_n=n)
            if i + 1 < n:
                time.sleep(min_delay + (max_delay - min_delay) * random.random())
        return n

    def drag(self, x1, y1, x2, y2, note=''):
        """Press, move, release: Pico2 DRAG (scroll by drag)."""
        if not self.has('DRAG'):
            raise CapabilityMissing('firmware has no DRAG (use drag_or_skip)')
        self._pre('drag', note)
        self._check_click(int(x1), int(y1))
        self._check_click(int(x2), int(y2))
        if self.click_guard is not None:        # t747u: 누르는 곳(시작점)도 검사
            self.click_guard.check(int(x1), int(y1), note)
        self._do('drag', int(x2), int(y2), start=(int(x1), int(y1)), note=note)

    def has(self, cap):
        """Firmware capability (CAPS). Dry-run: everything except names listed in self.missing_caps."""
        return cap not in getattr(self, 'missing_caps', ())

    def drag_or_skip(self, x1, y1, x2, y2, note=''):
        """t734u: drag only when the firmware has DRAG; otherwise log a skip and return False (no input sent).
        Callers must treat False as 'nothing moved' (offsets 0)."""
        if not self.has('DRAG'):
            self._rec({'action': 'drag_skipped', 'why': 'firmware has no DRAG', 'note': note})
            return False
        self.drag(x1, y1, x2, y2, note=note)
        return True

    def park(self, x=PARK_REST[0], y=PARK_REST[1], note='park'):
        y = min(int(y), int(self.size[1]) - 120)  # c09: (15,800) is off a 768-high screen -> (15,648); 1080 unchanged
        self._do('move', x, y, note=note)

    def cursor(self):
        return self.pos

    def close(self):
        pass

    # ------------------------------------------------ internals
    def _rec(self, rec):
        rec.setdefault('at', time.strftime('%Y-%m-%d %H:%M:%S'))
        rec.setdefault('input', 'pico2' if self.live else 'dry-run')
        self.history.append(rec)
        self.log(rec)

    def _do(self, verb, x, y, **kw):
        raise NotImplementedError


class DryRunActuator(Actuator):
    """Prints the planned Pico2 moves; never touches a device. Used by --dry-run and the unit tests."""

    def __init__(self, log=None, screen_size=(1920, 1080), echo=True, start=(960, 540)):
        super().__init__(log, screen_size)
        self.echo = echo
        self.pos = start

    def _do(self, verb, x, y, **kw):
        safe = kw.pop('safe', True)
        path = (self.path_to(x, y) if safe else [(int(x), int(y))]) if x is not None else []
        rec = {'action': verb, 'target_px': [x, y] if x is not None else None, 'path': [list(p) for p in path]}
        rec.update({k: v for k, v in kw.items() if v not in (None, '')})
        if kw.get('secret'):
            if 'text' in rec:
                rec['text'] = '[redacted]'
            if 'combo' in rec:
                rec['combo'] = '[redacted]'
        if x is not None:
            self.pos = (int(x), int(y))
        self._rec(rec)
        if self.echo:
            tgt = '' if x is None else ' -> (%d,%d)' % (x, y)
            via = '' if len(path) <= 1 else ' via ' + ' '.join('(%d,%d)' % p for p in path[:-1])
            items = dict(kw)
            if items.get('secret'):
                if 'text' in items:
                    items['text'] = '[redacted]'
                if 'combo' in items:
                    items['combo'] = '[redacted]'
            extra = ' '.join('%s=%s' % (k, v) for k, v in items.items() if k not in ('note', 'secret') and v not in (None, ''))
            print('PLAN %-6s%s%s %s  # %s' % (verb.upper(), tgt, via, extra, kw.get('note', '')), flush=True)


class PicoActuator(Actuator):
    """Pico2 through the collector's HumanSession/PicoInput (opens the COM port: LIVE ONLY)."""
    live = True

    def __init__(self, base_dir, log=None, cfg=None):
        import sys
        sys.path.insert(0, str(base_dir))
        from core.court_live.human.setup import HumanSession  # type: ignore
        self.s = HumanSession(cfg or {}, base=str(base_dir))
        self.s.open_pico(need_calibration=True)
        size = tuple(self.s.screen.screen_size())
        super().__init__(log, size)
        link = self.s.inp.link
        self.fw_version = getattr(link, 'version', None)
        self.fw_caps = tuple(getattr(link, 'caps', ()) or ())
        self.fw_port = getattr(link, 'port', None)
        import os as _os
        # TYPE is not listed in CAPS on any firmware; set BG_TYPE_VIA_KEYS=1 after a test shows TYPE is refused.
        self.type_via_keys = _os.environ.get('BG_TYPE_VIA_KEYS') == '1'
        self._rec({'action': 'pico_connected', 'version': self.fw_version, 'port': self.fw_port,
                   'caps': ','.join(self.fw_caps), 'type_via_keys': self.type_via_keys})
        # t747u/t753u: 하드 가드는 항상 켠다(끄는 스위치 없음). 아래층 PicoInput.click 도 같은 가드를 부른다
        # (가상키패드처럼 inp 를 바로 쓰는 스크립트도 막힘). 방금 통과한 같은 자리는 다시 OCR 하지 않음.
        from .click_guard import ClickGuard
        scr = self.s.screen
        self.click_guard = ClickGuard(lambda: (scr.grab() if hasattr(scr, 'grab') else scr.screenshot()).convert('RGB'),
                                      log=self._rec)
        g = self.click_guard
        def _low(pos, _g=g):
            if not _g.recently_ok(int(pos[0]), int(pos[1])):
                _g.check(int(pos[0]), int(pos[1]), 'pico_input.click')
        try:
            self.s.inp.click_guard = _low
        except Exception:  # noqa: BLE001
            pass

    def has(self, cap):
        # Prefer the link's has() so RCLICK aliases RIGHT_CLICK and 1.0.x live probes are honoured.
        inp = getattr(self, 's', None)
        link = getattr(getattr(inp, 'inp', None), 'link', None)
        fn = getattr(link, 'has', None)
        if callable(fn):
            return bool(fn(cap))
        caps = tuple(self.fw_caps or ())
        if cap in ('RIGHT_CLICK', 'RCLICK'):
            return 'RCLICK' in caps or 'RIGHT_CLICK' in caps
        return cap in caps

    def cursor(self):
        return tuple(self.s.screen.cursor())

    def _do(self, verb, x, y, **kw):
        inp = self.s.inp
        safe = kw.pop('safe', True)
        path = (self.path_to(x, y) if safe else [(int(x), int(y))]) if x is not None else []

        def fn():
            if verb == 'drag':
                start = kw.get('start') or (x, y)
                for (px, py) in (self.path_to(int(start[0]), int(start[1])) if safe else [(int(start[0]), int(start[1]))]):
                    inp.move_px(px, py)
                if not hasattr(inp, 'drag_px'):
                    raise RefusedAction('pico drag_px not available')
                inp.drag_px(int(x), int(y))
                return
            for (px, py) in path:
                inp.move_px(px, py)
            if verb == 'hover':
                inp.hover(int(kw.get('ms') or 600))
            elif verb == 'click':
                inp.hover(int(kw.get('ms') or 300))
                inp.click()
            elif verb == 'wheel':
                inp.hover(150)
                inp.wheel(int(kw['ticks']))
            elif verb == 'key':
                inp.key(kw['combo'])
            elif verb == 'type':
                inp.type_text(kw['text'])

        t0 = time.time()
        inp.act(fn, verb)
        rec = {'action': verb, 'target_px': [x, y] if x is not None else None,
               'path': [list(p) for p in path], 'wait_sec': round(time.time() - t0, 3),
               'cursor_after': list(self.cursor())}
        rec.update({k: v for k, v in kw.items() if v not in (None, '')})
        if kw.get('secret'):
            if 'text' in rec:
                rec['text'] = '[redacted]'
            if 'combo' in rec:
                rec['combo'] = '[redacted]'
        self._rec(rec)

    def close(self):
        try:
            self.s.close()
        except Exception:  # noqa: BLE001
            pass
