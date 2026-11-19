"""Pico 2 link for firmware 1.0.x (e.g. 1.0.2-FIX, PONG BIDGUARD_HID v7.3) AND the old S-0.2.14-HUMAN-MOVE.

Additive (2026-11-19, t734u): pico_s02_link.py / pico_s0214_link.py are unchanged; this class subclasses
PicoLinkS0214 and only replaces connect():
  * port: AUTO = exactly one USB serial device with VID:PID 2E8A:000C (Pico 2 HID+CDC), else exactly one with
    VID 2E8A, else the fallback port (COM8, settings 'pico_port_fallback' / env BIDGUARD_PICO_PORT_FALLBACK)
    if Windows lists it. Several Picos -> refuse (never guess).
  * PING: one line 'PONG BIDGUARD_HID v7.3 / 1.0.2-FIX / HUMAN=2' or three lines (old). Protocol v7.2 or v7.3,
    build S-0.2.14-HUMAN-MOVE or 1.0.<n>[-TAG].
  * CAPS read at connect; REQUIRED_CAPS missing -> RuntimeError with the exact missing names (the collector
    then refuses to run and logs one clear line). DRAG / RIGHT_CLICK are optional (callers check has()).
  * STATUS / VERSION are optional (no reply -> ignored).
Rules kept: single serial owner, no HID before ARM, never retry an HID command on timeout, F8/takeover -> STOP.
"""
from __future__ import annotations

import os
import re
import threading
import time

from . import pico_s02_link as _old
from .pico_s0214_link import PicoLinkS0214, BUILD_S0214

PICO_VID = 0x2E8A
PICO_PID = 0x000C
FALLBACK_PORT = 'COM8'
PROTOCOLS = ('PONG BIDGUARD_HID v7.2', 'PONG BIDGUARD_HID v7.3')
BUILD_RE = re.compile(r'^(S-0\.2\.14-HUMAN-MOVE|1\.0\.\d+(?:-[A-Za-z0-9_.]+)?)$')
REQUIRED_CAPS = ('MOVE_TO', 'HOVER', 'CLICK_DWELL', 'WHEEL', 'KEY', 'MOVE_CFG', 'WHERE')
OPTIONAL_CAPS = ('DRAG', 'RIGHT_CLICK', 'BOUNDS', 'ABORT')


def is_supported_build(build):
    return bool(build) and bool(BUILD_RE.match(str(build).strip()))


def parse_ping(lines):
    """-> (protocol, build, human) from one joined line or several lines; missing parts are None."""
    parts = []
    for ln in lines or []:
        parts += [p.strip() for p in str(ln).split(' / ')]
    proto = next((p for p in parts if p.startswith('PONG ')), None)
    build = next((p for p in parts if BUILD_RE.match(p) or p.startswith(('S-0.', '1.0.'))), None)
    human = next((p for p in parts if p.startswith('HUMAN=')), None)
    return proto, build, human


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


class PicoLinkFW1(PicoLinkS0214):
    protocol_name = 'FW1'

    def __init__(self, *a, **kw):
        self.port_fallback = kw.pop('port_fallback', None)
        super().__init__(*a, **kw)
        self.protocol = None
        self.human = None
        self.port_how = None
        self.missing_caps = ()

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
            self.held = st.split('hold=1:')[1].split(',')[0] if 'hold=1:' in st else None
            self.locked = bool(self.held) and ('lock=1' in st)
            r = self._tx('CAPS', timeout=1.0, accept=('CAPS,', 'CAPS ')) or ''
            r = r if isinstance(r, str) else ''
            self.caps = tuple(c.strip() for c in re.split(r'[ ,]+', r)[1:] if c.strip()) if r.startswith('CAPS') else ()
            self.armed = False
            self.connected = True
            self.stop_event.clear(); self.emergency.clear(); self.epoch += 1
        self._audit('CONNECT', 'PASS', '%s %s port=%s how=%s' % (proto, build, self.port, self.port_how))
        self._audit('CAPS', 'PASS' if self.caps else 'NONE', r[:160])
        self.missing_caps = tuple(c for c in REQUIRED_CAPS if c not in self.caps)
        if self.missing_caps:
            self.disconnect()
            raise RuntimeError('Pico 2 %s CAPS missing %s (have %s)' % (build, ','.join(self.missing_caps),
                                                                        ','.join(self.caps)))
        self.thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name='pico-fw1-heartbeat')
        self.thread.start()
        self._status('CONNECTED / DISARMED %s %s' % (self.port, build))
        return True


SUPPORTED_DESC = 'S-0.2.14-HUMAN-MOVE or 1.0.x (v7.2/v7.3)'
