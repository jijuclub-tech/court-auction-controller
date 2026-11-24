"""Pico 2 firmware 1.0.8-v8 (PONG BIDGUARD_HID v8) compatibility for the C2 collector (additive, 2026-11-20).

Owner approval 2026-11-20 23:50 KST (relayed by 지휘자): upgrade LG15 Pico2 to v8 and test collector 2호.
pico_s02_link.py / pico_s0214_link.py are NOT changed. Old firmware (v7.2 / S-0.2.1x) keeps the old path:
PicoLinkV8.connect() falls back to PicoLinkS0214.connect() when PING is not v8.

Wire differences of 1.0.8-v8 vs S-0.2.14 handled here (from firmware/bidguard-hid-pico2.ino on branch
grok/firmware-1.0.8-v8-gameui-20261120):
  * PING -> 'PONG BIDGUARD_HID v8' / '1.0.8-v8' / 'HUMAN=n' (3 lines). STATUS is unknown (ERR:UNKNOWN_CMD).
  * MOVE_CFG: only the bare 'MOVE_CFG' query exists -> 'ACK:MOVE_CFG,rate=..,require_arm=1,...' (always 1).
    'MOVE_CFG,k=v' and 'MOVE_CFG?' are unknown -> we send the bare query and read require_arm back.
  * BOUNDS: only bare 'BOUNDS' -> 'ACK:BOUNDS,0,0,10000,10000' (BOUNDS,RESET / BOUNDS? unknown).
  * RIGHT_CLICK: only bare 'RIGHT_CLICK' / 'RCLICK' -> 'ACK:CMD_OK'.
  * MOVE_TO / DRAG / HOVER / CLICK_DWELL / WHEEL / KEY / WHERE / ABORT / STOP / HEARTBEAT / ARM / DISARM:
    same reply prefixes as S-0.2.14 (ACK:MOVE_TO,.. ACK:DRAG,.. ACK:WHEEL,n ACK:KEY,K,1 ACK:STOPPED ...).
Rules kept: single serial owner, no HID before ARM (host side), never retry an HID command on timeout,
F8 / takeover -> STOP written at once.
"""
from __future__ import annotations

import time

from . import pico_s02_link as _old
from .pico_s0214_link import PicoLinkS0214, BUILD_S0214, _ints

PROTOCOL_V8 = 'PONG BIDGUARD_HID v8'
BUILD_V8 = '1.0.8-v8'
# New allowed values (old ones in pico_s02_link / pico_s0214_link stay as they are).
ACCEPTED_PROTOCOLS_V8 = (PROTOCOL_V8,)
ACCEPTED_BUILDS_V8 = (BUILD_V8,)
ACCEPTED_BUILDS_ALL = (BUILD_S0214, BUILD_V8)
# r2 (2026-11-21, commit 115c605, UF2 cc81a4b0...): additive values, r1 values above stay.
ACCEPTED_VERSION_REPLIES = (BUILD_V8, 'v8')          # VERSION: r1 -> '1.0.8-v8', r2 -> 'v8'
ACK_STOPPED_REPLIES = ('ACK:STOPPED', 'ACK:STOPPED,DISARMED')   # r2 STOP/F8/EMERGENCY_STOP -> ACK:STOPPED,DISARMED
ACK_RIGHT_CLICK_R2 = ('ACK:RIGHT_CLICK,',)
ACK_AUTO_DISARM = 'ACK:AUTO_DISARM'                  # r2: unsolicited, armed + no command/HEARTBEAT for 15 s
HEARTBEAT_MAX_SEC = 8.0                              # host HEARTBEAT interval cap (< 15 s auto-disarm; default 0.8 s)


def version_reply_ok(reply):
    return str(reply or '').strip() in ACCEPTED_VERSION_REPLIES


def is_v8(build):
    return str(build or '').strip() in ACCEPTED_BUILDS_V8


class PicoLinkV8(PicoLinkS0214):
    protocol_name = 'v8'

    def _is_v8(self):
        return is_v8(self.version)

    # ---- connection
    def connect(self, port=None):
        if not port or str(port).upper() == 'AUTO':
            avail = self.discover_pico_ports()
            if len(avail) != 1:
                raise RuntimeError('Pico 2 auto-detection requires exactly one Raspberry Pi USB serial device')
            port = avail[0][0]
        if self.connected:
            self.disconnect()
        if _old.serial is None and self.serial_factory is None:
            raise RuntimeError('Install pyserial')
        factory = self.serial_factory or _old.serial.Serial
        ser = factory(str(port), 115200, timeout=0.15, write_timeout=0.5)
        time.sleep(0.05 if self.serial_factory else 0.35)
        self.ser, self.port = ser, str(port)
        with self.lock:
            lines = self._tx('PING', timeout=1.5, collect=True, accept=('HUMAN=',)) or []
        build = next((l for l in lines if l in ACCEPTED_BUILDS_V8), None)
        if not any(p in lines for p in ACCEPTED_PROTOCOLS_V8) or not build:
            # not v8 -> old path unchanged (it reopens the port and checks v7.2 / S-0.2.1x)
            try:
                ser.close()
            finally:
                self.ser = None
            return super().connect(port)
        with self.lock:
            self.version = build
            self.held, self.locked = None, False          # v8 has no HOLD / STATUS
            self.armed = False
            self.connected = True
            self.stop_event.clear(); self.emergency.clear(); self.epoch += 1
            r = self._tx('CAPS', timeout=1.0, accept=('CAPS,',)) or ''
        self.caps = tuple(r.split(',')[1:]) if r.startswith('CAPS,') else ()
        self._audit('CAPS', 'PASS' if self.caps else 'NONE', r[:120])
        self.fw_rev = 'r2' if 'STATUS' in self.caps else 'r1'     # r2 adds STATUS/F8/BUILD to CAPS
        if self.heartbeat_sec > HEARTBEAT_MAX_SEC:                # r2 AUTO_DISARM after 15 s without HEARTBEAT
            self.heartbeat_sec = HEARTBEAT_MAX_SEC
        import threading
        self.thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name='pico-v8-heartbeat')
        self.thread.start()
        self._audit('CONNECT', 'PASS', build)
        self._status('CONNECTED / DISARMED ' + self.port + ' / ' + build)
        return True

    # ---- reply prefixes that differ on v8
    def _expect(self, kind):
        if self._is_v8():
            if kind == 'RIGHT_CLICK' or kind == 'RCLICK':
                return ('ACK:CMD_OK',) + ACK_RIGHT_CLICK_R2      # r1: ACK:CMD_OK, r2: ACK:RIGHT_CLICK,pre,hold
            if kind == 'MOVE_CFG':
                return ('ACK:MOVE_CFG,',)
            if kind == 'BOUNDS':
                return ('ACK:BOUNDS,',)
        return super()._expect(kind)

    def move_cfg(self, **kw):
        if not self._is_v8():
            return super().move_cfg(**kw)
        # v8: read-only query; require_arm is fixed to 1 in the firmware reply. Extra keys are not settable.
        r = self.command('MOVE_CFG', 1.0)
        out = {}
        for kv in r[len('ACK:MOVE_CFG,'):].split(','):
            if '=' in kv:
                k, v = kv.split('=', 1)
                try:
                    out[k] = int(v)
                except ValueError:
                    pass
        if kw:
            self._audit('MOVE_CFG', 'V8_READONLY', ','.join(sorted(kw))[:100])
        return out

    def bounds(self, x0=None, y0=None, x1=None, y1=None, reset=False):
        if not self._is_v8():
            return super().bounds(x0, y0, x1, y1, reset=reset)
        if x0 is not None:
            raise ValueError('Refused: v8 BOUNDS is fixed 0,0,10000,10000')
        return tuple(_ints(self.command('BOUNDS', 1.0), 'ACK:BOUNDS,'))

    def right_click(self, pre_ms=None, hold_ms=None):
        if not self._is_v8():
            return super().right_click(pre_ms, hold_ms)
        self.command('RIGHT_CLICK', 4.0)
        return [0, 0]

    def key(self, combo, count=1):
        if not self._is_v8() or int(count) == 1:
            return super().key(combo, count)
        n = int(count)                                  # v8 KEY has no count argument: send one at a time
        if not 1 <= n <= 20:
            raise ValueError('count 1..20')
        last = None
        for _ in range(n):
            last = super().key(combo, 1)
        return last

    # ---- r2 additions
    fw_rev = None

    def _readline(self):
        r = super()._readline()
        if r and r.startswith(ACK_AUTO_DISARM):
            # r2 disarmed itself (no command/HEARTBEAT for idle_disarm_ms): mirror it, never treat as a reply
            if self.armed:
                self._status('AUTO_DISARM (firmware) / DISARMED')
            self.armed = False
            self._audit('AUTO_DISARM', 'FIRMWARE', r[:60])
            return ''
        return r

    def read_version(self):
        """VERSION -> '1.0.8-v8' (r1) or 'v8' (r2); returns (ok, reply)."""
        with self.lock:
            r = self._tx('VERSION', timeout=1.0, accept=ACCEPTED_VERSION_REPLIES) or ''
        ok = version_reply_ok(r)
        self._audit('VERSION', 'PASS' if ok else 'FAIL', str(r)[:60])
        return ok, r
