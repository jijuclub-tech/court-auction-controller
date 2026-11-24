"""Pico 2 link for firmware 1.0.x (1.0.2-FIX, 1.0.3, PONG BIDGUARD_HID v7.3) AND the old S-0.2.14-HUMAN-MOVE.

Additive (2026-11-19, t734u/t740u): pico_s02_link.py / pico_s0214_link.py stay unchanged; this class
subclasses PicoLinkS0214 and replaces connect() plus 1.0.x ACK / command differences.

  * port: AUTO = exactly one USB serial device with VID:PID 2E8A:000C (Pico 2 HID+CDC), else exactly one with
    VID 2E8A, else the fallback port (COM8, settings 'pico_port_fallback' / env BIDGUARD_PICO_PORT_FALLBACK)
    if Windows lists it. Several Picos -> refuse (never guess).
  * PING: one line 'PONG BIDGUARD_HID v7.3 / 1.0.2-FIX / HUMAN=2' or three lines (old). Protocol v7.2 or v7.3,
    build S-0.2.14-HUMAN-MOVE or 1.0.<n>[-TAG].
  * CAPS is not enough on 1.0.x: WHEEL / MOVE_CFG / BOUNDS may be listed but return ERR:UNKNOWN_CMD.
    After CAPS, 1.0.x is probed with WHEEL,1 and KEY,END / KEY,HOME (no ARM; 1.0.x returns KEY_BLOCKED or
    UNKNOWN_CMD with no HID). If WHEEL or KEY END/HOME is unavailable, connect() disconnects and raises
    RuntimeError with one clear line (the collector must not start).
  * 1.0.3 DRAG,x,y and RCLICK / RDRAG,x,y use coordinates 0..10000 and ACK:CMD_OK (old S-0.2.14 still uses
    ACK:DRAG, / ACK:RIGHT_CLICK,). MOVE_TO ACK may be ACK:MOVE_TO,x,y,OK.
  * HEARTBEAT thread (inherited, every heartbeat_sec, default 0.8s) still runs. Firmware 1.0.3 only needs
    one successful HEARTBEAT then ARM (heartbeatSeen is sticky; no auto-disarm timeout). Collector still
    keeps sending HEARTBEAT and ARMs before input / DISARMs at the end.
  * STATUS / VERSION are optional (no reply -> ignored).
Rules kept: single serial owner, no HID before ARM, never retry an HID command on timeout, F8/takeover -> STOP.
"""
from __future__ import annotations

import os
import re
import threading
import time

from . import pico_s02_link as _old
from .pico_s0214_link import PicoLinkS0214, BUILD_S0214, HID_KINDS, _ints

PICO_VID = 0x2E8A
PICO_PID = 0x000C
FALLBACK_PORT = 'COM8'
PROTOCOLS = ('PONG BIDGUARD_HID v7.2', 'PONG BIDGUARD_HID v7.3')
BUILD_RE = re.compile(r'^(S-0\.2\.14-HUMAN-MOVE|1\.0\.\d+(?:-[A-Za-z0-9_.]+)?)$')
# CAPS names that must be listed (1.0.x may still advertise WHEEL/MOVE_CFG without a handler).
REQUIRED_CAPS = ('MOVE_TO', 'HOVER', 'CLICK_DWELL', 'WHEEL', 'KEY', 'MOVE_CFG', 'WHERE')
OPTIONAL_CAPS = ('DRAG', 'RIGHT_CLICK', 'RCLICK', 'RDRAG', 'BOUNDS', 'ABORT')
# refusals that happen before any HID (do not emergency-stop)
FW1_NO_INPUT = ('ERR:KEY_BLOCKED', 'ERR:DISARMED', 'ERR:NO_HEARTBEAT', 'ERR:UNKNOWN_CMD',
                'ERR:BAD_ARGS', 'ERR:NOT_ARMED')


def is_supported_build(build):
    return bool(build) and bool(BUILD_RE.match(str(build).strip()))


def is_fw1_build(build):
    return bool(build) and str(build).strip().startswith('1.0.')


def parse_ping(lines):
    """-> (protocol, build, human) from one joined line or several lines; missing parts are None."""
    parts = []
    for ln in lines or []:
        parts += [p.strip() for p in str(ln).split(' / ')]
    proto = next((p for p in parts if p.startswith('PONG ')), None)
    build = next((p for p in parts if BUILD_RE.match(p) or p.startswith(('S-0.', '1.0.'))), None)
    human = next((p for p in parts if p.startswith('HUMAN=')), None)
    return proto, build, human


def reply_means_implemented(reply, ack_prefixes):
    """True if the firmware has the command (ACK, or a session error). False for blocked/unknown/no reply."""
    if not reply:
        return False
    r = str(reply)
    if any(r.startswith(p) for p in ack_prefixes):
        return True
    if r.startswith(('ERR:DISARMED', 'ERR:NO_HEARTBEAT', 'ERR:NOT_ARMED')):
        return True
    return False


def parse_ack(lines, cmd=None):
    """Classify one or more firmware reply lines into ack/err/kind/detail/implemented/ok."""
    raw = [str(x) for x in (lines or []) if str(x).strip()]
    ack = next((x for x in raw if x.startswith('ACK:')), None)
    err = next((x for x in raw if x.startswith('ERR:')), None)
    pong = next((x for x in raw if x.startswith('PONG')), None)
    kind, detail = None, None
    src = ack or err
    if src:
        body = src.split(':', 1)[1]
        if ',' in body:
            kind, detail = body.split(',', 1)
        else:
            kind, detail = body, ''
    impl = None
    if cmd and str(cmd).startswith(('WHEEL', 'KEY,', 'MOVE_CFG', 'BOUNDS')):
        prefixes = ('ACK:WHEEL', 'ACK:CMD_OK', 'ACK:KEY,', 'ACK:MOVE_CFG', 'MOVE_CFG,', 'ACK:BOUNDS', 'BOUNDS,')
        impl = reply_means_implemented(ack or err, prefixes)
    return {'raw': raw, 'ack': ack, 'err': err, 'pong': pong, 'kind': kind, 'detail': detail,
            'implemented': impl, 'ok': bool(raw) and err is None}


def find_port(comports=None, fallback=None):
    """-> (port, how). comports: list of objects with .device/.vid/.pid (pyserial list_ports.comports())."""
    if comports is None:
        if _old.list_ports is None:
            raise RuntimeError('Install pyserial')
        comports = list(_old.list_ports.comports())
    exact = sorted(p.device for p in comports if p.vid == PICO_VID and p.pid == PICO_PID)
    if len(exact) == 1:
        return exact[0], 'vid_pid_2E8A:000C'
    if len(exact) > 1:
        raise RuntimeError('several Pico 2 (2E8A:000C) found %s - set pico_port' % exact)
    anyv = sorted(p.device for p in comports if p.vid == PICO_VID)
    if len(anyv) == 1:
        return anyv[0], 'vid_2E8A'
    if len(anyv) > 1:
        raise RuntimeError('several 2E8A devices found %s - set pico_port' % anyv)
    fb = fallback or os.environ.get('BIDGUARD_PICO_PORT_FALLBACK') or FALLBACK_PORT
    if fb and fb.upper() in {str(p.device).upper() for p in comports}:
        return fb, 'fallback'
    raise RuntimeError('no Pico 2 serial port (2E8A:000C) and fallback %s not present' % fb)


def _start_refused_message(build, missing):
    return ('수집 시작 거부: 펌웨어 %s 에 수집기가 꼭 써야 하는 명령이 없습니다: %s. '
            'WHEEL 실핸들러와 KEY END/HOME(그리고 ESC/ENTER)이 있는 펌웨어로 보드를 갱신하기 전에는 '
            '수집을 시작하지 않습니다.' % (build, '; '.join(missing)))


class PicoLinkFW1(PicoLinkS0214):
    protocol_name = 'FW1'

    def __init__(self, *a, **kw):
        self.port_fallback = kw.pop('port_fallback', None)
        super().__init__(*a, **kw)
        self.protocol = None
        self.human = None
        self.port_how = None
        self.missing_caps = ()
        self.wheel_live = None
        self.key_end_live = None
        self.key_home_live = None
        self.probe_replies = {}

    def _is_fw1(self):
        return is_fw1_build(self.version)

    def _rotate_events(self, max_bytes=20 * 1024 * 1024):
        """logs/pico_events.jsonl grew to 17 MB by 11/19: rename it (date stamp) once it passes 20 MB."""
        try:
            p = self.events_path
            if p.exists() and p.stat().st_size > max_bytes:
                p.rename(p.with_name('pico_events_%s.jsonl' % time.strftime('%Y%m%d_%H%M%S')))
        except OSError:
            pass

    def connect(self, port=None):
        self._rotate_events()
        if not port or str(port).upper() == 'AUTO':
            port, self.port_how = find_port(fallback=self.port_fallback)
        else:
            self.port_how = 'configured'
        if self.connected:
            self.disconnect()
        if _old.serial is None and self.serial_factory is None:
            raise RuntimeError('Install pyserial')
        self.port = str(port)
        factory = self.serial_factory or _old.serial.Serial
        self.ser = factory(self.port, 115200, timeout=0.15, write_timeout=0.5)
        time.sleep(0.05 if self.serial_factory else 0.35)
        with self.lock:
            lines = self._tx('PING', timeout=1.5, collect=True, accept=('HUMAN=',)) or []
            proto, build, human = parse_ping(lines)
            if proto not in PROTOCOLS or not is_supported_build(build):
                try:
                    self.ser.close()
                finally:
                    self.ser = None
                raise RuntimeError('Unsupported firmware: %s' % (lines[:3],))
            self.version, self.protocol, self.human = build.strip(), proto, human
            st = self._tx('STATUS', timeout=1.0, accept=('STATUS,',)) or ''
            if not isinstance(st, str) or st.startswith('ERR:'):
                st = ''
            self.held = st.split('hold=1:')[1].split(',', 1)[0] if 'hold=1:' in st else None
            self.locked = bool(self.held) and ('lock=1' in st)
            r = self._tx('CAPS', timeout=1.0, accept=('CAPS,', 'CAPS ')) or ''
            r = r if isinstance(r, str) else ''
            self.caps = tuple(c.strip() for c in re.split(r'[ ,]+', r)[1:] if c.strip()) if r.startswith('CAPS') else ()
            self.armed = False
            self.connected = True
            self.stop_event.clear(); self.emergency.clear(); self.epoch += 1
        self._audit('CONNECT', 'PASS', '%s %s port=%s how=%s' % (proto, build, self.port, self.port_how))
        self._audit('CAPS', 'PASS' if self.caps else 'NONE', r[:160])
        self.missing_caps = tuple(c for c in REQUIRED_CAPS if c not in self.caps and
                                  not (c == 'RIGHT_CLICK' and 'RCLICK' in self.caps))
        if self.missing_caps:
            self.disconnect()
            raise RuntimeError('Pico 2 %s CAPS missing %s (have %s)' % (build, ','.join(self.missing_caps),
                                                                        ','.join(self.caps)))
        if self._is_fw1():
            missing_live = self._probe_required_live()
            if missing_live:
                msg = _start_refused_message(build, missing_live)
                self._audit('START_REFUSED', 'FAIL', msg)
                self.disconnect()
                raise RuntimeError(msg)
        self.thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name='pico-fw1-heartbeat')
        self.thread.start()
        self._status('CONNECTED / DISARMED %s %s' % (self.port, build))
        return True

    def _probe_required_live(self):
        """1.0.x only. No ARM. WHEEL / KEY END / KEY HOME must be implemented, not just listed in CAPS."""
        missing = []
        with self.lock:
            wr = self._tx('WHEEL,1', timeout=1.0, accept=('ACK:WHEEL', 'ACK:CMD_OK', 'ERR:'))
        self.probe_replies['WHEEL,1'] = wr
        self.wheel_live = reply_means_implemented(wr, ('ACK:WHEEL', 'ACK:CMD_OK'))
        self._audit('PROBE_WHEEL', 'PASS' if self.wheel_live else 'FAIL', str(wr))
        if not self.wheel_live:
            missing.append('WHEEL (답: %s)' % (wr or 'NO_RESPONSE'))
        for name, attr in (('END', 'key_end_live'), ('HOME', 'key_home_live')):
            cmd = 'KEY,' + name
            with self.lock:
                kr = self._tx(cmd, timeout=1.0, accept=('ACK:KEY,', 'ERR:'))
            self.probe_replies[cmd] = kr
            ok = reply_means_implemented(kr, ('ACK:KEY,',))
            setattr(self, attr, ok)
            self._audit('PROBE_' + cmd, 'PASS' if ok else 'FAIL', str(kr))
            if not ok:
                missing.append('%s (답: %s)' % (cmd, kr or 'NO_RESPONSE'))
        return missing

    def has(self, cap):
        if cap in ('RIGHT_CLICK', 'RCLICK'):
            return ('RCLICK' in self.caps) or ('RIGHT_CLICK' in self.caps)
        if cap == 'WHEEL' and self.wheel_live is False:
            return False
        if cap in ('MOVE_CFG', 'BOUNDS') and self._is_fw1():
            # advertised on 1.0.x CAPS but not implemented in PR #10 source
            return cap in self.caps and cap not in ('MOVE_CFG', 'BOUNDS')
        return cap in (self.caps or ())

    def _expect(self, kind):
        if kind == 'RCLICK':
            return ('ACK:CMD_OK', 'ACK:RIGHT_CLICK,', 'ACK:RCLICK')
        if kind == 'RDRAG':
            return ('ACK:CMD_OK', 'ACK:RDRAG,')
        exp = super()._expect(kind)
        if not self._is_fw1() or exp is None:
            return exp
        extra = {
            'DRAG': ('ACK:CMD_OK', 'ACK:DRAG,'),
            'RIGHT_CLICK': ('ACK:CMD_OK', 'ACK:RIGHT_CLICK,', 'ACK:RCLICK'),
            'MOVE_TO': ('ACK:MOVE_TO,', 'ACK:CMD_OK'),
            'WHEEL': ('ACK:WHEEL,', 'ACK:CMD_OK'),
            'KEY': ('ACK:KEY,',),
            'HOVER': ('ACK:HOVER,', 'ACK:CMD_OK'),
            'CLICK_DWELL': ('ACK:CLICK_DWELL,', 'ACK:CMD_OK'),
        }
        if kind in extra:
            return extra[kind]
        return exp

    def command(self, cmd, timeout=3.0):
        if not self.connected:
            raise RuntimeError('Pico 2 link not ready')
        kind = str(cmd).split(',', 1)[0]
        if self._is_fw1() and kind == 'RIGHT_CLICK':
            cmd = 'RCLICK' + str(cmd)[len('RIGHT_CLICK'):]
            kind = 'RCLICK'
        exp = self._expect(kind)
        if exp is None:
            return self.request_confirmed(cmd, timeout)
        cap_name = 'RIGHT_CLICK' if kind == 'RCLICK' else kind
        if self.caps and kind not in ('CAPS', 'WHERE', 'MOVE_CFG?', 'BOUNDS?', 'ABORT') and \
                cap_name.rstrip('?') not in self.caps and kind not in self.caps and \
                not (kind in ('RCLICK', 'RIGHT_CLICK') and self.has('RCLICK')):
            raise RuntimeError('Firmware has no ' + kind)
        hid_kinds = HID_KINDS + ('RCLICK', 'RDRAG')
        if kind in hid_kinds:
            if not self.armed:
                raise RuntimeError('Pico 2 is DISARMED')
            if self.held and self.locked:
                raise RuntimeError('Pico 2 HOLD (' + self.held + '): human must resolve first')
        gen = self.epoch
        with self.lock:
            r = self._tx(cmd, timeout=timeout, accept=exp)
        ok = bool(r) and not str(r).startswith('ERR:') and any(str(r).startswith(e) or str(r) == e for e in exp)
        self._audit(kind, 'PASS' if ok else 'FAIL', (r or 'NO_RESPONSE')[:120])
        if gen != self.epoch or self.emergency.is_set():
            raise RuntimeError('Cancelled by emergency stop')
        if ok:
            return r
        if r and str(r).startswith('ERR:MOVE_ABORTED'):
            if kind == 'ABORT':
                return r
            raise RuntimeError('Aborted at ' + str(r).split(',', 1)[1])
        if r and str(r).startswith(FW1_NO_INPUT):
            raise ValueError('Refused: ' + r)
        if kind in hid_kinds:
            self.emergency_stop()
        raise RuntimeError('Command not acknowledged: ' + str(r))

    def _parse_path_ack(self, kind, x, y, r, dur=None):
        if r and str(r).startswith('ACK:CMD_OK'):
            return dict(x=int(x), y=int(y), dur_ms=dur, points=None, peak=None, clamped=None, ack='CMD_OK')
        prefix = 'ACK:%s,' % kind
        if not r or not str(r).startswith(prefix):
            raise RuntimeError('Command not acknowledged: ' + str(r))
        nums, flags = [], []
        for p in str(r)[len(prefix):].split(','):
            try:
                nums.append(int(p))
            except ValueError:
                flags.append(p)
        out = dict(zip(('x', 'y', 'dur_ms', 'points', 'peak', 'clamped'), (nums + [None] * 6)[:6]))
        out['ack'] = flags[0] if flags else None
        return out

    def _path(self, kind, x, y, start=None, dur=None):
        if not self._is_fw1():
            return super()._path(kind, x, y, start, dur)
        self._check_xy(x, y)
        if kind == 'DRAG':
            r = self.command('DRAG,%d,%d' % (int(x), int(y)), self._move_timeout(dur))
            return self._parse_path_ack(kind, x, y, r, dur)
        args = [int(x), int(y)]
        if start is not None:
            self._check_xy(*start)
            args += [int(start[0]), int(start[1])]
        if dur is not None:
            args.append(int(dur))
        r = self.command(kind + ',' + ','.join(map(str, args)), self._move_timeout(dur))
        return self._parse_path_ack(kind, x, y, r, dur)

    def right_click(self, pre_ms=None, hold_ms=None):
        if self._is_fw1() and self.has('RCLICK'):
            r = self.command('RCLICK', 4.0)
            if r and str(r).startswith('ACK:CMD_OK'):
                return [0, 0]
            return _ints(r, 'ACK:RIGHT_CLICK,') or _ints(r, 'ACK:RCLICK,')
        return super().right_click(pre_ms, hold_ms)

    def rdrag(self, x, y):
        """1.0.3 RDRAG,x,y (0..10000)."""
        self._check_xy(x, y)
        r = self.command('RDRAG,%d,%d' % (int(x), int(y)), self._move_timeout())
        return self._parse_path_ack('RDRAG', x, y, r)

    def key(self, combo, count=1):
        if self._is_fw1():
            # 1.0.x KEY has no count argument; repeat the command. ACK is ACK:KEY,<name>.
            n = int(count)
            if not 1 <= n <= 20:
                raise ValueError('count 1..20')
            last = None
            for _ in range(n):
                last = super().key(combo, 1)
            return last
        return super().key(combo, count)

    def move_cfg(self, **kw):
        if self._is_fw1():
            raise ValueError('Refused: ERR:UNKNOWN_CMD')
        return super().move_cfg(**kw)

    def bounds(self, x0=None, y0=None, x1=None, y1=None, reset=False):
        if self._is_fw1():
            raise ValueError('Refused: ERR:UNKNOWN_CMD')
        return super().bounds(x0, y0, x1, y1, reset=reset)


SUPPORTED_DESC = 'S-0.2.14-HUMAN-MOVE or 1.0.x (v7.2/v7.3)'
