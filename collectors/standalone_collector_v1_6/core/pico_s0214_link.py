"""Pico 2 firmware S-0.2.14-HUMAN-MOVE serial link (additive; core/pico_s02_link.py is unchanged).

Same public surface and rules as PicoLinkS02 (single serial owner, no HID before ARM, never retry an HID command
on timeout, a late reply is not success, F8/emergency cancels pending work and DISARMs) plus the S-0.2.14 commands:

  CAPS                         -> 'CAPS,MOVE_TO,HOVER,CLICK_DWELL,MOVE_CFG,BOUNDS,WHERE,WHEEL,ABORT,KEY,DRAG,RIGHT_CLICK'
  WHERE                        -> 'WHERE,x,y'                 (0..10000 normalized)
  MOVE_TO,x,y[,fx,fy][,dur]    -> 'ACK:MOVE_TO,x,y,dur,points,peak,clamped'
  DRAG,x,y[,fx,fy][,dur]       -> 'ACK:DRAG,x,y,dur,points,peak,clamped'
  HOVER,ms                     -> 'ACK:HOVER,ms'
  CLICK_DWELL,pre[,hold]       -> 'ACK:CLICK_DWELL,pre,hold'
  RIGHT_CLICK[,pre[,hold]]     -> 'ACK:RIGHT_CLICK,pre,hold'
  WHEEL,ticks[,gap]            -> 'ACK:WHEEL,ticks'           (+ = up, - = down; -10..10)
  KEY,combo[,count]            -> 'ACK:KEY,COMBO,count' / 'ERR:KEY_NOT_ALLOWED,COMBO'
  MOVE_CFG,k=v.. / MOVE_CFG?   -> 'ACK:MOVE_CFG,...' / 'MOVE_CFG,...'
  BOUNDS,x0,y0,x1,y1 / RESET / BOUNDS? -> 'ACK:BOUNDS,...' / 'BOUNDS,...'
  ABORT                        -> 'ACK:ABORT' (idle) or 'ERR:MOVE_ABORTED,x,y' + 'ACK:ABORT' (while moving)
Refusals: ERR:BAD_ARGS[,d] ERR:OUT_OF_BOUNDS,x,y ERR:HOLD_HUMAN,r ERR:STORAGE_FULL ERR:NOT_ARMED ERR:BUSY_MOVING

Human takeover / F8: emergency_stop() writes STOP at once, WITHOUT waiting for the command lock, so a running
MOVE_TO / DRAG / KEY stops at the next point (firmware: ERR:MOVE_ABORTED,x,y then ACK:STOPPED,DISARMED).
"""
from __future__ import annotations

import threading
import time

from . import pico_s02_link as _old
from .pico_s02_link import PicoLinkS02

BUILD_S0214 = 'S-0.2.14-HUMAN-MOVE'
SUPPORTED_BUILDS = (BUILD_S0214,) + tuple(_old.SUPPORTED_BUILDS)
CAPS_EXPECTED = ('MOVE_TO', 'HOVER', 'CLICK_DWELL', 'MOVE_CFG', 'BOUNDS', 'WHERE', 'WHEEL', 'ABORT', 'KEY', 'DRAG',
                 'RIGHT_CLICK')
HID_KINDS = ('MOVE', 'CLICK', 'TYPE', 'MOVE_TO', 'DRAG', 'HOVER', 'CLICK_DWELL', 'RIGHT_CLICK', 'WHEEL', 'KEY')
# refusals that happen BEFORE any input: no reason to emergency-stop (the caller fixes its arguments)
NO_INPUT_REFUSALS = ('ERR:BAD_ARGS', 'ERR:OUT_OF_BOUNDS', 'ERR:KEY_NOT_ALLOWED', 'ERR:NOT_ARMED', 'ERR:HOLD_HUMAN',
                     'ERR:STORAGE_FULL', 'ERR:UNKNOWN_CMD')
KEY_ALLOWED = (
    'ENTER', 'ESC', 'TAB', 'HOME', 'END', 'PGUP', 'PGDN', 'UP', 'DOWN', 'LEFT', 'RIGHT', 'BACKSPACE', 'DELETE', 'SPACE',
    'SHIFT+TAB', 'SHIFT+HOME', 'SHIFT+END', 'SHIFT+PGUP', 'SHIFT+PGDN', 'SHIFT+UP', 'SHIFT+DOWN', 'SHIFT+LEFT',
    'SHIFT+RIGHT', 'SHIFT+SPACE', 'CTRL+A', 'CTRL+C', 'CTRL+V', 'CTRL+F', 'CTRL+S', 'CTRL+P', 'CTRL+L', 'CTRL+HOME',
    'CTRL+END', 'CTRL+LEFT', 'CTRL+RIGHT', 'CTRL+BACKSPACE', 'CTRL+SHIFT+LEFT', 'CTRL+SHIFT+RIGHT',
    'CTRL+SHIFT+HOME', 'CTRL+SHIFT+END', 'ALT+LEFT', 'ALT+RIGHT', 'F5', 'ALT+T', 'CTRL+N', 'CTRL+W', 'ALT+F4', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'SHIFT+A', 'SHIFT+B', 'SHIFT+C', 'SHIFT+D', 'SHIFT+E', 'SHIFT+F', 'SHIFT+G', 'SHIFT+H', 'SHIFT+I', 'SHIFT+J', 'SHIFT+K', 'SHIFT+L', 'SHIFT+M', 'SHIFT+N', 'SHIFT+O', 'SHIFT+P', 'SHIFT+Q', 'SHIFT+R', 'SHIFT+S', 'SHIFT+T', 'SHIFT+U', 'SHIFT+V', 'SHIFT+W', 'SHIFT+X', 'SHIFT+Y', 'SHIFT+Z', 'SHIFT+0', 'SHIFT+1', 'SHIFT+2', 'SHIFT+3', 'SHIFT+4', 'SHIFT+5', 'SHIFT+6', 'SHIFT+7', 'SHIFT+8', 'SHIFT+9', 'MINUS', 'SHIFT+MINUS', 'EQUAL', 'SHIFT+EQUAL', 'LEFTBRACKET', 'SHIFT+LEFTBRACKET', 'RIGHTBRACKET', 'SHIFT+RIGHTBRACKET', 'BACKSLASH', 'SHIFT+BACKSLASH', 'SEMICOLON', 'SHIFT+SEMICOLON', 'QUOTE', 'SHIFT+QUOTE', 'COMMA', 'SHIFT+COMMA', 'DOT', 'SHIFT+DOT', 'SLASH', 'SHIFT+SLASH', 'BACKQUOTE', 'SHIFT+BACKQUOTE')
_ALIAS = {'RETURN': 'ENTER', 'ESCAPE': 'ESC', 'PAGEUP': 'PGUP', 'PAGEDOWN': 'PGDN', 'DEL': 'DELETE',
          'BKSP': 'BACKSPACE', 'CONTROL': 'CTRL'}


def normalize_key(combo):
    """'ctrl+Home' -> 'CTRL+HOME' (same order rule as the firmware); None if malformed."""
    parts = [_ALIAS.get(p.strip().upper(), p.strip().upper()) for p in str(combo).split('+')]
    mods = [m for m in ('CTRL', 'SHIFT', 'ALT') if m in parts]
    keys = [p for p in parts if p not in ('CTRL', 'SHIFT', 'ALT')]
    if len(keys) != 1 or not keys[0] or len(mods) + 1 != len(parts):
        return None
    return '+'.join(mods + keys)


def _ints(r, prefix):
    try:
        return [int(v) for v in r[len(prefix):].split(',')]
    except (ValueError, TypeError):
        return None


class PicoLinkS0214(PicoLinkS02):
    protocol_name = 'S-0.2.14'

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.caps = ()
        self.write_lock = threading.Lock()
        self.last_aborted_at = None

    # ---- connection: accept S-0.2.14 too, read CAPS (old firmware -> ERR:UNKNOWN_CMD -> caps = ())
    def connect(self, port=None):
        saved = _old.SUPPORTED_BUILDS
        _old.SUPPORTED_BUILDS = SUPPORTED_BUILDS          # the old connect() checks this module global
        try:
            ok = super().connect(port)
        finally:
            _old.SUPPORTED_BUILDS = saved
        with self.lock:
            r = self._tx('CAPS', timeout=1.0, accept=('CAPS,',)) or ''
        self.caps = tuple(r.split(',')[1:]) if r.startswith('CAPS,') else ()
        self._audit('CAPS', 'PASS' if self.caps else 'NONE', r[:120])
        return ok

    def has(self, cap):
        return cap in self.caps

    def _tx(self, line, timeout=1.0, accept=(), collect=False):
        with self.write_lock:                              # STOP may be written from another thread meanwhile
            if not self.ser or not getattr(self.ser, 'is_open', True):
                raise ConnectionError('Serial closed')
            self.ser.write((line + '\n').encode('ascii'))
        deadline = time.monotonic() + timeout
        lines = []
        while time.monotonic() < deadline:
            r = self._readline()
            if not r or r.startswith('['):
                continue
            if collect:
                lines.append(r)
                if any(r.startswith(a) for a in accept) and len(lines) >= 3:
                    return lines
                continue
            if r.startswith('ERR:MOVE_ABORTED,'):
                self.last_aborted_at = tuple(_ints(r, 'ERR:MOVE_ABORTED,') or ())
                return r
            if r.startswith('ACK:HEARTBEAT') and not any(r.startswith(a) for a in accept):
                self.last_heartbeat = time.monotonic()      # answered by the firmware during a move
                continue
            if r.startswith('ERR:'):
                return r
            if not accept or any(r == a or r.startswith(a) for a in accept):
                return r
            self._audit('SERIAL_UNMATCHED', 'IGNORED', r[:120])
        return lines if collect else None

    # ---- emergency: STOP goes out immediately (no wait for the command lock)
    def emergency_stop(self):
        if self.connected and self.ser is not None:
            try:
                with self.write_lock:
                    self.ser.write(b'STOP\n')
                self._audit('EMERGENCY_STOP_WIRE', 'SENT')
            except Exception as e:  # noqa: BLE001
                self._audit('EMERGENCY_STOP_WIRE', 'ERROR', str(e)[:100])
        super().emergency_stop()

    human_takeover = emergency_stop

    # ---- generic confirmed command for the new set
    def _expect(self, kind):
        return {'CAPS': ('CAPS,',), 'WHERE': ('WHERE,',), 'MOVE_TO': ('ACK:MOVE_TO,',), 'DRAG': ('ACK:DRAG,',),
                'HOVER': ('ACK:HOVER,',), 'CLICK_DWELL': ('ACK:CLICK_DWELL,',), 'RIGHT_CLICK': ('ACK:RIGHT_CLICK,',),
                'WHEEL': ('ACK:WHEEL,',), 'KEY': ('ACK:KEY,',), 'MOVE_CFG': ('ACK:MOVE_CFG,',),
                'MOVE_CFG?': ('MOVE_CFG,',), 'BOUNDS': ('ACK:BOUNDS,',), 'BOUNDS?': ('BOUNDS,',),
                'ABORT': ('ACK:ABORT',)}.get(kind)

    def command(self, cmd, timeout=3.0):
        """Send one S-0.2.14 command and return the reply. HID commands need ARM; a refusal before any input
        raises ValueError/RuntimeError without an emergency stop; no reply / abort -> emergency stop, no retry."""
        if not self.connected:
            raise RuntimeError('Pico 2 link not ready')
        kind = str(cmd).split(',', 1)[0]
        exp = self._expect(kind)
        if exp is None:
            return self.request_confirmed(cmd, timeout)    # old commands: unchanged behaviour
        if self.caps and kind not in ('CAPS', 'WHERE', 'MOVE_CFG?', 'BOUNDS?', 'ABORT') and \
                kind.rstrip('?') not in self.caps:
            raise RuntimeError('Firmware has no ' + kind)
        if not self.caps and kind != 'CAPS':
            raise RuntimeError('Firmware has no S-0.2.14 commands (CAPS missing)')
        if kind in HID_KINDS:
            if not self.armed:
                raise RuntimeError('Pico 2 is DISARMED')
            if self.held and self.locked:
                raise RuntimeError('Pico 2 HOLD (' + self.held + '): human must resolve first')
        gen = self.epoch
        with self.lock:
            r = self._tx(cmd, timeout=timeout, accept=exp)
        ok = bool(r) and not r.startswith('ERR:') and any(r.startswith(e) for e in exp)
        self._audit(kind, 'PASS' if ok else 'FAIL', (r or 'NO_RESPONSE')[:120])
        if gen != self.epoch or self.emergency.is_set():
            raise RuntimeError('Cancelled by emergency stop')
        if ok:
            return r
        if r and r.startswith('ERR:MOVE_ABORTED'):
            if kind == 'ABORT':
                return r
            raise RuntimeError('Aborted at ' + r.split(',', 1)[1])
        if r and r.startswith(NO_INPUT_REFUSALS):
            raise ValueError('Refused: ' + r)
        if kind in HID_KINDS:
            self.emergency_stop()                          # no reply / unexpected: stop and DISARM, no retry
        raise RuntimeError('Command not acknowledged: ' + str(r))

    # ---- helpers
    def read_caps(self):
        r = self.command('CAPS', 1.0)
        self.caps = tuple(r.split(',')[1:])
        return self.caps

    def where(self):
        x, y = _ints(self.command('WHERE', 1.0), 'WHERE,')
        return x, y

    def _move_timeout(self, dur=None):
        return (max(int(dur or 0), 3000) + 3000) / 1000.0     # vmax may stretch a long move beyond dmax

    @staticmethod
    def _check_xy(*v):
        for a in v:
            if not (0 <= int(a) <= 10000):
                raise ValueError('coordinate out of 0..10000: %r' % (a,))

    def _path(self, kind, x, y, start=None, dur=None):
        self._check_xy(x, y)
        args = [int(x), int(y)]
        if start is not None:
            self._check_xy(*start)
            args += [int(start[0]), int(start[1])]
        if dur is not None:
            args.append(int(dur))
        r = self.command(kind + ',' + ','.join(map(str, args)), self._move_timeout(dur))
        v = _ints(r, 'ACK:%s,' % kind)
        return dict(zip(('x', 'y', 'dur_ms', 'points', 'peak', 'clamped'), v))

    def move_to(self, x, y, start=None, dur=None):
        return self._path('MOVE_TO', x, y, start, dur)

    def drag(self, x, y, start=None, dur=None):
        return self._path('DRAG', x, y, start, dur)

    def hover(self, ms):
        return int(self.command('HOVER,%d' % int(ms), int(ms) / 1000.0 + 2.0).split(',')[1])

    def click_dwell(self, pre_ms, hold_ms=None):
        cmd = 'CLICK_DWELL,%d' % int(pre_ms) + ('' if hold_ms is None else ',%d' % int(hold_ms))
        return _ints(self.command(cmd, 4.0), 'ACK:CLICK_DWELL,')

    def right_click(self, pre_ms=None, hold_ms=None):
        cmd = 'RIGHT_CLICK' + ('' if pre_ms is None else ',%d' % int(pre_ms)) + \
              ('' if hold_ms is None or pre_ms is None else ',%d' % int(hold_ms))
        return _ints(self.command(cmd, 4.0), 'ACK:RIGHT_CLICK,')

    def wheel(self, ticks, gap_ms=None):
        t = int(ticks)
        if t == 0 or not -10 <= t <= 10:
            raise ValueError('ticks -10..10, not 0')
        cmd = 'WHEEL,%d' % t + ('' if gap_ms is None else ',%d' % int(gap_ms))
        return int(self.command(cmd, abs(t) * ((gap_ms or 140) / 1000.0) + 2.0).split(',')[1])

    def key(self, combo, count=1):
        n = normalize_key(combo)
        if n not in KEY_ALLOWED:
            raise ValueError('key not allowed: %r' % (combo,))
        if not 1 <= int(count) <= 20:
            raise ValueError('count 1..20')
        r = self.command('KEY,%s' % n + ('' if int(count) == 1 else ',%d' % int(count)), 2.0 + 0.4 * int(count))
        return r.split(',', 1)[1]

    @staticmethod
    def _cfg_dict(r, prefix):
        return {k: int(v) for k, v in (kv.split('=') for kv in r[len(prefix):].split(','))}

    def move_cfg(self, **kw):
        if not kw:
            return self._cfg_dict(self.command('MOVE_CFG?', 1.0), 'MOVE_CFG,')
        r = self.command('MOVE_CFG,' + ','.join('%s=%d' % (k, int(v)) for k, v in kw.items()), 1.0)
        return self._cfg_dict(r, 'ACK:MOVE_CFG,')

    def bounds(self, x0=None, y0=None, x1=None, y1=None, reset=False):
        if reset:
            return tuple(_ints(self.command('BOUNDS,RESET', 1.0), 'ACK:BOUNDS,'))
        if x0 is None:
            return tuple(_ints(self.command('BOUNDS?', 1.0), 'BOUNDS,'))
        self._check_xy(x0, y0, x1, y1)
        return tuple(_ints(self.command('BOUNDS,%d,%d,%d,%d' % (x0, y0, x1, y1), 1.0), 'ACK:BOUNDS,'))

    def abort(self):
        """Stop a running move but stay armed. Written immediately (no lock wait)."""
        with self.write_lock:
            self.ser.write(b'ABORT\n')
        self._audit('ABORT', 'SENT')
