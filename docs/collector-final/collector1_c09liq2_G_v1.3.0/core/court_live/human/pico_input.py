"""PicoInput: every mouse/keyboard action of the S-0.3.24 collector goes through the Pico 2 (S-0.2.14-HUMAN-MOVE).

Rules (user request 2026-11-14):
  * connect -> PING build must be S-0.2.14-HUMAN-MOVE and CAPS must list MOVE_TO/HOVER/CLICK_DWELL/WHEEL/KEY,
    otherwise PicoMissing (the collector does not collect).
  * at connect: MOVE_CFG require_arm=1 when the firmware implements MOVE_CFG (1.0.x does not; skipped).
  * 1.0.x: one HEARTBEAT then ARM before input, DISARM at the end of session(); heartbeat thread stays running.
  * every action runs inside session(): confirmed ARM -> action -> confirmed DISARM.
  * every MOVE_TO carries the start point = the REAL Windows cursor (converted with the calibration), so the
    firmware path starts where the cursor really is (no first-point jump).
  * human takeover: cursor moved away from where we left it, or a non-Pico mouse/keyboard event (raw input
    watcher) -> emergency STOP (DISARM), HumanTakeover, wait until the human is idle, then retry the action.
"""
from __future__ import annotations

import contextlib
import math
import random
import time

DEFAULTS = {
    'require_arm': 1,
    'move_cfg': {},                 # extra MOVE_CFG keys (e.g. speed_pct) - optional
    'takeover_tol_px': 8,           # cursor further than this from where the Pico left it -> human took over
    'idle_resume_sec': 15,          # human must leave mouse/keyboard alone this long before we continue
    'max_takeovers': 3,
    'click_pre_ms': [140, 420],
    'click_hold_ms': [60, 140],
    'hover_chunk_ms': 1500,         # HOVER chunks (host heartbeat keeps running between chunks)
    'settle_sec': 0.12,
}
REQUIRED_CAPS = ('MOVE_TO', 'HOVER', 'CLICK_DWELL', 'WHEEL', 'KEY', 'WHERE')
# MOVE_CFG / BOUNDS are listed on 1.0.x CAPS but have no handler (ERR:UNKNOWN_CMD). setup() skips them.
# WHEEL and KEY END/HOME: PicoLinkFW1.connect live-probes 1.0.x and refuses to start if they are missing.


class PicoMissing(RuntimeError):
    """No usable Pico 2 (S-0.2.14 or 1.0.x with the required CAPS): the collector must not collect."""


class HumanTakeover(RuntimeError):
    pass


def _merge(cfg):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (cfg or {}).items() if v is not None})
    return out


class PicoInput:
    def __init__(self, link, screen, calib, cfg=None, sleep=time.sleep, now=time.time, rng=None, log=None,
                 wait_fn=None):
        self.link, self.screen, self.calib = link, screen, calib
        self.cfg = _merge(cfg)
        self.sleep, self.now = sleep, now
        self.rng = rng or random.Random()
        self.log = log or (lambda kind, **kw: None)
        self.wait_fn = wait_fn                      # collector.wait(sec, why) when running inside the collector
        self.deliberate_sec = 0.0
        self.depth = 0
        self.expect = None                          # where the Pico left the cursor (px)
        self.mark_ts = None                         # human input after this -> takeover
        self.takeover_flag = None
        self.history = []                           # (kind, detail) for tests / audit
        self.move_cfg = {}
        self.click_guard = None                     # t747u: callable((x, y)) - raises to refuse a click
        try:
            screen.start_watcher(self._on_human)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ connect
    @classmethod
    def open(cls, port, screen, calib, cfg=None, link_factory=None, log=None, **kw):
        # t734u 2026-11-19: firmware 1.0.x (1.0.2-FIX) or S-0.2.14; port AUTO = VID:PID 2E8A:000C, fallback COM8
        from ...pico_fw1_link import PicoLinkFW1, is_supported_build, SUPPORTED_DESC  # noqa: F401 (old path kept)
        # c09 2026-11-23 (DESKTOP-698F5NB, 1.0.8-v8 r2): PicoLinkV8 (core/pico_compat_v8.py = pico2/lg15_v8_upgrade_20261120/
        # pico_compat_v8_r2.py) speaks v8 and falls back to PicoLinkS0214 for old firmware. Port AUTO is resolved first with
        # pico_fw1_link.find_port: exactly one 2E8A:000C, else one 2E8A, else fallback (env BIDGUARD_PICO_PORT_FALLBACK, default COM4).
        import os as _os
        from ...pico_fw1_link import find_port
        from ...pico_compat_v8 import PicoLinkV8
        link = (link_factory or PicoLinkV8)()
        try:
            if link_factory is None and (not port or str(port).upper() == 'AUTO'):
                port, _how = find_port(fallback=_os.environ.get('BIDGUARD_PICO_PORT_FALLBACK') or 'COM4')
                log and log('pico_port', port=port, how=_how)
            link.connect(port)
        except Exception as e:  # noqa: BLE001
            raise PicoMissing('Pico 2 not connected (%s): %s' % (port or 'AUTO', e))
        if not is_supported_build(getattr(link, 'version', None)):
            link.disconnect()
            raise PicoMissing('Pico 2 firmware is %s, need %s' % (getattr(link, 'version', None), SUPPORTED_DESC))
        miss = [c for c in REQUIRED_CAPS if c not in (getattr(link, 'caps', ()) or ())]
        if miss:
            link.disconnect()
            raise PicoMissing('Pico 2 CAPS missing ' + ','.join(miss))
        inp = cls(link, screen, calib, cfg, log=log, **kw)
        inp.setup()
        return inp

    def setup(self):
        """MOVE_CFG require_arm=1 when the firmware has it; reset BOUNDS; start DISARMED.

        1.0.x (PR #10) lists MOVE_CFG and BOUNDS in CAPS but returns ERR:UNKNOWN_CMD. Skip those and
        still ARM/DISARM around input. The host already requires one HEARTBEAT before ARM.
        """
        kw = dict(self.cfg.get('move_cfg') or {})
        kw['require_arm'] = int(self.cfg.get('require_arm', 1))
        got = {}
        try:
            got = self.link.move_cfg(**kw)
            if kw['require_arm'] and int(got.get('require_arm', -1)) != 1:
                got = self.link.move_cfg()
                if int(got.get('require_arm', -1)) != 1:
                    raise PicoMissing('firmware did not accept MOVE_CFG require_arm=1 (%s)' % got)
        except PicoMissing:
            raise
        except Exception as e:  # noqa: BLE001
            # 1.0.x: no MOVE_CFG handler. ARM is still required by the session and by MOVE/CLICK/TYPE/KEY/DRAG.
            self._note('SETUP', 'MOVE_CFG skipped: %s' % e)
            got = {}
        self.move_cfg = got
        try:
            self.link.bounds(reset=True)
        except Exception:  # noqa: BLE001
            pass
        if getattr(self.link, 'armed', False):
            self._disarm()
        self._note('SETUP', 'require_arm=%s' % (got.get('require_arm') if got else 'host-session'))
        return got

    def close_session(self):
        """Make sure the Pico is DISARMED (the link stays connected)."""
        if getattr(self.link, 'armed', False):
            self._disarm()

    def close(self):
        try:
            if getattr(self.link, 'armed', False):
                self._disarm()
        finally:
            try:
                self.link.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ helpers
    def _note(self, kind, detail=''):
        self.history.append((kind, detail))
        self.log('PICO_' + kind, detail=str(detail)[:200])

    def _on_human(self, info=None):
        """Raw-input watcher thread: a real mouse/keyboard event while we are driving -> STOP at once."""
        if self.depth > 0 and self.takeover_flag is None:
            self.takeover_flag = 'human device input %s' % (info or '')
            try:
                self.link.emergency_stop()
            except Exception:  # noqa: BLE001
                pass

    def cursor(self):
        return self.screen.cursor()

    def check_takeover(self):
        if self.takeover_flag:
            why, self.takeover_flag = self.takeover_flag, None
            raise HumanTakeover(why)
        if self.expect is not None:
            cx, cy = self.cursor()
            if math.hypot(cx - self.expect[0], cy - self.expect[1]) > float(self.cfg['takeover_tol_px']):
                raise HumanTakeover('cursor moved by someone else: expected %s now %s' % (self.expect, (cx, cy)))
        if self.mark_ts is not None:
            hs = self.screen.human_since(self.mark_ts)
            if hs:
                raise HumanTakeover('human device input after the last Pico action')

    def _arm(self):
        t_end = time.monotonic() + 2.5
        while getattr(self.link, 'last_heartbeat', 1) == 0 and time.monotonic() < t_end:
            time.sleep(0.05)
        ok, r = self.link._cmd('ARM', 1.0)
        if not ok:
            raise RuntimeError('Pico 2 ARM not confirmed: %s' % r)
        self.link.armed = True
        self._note('ARM')

    def _disarm(self):
        ok, r = self.link._cmd('DISARM', 1.0)
        self.link.armed = False
        self._note('DISARM', '' if ok else 'UNCONFIRMED ' + str(r))
        if not ok:
            self.link.emergency_stop()

    @contextlib.contextmanager
    def session(self, label=''):
        """ARM before input, DISARM after (also on errors). Nested sessions share one ARM."""
        if self.depth == 0:
            self.check_takeover()
            self.takeover_flag = None
            self._arm()
        self.depth += 1
        try:
            yield self
        except RuntimeError as e:
            if self.takeover_flag and not isinstance(e, HumanTakeover):
                why, self.takeover_flag = self.takeover_flag, None
                raise HumanTakeover(why) from e
            raise
        finally:
            self.depth -= 1
            if self.depth == 0:
                try:
                    self._disarm()
                except Exception:  # noqa: BLE001
                    try:
                        self.link.emergency_stop()
                    except Exception:  # noqa: BLE001
                        pass
                self.expect = self.cursor()
                self.mark_ts = self.now()

    def _need(self):
        if self.depth <= 0:
            raise RuntimeError('Pico input outside an ARM session')
        if self.takeover_flag:
            why, self.takeover_flag = self.takeover_flag, None
            raise HumanTakeover(why)

    def act(self, fn, label='', on_takeover=None):
        """Run fn() inside one ARM session; on human takeover wait for idle and run it again (max N)."""
        last = None
        for _ in range(int(self.cfg['max_takeovers']) + 1):
            try:
                with self.session(label):
                    return fn()
            except HumanTakeover as e:
                last = e
                self._note('TAKEOVER', '%s: %s' % (label, e))
                if on_takeover:
                    on_takeover(e)
                self.wait_human_idle()
        raise HumanTakeover('gave up after %d takeovers (%s)' % (self.cfg['max_takeovers'], last))

    def _wait(self, sec, why):
        if self.wait_fn:
            self.wait_fn(sec, why)
        else:
            self.sleep(sec)

    def wait_human_idle(self, idle=None, why='human is using the mouse/keyboard'):
        idle = float(self.cfg['idle_resume_sec'] if idle is None else idle)
        last = self.cursor()
        quiet = self.now()
        while True:
            self._wait(0.5, why)
            p = self.cursor()
            if math.hypot(p[0] - last[0], p[1] - last[1]) > 1.5 or self.screen.human_since(quiet):
                last, quiet = p, self.now()
                continue
            if self.now() - quiet >= idle:
                break
        self.takeover_flag = None
        self.expect = self.cursor()
        self.mark_ts = self.now()

    # ------------------------------------------------------------------ input (inside session)
    def move_pico(self, x, y, check=True, dur=None):
        self._need()
        cx, cy = self.cursor()
        sx, sy = self.calib.to_pico(cx, cy)             # start = REAL cursor -> no first-point jump
        x, y = max(0, min(10000, int(round(x)))), max(0, min(10000, int(round(y))))
        if abs(sx - x) + abs(sy - y) < 3:
            return {'x': x, 'y': y, 'dur_ms': 0, 'points': 0}
        r = self.link.move_to(x, y, (sx, sy), dur)
        self.deliberate_sec += (r.get('dur_ms') or 0) / 1000.0
        self.sleep(float(self.cfg['settle_sec']))
        self._note('MOVE_TO', '%d,%d from %d,%d (%s)' % (x, y, sx, sy, r.get('points')))
        return r

    def move_px(self, px, py, dur=None, check=True):
        x, y = self.calib.to_pico(px, py)
        return self.move_pico(x, y, dur=dur)

    def hover(self, ms):
        self._need()
        left = int(ms)
        chunk = int(self.cfg['hover_chunk_ms'])
        while left > 0:
            n = min(chunk, left)
            self.link.hover(n)
            self.deliberate_sec += n / 1000.0
            left -= n
        self._note('HOVER', ms)

    def click(self, pre_ms=None, hold_ms=None):
        self._need()
        pre = int(pre_ms if pre_ms is not None else self.rng.randint(*self.cfg['click_pre_ms']))
        hold = int(hold_ms if hold_ms is not None else self.rng.randint(*self.cfg['click_hold_ms']))
        g = getattr(self, 'click_guard', None)      # t747u: 인증서 하드 가드(누르기 직전, 실제 커서 자리)
        if g is not None:
            g(self.cursor())
        self.link.click_dwell(pre, hold)
        self.deliberate_sec += (pre + hold) / 1000.0
        self._note('CLICK_DWELL', '%d,%d' % (pre, hold))

    def wheel(self, ticks):
        self._need()
        t = int(ticks)
        while t:
            n = max(-10, min(10, t))
            self.link.wheel(n)
            self.deliberate_sec += abs(n) * 0.1
            self._note('WHEEL', n)
            t -= n

    def key(self, combo, count=1):
        self._need()
        c = int(count)
        while c > 0:
            n = min(20, c)
            self.link.key(combo, n)
            c -= n
        self._note('KEY', '%s x%d' % (combo, int(count)))

    def type_text(self, text):
        self._need()
        s = str(text)
        if any(ord(ch) < 32 or ord(ch) > 126 for ch in s):
            raise ValueError('TYPE accepts printable ASCII only')
        for i in range(0, len(s), 60):
            self.link.request_confirmed('TYPE,' + s[i:i + 60], 3.0 + 0.25 * len(s[i:i + 60]))
        self.deliberate_sec += 0.12 * len(s)
        self._note('TYPE', '%d chars' % len(s))

    def has(self, cap):
        fn = getattr(self.link, 'has', None)
        if callable(fn):
            return bool(fn(cap))
        caps = tuple(getattr(self.link, 'caps', ()) or ())
        if cap in ('RIGHT_CLICK', 'RCLICK'):
            return 'RCLICK' in caps or 'RIGHT_CLICK' in caps
        return cap in caps

    def drag_px(self, px, py):
        if not self.has('DRAG'):
            raise RuntimeError('Firmware has no DRAG (CAPS) - caller must skip the drag step')
        self._need()
        cx, cy = self.cursor()
        x, y = self.calib.to_pico(px, py)
        # 1.0.3 DRAG,x,y is destination only (0..10000). Start point is the current cursor; firmware ignores extras.
        r = self.link.drag(x, y, self.calib.to_pico(cx, cy))
        self.deliberate_sec += (r.get('dur_ms') or 0) / 1000.0
        self._note('DRAG', '%d,%d' % (x, y))
        return r
