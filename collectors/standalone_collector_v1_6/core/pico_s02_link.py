"""Pico 2 firmware S-0.2.13-HOLD-OPTIONAL / S-0.2.12-SAFE-HOLD serial link (same public surface as PicoLink).
Optional: settings.json "pico_protocol": "S-0.2.13". The original default link is PicoLink (S-0.3.0).

Firmware repo protocol (PONG BIDGUARD_HID v7.2, space form):
  PING -> 'PONG BIDGUARD_HID v7.2' / '<build>' / 'HUMAN=n'
  HEARTBEAT -> 'ACK:HEARTBEAT,n'      ARM/DISARM/STOP -> 'ACK:ARMED' / 'ACK:DISARMED' / 'ACK:STOPPED,DISARMED'
  MOVE,x,y / CLICK / TYPE,text -> 'ACK:CMD_OK'   (refused with 'ERR:HOLD_HUMAN,<r>' only while HOLD with the lock on)
  HOLDLOCK,1|0 -> 'ACK:HOLDLOCK,1|0' (S-0.2.13; lock OFF by default)
  HOLD,<CAPTCHA|BLOCK|LOGIN|RATE_LIMIT> -> 'ACK:HOLD,<r>'   RELEASE -> 'ACK:RELEASE'   BEEP,n -> 'ACK:BEEP,n'
  STATUS -> 'STATUS,...'
Rules kept from PicoLink: single serial owner, no HID before ARM, never retry an HID command on timeout,
a late reply is not success, F8/emergency cancels pending work and DISARMs.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None

LOG = logging.getLogger('PicoLinkS02')
PROTOCOL = 'PONG BIDGUARD_HID v7.2'
SUPPORTED_BUILDS = ('S-0.2.13-HOLD-OPTIONAL', 'S-0.2.12-SAFE-HOLD')
ALWAYS_LOCKED_BUILDS = ('S-0.2.12-SAFE-HOLD',)
FEATURES = ('MOTION', 'LED', 'TIME', 'DIAGNOSTIC', 'SESSION', 'SOUND')
HOLD_REASONS = ('CAPTCHA', 'BLOCK', 'LOGIN', 'RATE_LIMIT')


class PicoLinkS02:
    protocol_name = 'S-0.2.13'

    def __init__(self, on_status=None, on_log=None, serial_factory=None, heartbeat_sec=0.8):
        self.on_status = on_status or (lambda m: None)
        self.on_log = on_log or (lambda e: None)
        self.serial_factory = serial_factory
        self.ser = None
        self.port = None
        self.version = None
        self.armed = False
        self.connected = False
        self.held = None
        self.locked = False
        self.emergency = threading.Event()
        self.stop_event = threading.Event()
        self.epoch = 0
        self.lock = threading.RLock()
        self.thread = None
        self.heartbeat_sec = heartbeat_sec
        self.last_heartbeat = 0.0
        self.feature_settings = {f: True for f in FEATURES}
        self.events_path = Path('logs') / 'pico_events.jsonl'

    # ---- helpers
    def _status(self, msg):
        try:
            self.on_status(msg)
        except Exception:
            pass
        LOG.info('%s', msg)

    def _audit(self, kind, result, detail=''):
        evt = {'time': datetime.now(timezone.utc).isoformat(), 'device': self.port, 'version': self.version,
               'event': kind, 'result': result, 'detail': detail}
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(evt, ensure_ascii=True) + '\n')
            self.on_log(evt)
        except Exception:
            LOG.exception('audit failed')

    @staticmethod
    def ports():
        if not list_ports:
            return []
        return [(p.device, p.vid, p.pid) for p in list_ports.comports()]

    @staticmethod
    def discover_pico_ports():
        if not list_ports:
            return []
        return sorted(((e.device.upper(), e.description or 'Raspberry Pi USB')
                       for e in list_ports.comports() if e.vid == 0x2E8A), key=lambda x: x[0])

    @classmethod
    def detect_ports(cls, serial_factory=None):
        return cls.discover_pico_ports()

    def _readline(self):
        raw = self.ser.readline()
        return raw.decode('utf-8', 'replace').strip() if raw else ''

    def _tx(self, line, timeout=1.0, accept=(), collect=False):
        """Send one line and wait for an accepted reply. Notice lines '[...]' are skipped."""
        if not self.ser or not getattr(self.ser, 'is_open', True):
            raise ConnectionError('Serial closed')
        self.ser.write((line + '\n').encode('ascii'))
        deadline = time.monotonic() + timeout
        lines = []
        while time.monotonic() < deadline:
            r = self._readline()
            if not r:
                continue
            if r.startswith('['):
                continue
            if collect:
                lines.append(r)
                if any(r.startswith(a) for a in accept) and len(lines) >= 3:
                    return lines
                continue
            if r.startswith('ERR:'):
                return r
            if not accept or any(r == a or r.startswith(a) for a in accept):
                return r
            self._audit('SERIAL_UNMATCHED', 'IGNORED', r[:120])
        return lines if collect else None

    # ---- connection
    def connect(self, port=None):
        if not port or str(port).upper() == 'AUTO':
            avail = self.discover_pico_ports()
            if len(avail) != 1:
                raise RuntimeError('Pico 2 auto-detection requires exactly one Raspberry Pi USB serial device')
            port = avail[0][0]
        if self.connected:
            self.disconnect()
        if serial is None and self.serial_factory is None:
            raise RuntimeError('Install pyserial')
        self.port = str(port)
        factory = self.serial_factory or serial.Serial
        self.ser = factory(self.port, 115200, timeout=0.15, write_timeout=0.5)
        time.sleep(0.05 if self.serial_factory else 0.35)
        with self.lock:
            lines = self._tx('PING', timeout=1.5, collect=True, accept=('HUMAN=',)) or []
            build = next((l for l in lines if l.startswith('S-0.')), None)
            if PROTOCOL not in lines or build not in SUPPORTED_BUILDS:
                try:
                    self.ser.close()
                finally:
                    self.ser = None
                raise RuntimeError(f'Unsupported firmware: {lines[:3]}')
            self.version = build
            st = self._tx('STATUS', timeout=1.0, accept=('STATUS,',)) or ''
            self.held = st.split('hold=1:')[1].split(',')[0] if 'hold=1:' in st else None
            self.locked = bool(self.held) and (build in ALWAYS_LOCKED_BUILDS or 'lock=1' in st)
            self.armed = False
            self.connected = True
            self.stop_event.clear(); self.emergency.clear(); self.epoch += 1
        self.thread = threading.Thread(target=self._heartbeat_loop, daemon=True, name='pico-s02-heartbeat')
        self.thread.start()
        self._audit('CONNECT', 'PASS', build)
        self._status('CONNECTED / DISARMED ' + self.port + (' / HOLD ' + self.held if self.held else ''))
        return True

    def _heartbeat_loop(self):
        while not self.stop_event.is_set():
            if self.emergency.is_set():
                self._do_emergency()
            try:
                with self.lock:
                    hb = self._tx('HEARTBEAT', timeout=0.6, accept=('ACK:HEARTBEAT',))
            except Exception as e:
                hb = None
                self._audit('HEARTBEAT', 'ERROR', str(e)[:100])
            if not hb or not hb.startswith('ACK:HEARTBEAT'):
                if self.armed:
                    self._status('HEARTBEAT LOST / DISARMED')
                self.armed = False
                self._audit('HEARTBEAT', 'FAIL', str(hb))
                if self.stop_event.wait(self.heartbeat_sec):
                    break
                continue
            self.last_heartbeat = time.monotonic()
            self.stop_event.wait(self.heartbeat_sec)

    def _do_emergency(self):
        with self.lock:
            a = self._tx('STOP', timeout=0.6, accept=('ACK:STOPPED',))
            b = self._tx('DISARM', timeout=0.6, accept=('ACK:DISARMED',))
        self._audit('EMERGENCY_STOP', 'PASS' if (a or b) else 'UNCONFIRMED')
        self._status('DISARMED' if (a or b) else 'STOP UNCONFIRMED')
        self.emergency.clear()

    # ---- commands
    def _cmd(self, cmd, timeout):
        kind = cmd.split(',', 1)[0]
        expected = {'ARM': ('ACK:ARMED',), 'DISARM': ('ACK:DISARMED',), 'STOP': ('ACK:STOPPED',),
                    'MOVE': ('ACK:CMD_OK',), 'CLICK': ('ACK:CMD_OK',), 'TYPE': ('ACK:CMD_OK',),
                    'HOLD': ('ACK:HOLD,',), 'HOLDLOCK': ('ACK:HOLDLOCK,',), 'RELEASE': ('ACK:RELEASE',), 'BEEP': ('ACK:BEEP,',),
                    'STATUS': ('STATUS,',), 'FEATURES': ('STATUS,',), 'DIAG': ('STATUS,',),
                    'TIME_STATUS': ('STATUS,',)}.get(kind)
        if expected is None:
            raise ValueError('Unsupported command for S-0.2.1x: ' + kind)
        wire = 'STATUS' if kind in ('FEATURES', 'DIAG', 'TIME_STATUS') else cmd
        with self.lock:
            r = self._tx(wire, timeout=timeout, accept=expected)
        ok = bool(r) and not r.startswith('ERR:') and any(r.startswith(e) for e in expected)
        self._audit(kind, 'PASS' if ok else 'FAIL', (r or 'NO_RESPONSE')[:100])
        return ok, r

    def request(self, command):
        """Fire-and-forget (UI thread safe): runs in a short worker thread."""
        if not self.connected:
            raise RuntimeError('Not connected')
        if command.startswith(('MOVE,', 'CLICK', 'TYPE,')) and not self.armed:
            raise RuntimeError('Device not armed')
        def run():
            try:
                kind = command.split(',', 1)[0]
                if kind == 'FEATURE':
                    self._audit('FEATURE', 'LOCAL_ONLY', command[:60]); return
                if kind == 'ARM' and time.monotonic() - self.last_heartbeat > 2:
                    self._audit('ARM', 'REJECTED_NO_HEARTBEAT'); return
                ok, r = self._cmd(command, 1.0)
                if kind == 'ARM':
                    self.armed = ok
                    self._status(('ARMED' if ok else 'ARM FAILED: ' + str(r)) + ' / ' + str(self.port))
                elif kind in ('DISARM', 'STOP'):
                    self.armed = False
                    self._status('DISARMED / ' + str(self.port))
            except Exception as e:
                self._status('DEVICE ERROR: ' + str(e))
        threading.Thread(target=run, daemon=True).start()

    def request_confirmed(self, command, timeout=3.0):
        if not self.connected:
            raise RuntimeError('Pico 2 link not ready')
        kind = str(command).split(',', 1)[0]
        if kind not in ('FEATURES', 'TIME_STATUS', 'DIAG', 'STATUS', 'MOVE', 'CLICK', 'TYPE'):
            raise ValueError('Unsupported confirmed command: ' + kind)
        if kind in ('MOVE', 'CLICK', 'TYPE'):
            if not self.armed:
                raise RuntimeError('Pico 2 is DISARMED')
            if self.held and self.locked:
                raise RuntimeError('Pico 2 HOLD (' + self.held + '): human must resolve first')
            if kind == 'TYPE':
                timeout = max(timeout, 3.0 + 0.25 * len(command))
            else:
                timeout = max(timeout, 8.0)   # human-level paths take a few seconds
        gen = self.epoch
        ok, r = self._cmd(command, timeout)
        if gen != self.epoch or self.emergency.is_set():
            raise RuntimeError('Cancelled by emergency stop')
        if not ok:
            if kind in ('MOVE', 'CLICK', 'TYPE'):
                self.emergency_stop()   # no retry: stop and DISARM
            raise RuntimeError('Command not acknowledged: ' + str(r))
        return r

    def set_feature(self, name, enabled):
        name = name.upper()
        if name not in FEATURES:
            raise ValueError('Unknown feature')
        self.feature_settings[name] = bool(enabled)

    def arm(self):
        if self.held and self.locked:
            raise RuntimeError('HOLD active: resolve and RELEASE first')
        self.request('ARM')

    def disarm(self):
        self.request('DISARM')

    def emergency_stop(self):
        self.armed = False
        self.epoch += 1
        self.emergency.set()
        if self.connected and (self.thread is None or not self.thread.is_alive()):
            self._do_emergency()
        self._status('EMERGENCY STOP REQUESTED')

    # ---- optional HOLD (settings policy.pico_hold: 'signal' | 'lock'; default 'off' = never called)
    def hold(self, reason, lock=False):
        reason = str(reason).upper()
        if reason not in HOLD_REASONS:
            raise ValueError('bad hold reason')
        if not self.connected:
            return False
        if lock and self.version not in ALWAYS_LOCKED_BUILDS:
            ok, _ = self._cmd('HOLDLOCK,1', 1.0)
            if not ok:
                return False
        ok, _ = self._cmd('HOLD,' + reason, 1.0)
        if ok:
            self.held = reason
            self.locked = bool(lock) or self.version in ALWAYS_LOCKED_BUILDS
        return ok

    def release(self):
        was_locked = self.locked
        self.held = None
        self.locked = False
        if not self.connected:
            return False
        ok, _ = self._cmd('RELEASE', 1.0)
        if was_locked and self.version not in ALWAYS_LOCKED_BUILDS:
            self._cmd('HOLDLOCK,0', 1.0)
        return ok

    def beep(self, n=3):
        if not self.connected:
            return False
        ok, _ = self._cmd('BEEP,%d' % int(n), 3.0)
        return ok

    def disconnect(self):
        self.emergency_stop()
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.connected = False
        self.armed = False
