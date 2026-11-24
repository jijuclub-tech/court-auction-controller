"""S-0.3.24 session: Pico 2 (S-0.2.14) + calibration + localhost bridge + read-only extension -> HumanChromeDriver.

Settings: pico_port / pico_protocol come from settings.json (read-only here, nothing else is read) with the
NON-SECRET overlay config/pico_s0324.json on top (written by UPDATE.bat: {"pico_protocol": "S-0.2.14"}). Secrets
files are never opened. If anything is missing, nothing is collected.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

OVERLAY = Path('config') / 'pico_s0324.json'
CALIB = Path('local_data') / 'pico_calibration.json'
PROTOCOL = 'S-0.2.14'


class CalibrationMissing(RuntimeError):
    pass


def pico_settings(base='.'):
    base = Path(base)
    out = {'pico_port': 'AUTO', 'pico_protocol': '', 'source': []}
    try:
        s = json.loads((base / 'settings.json').read_text(encoding='utf-8'))
        for k in ('pico_port', 'pico_protocol'):
            if s.get(k):
                out[k] = s[k]
        out['source'].append('settings.json')
    except (OSError, ValueError):
        pass
    try:
        o = json.loads((base / OVERLAY).read_text(encoding='utf-8'))
        for k in ('pico_port', 'pico_protocol'):
            if o.get(k):
                out[k] = o[k]
        out['source'].append(str(OVERLAY))
    except (OSError, ValueError):
        pass
    if os.environ.get('BIDGUARD_PICO_PORT'):
        out['pico_port'] = os.environ['BIDGUARD_PICO_PORT']
        out['source'].append('env BIDGUARD_PICO_PORT')
    return out


def check_protocol(ps):
    from .pico_input import PicoMissing
    # t734u: 1.0.x firmware accepted too ('pico_protocol': '1.0' or 'S-0.2.14'); the link checks the real build.
    if not str(ps.get('pico_protocol') or '').startswith((PROTOCOL, '1.0')):
        raise PicoMissing('pico_protocol is %r - S-0.3.24 needs %s (UPDATE.bat writes it to %s)'
                          % (ps.get('pico_protocol'), PROTOCOL, OVERLAY))


def load_calibration(screen, base='.', required=True):
    from .calibration import Calibration
    try:
        from .pico_profiles import load_matching
        found = load_matching(base, screen)
        if found is not None:
            return found
    except Exception:
        pass
    p = Path(base) / CALIB
    if not p.exists():
        if required:
            raise CalibrationMissing('no calibration yet (%s) - run PICO_CALIBRATE.bat first' % p)
        w, h = screen.screen_size()
        return Calibration.guess(w, h)
    cal = Calibration.load(p)
    if required and not cal.meta.get('ok'):
        raise CalibrationMissing('calibration %s did not pass - run PICO_CALIBRATE.bat again' % p)
    if required and not cal.matches_screen(screen):
        raise CalibrationMissing('screen size / scale / monitors changed since calibration - run PICO_CALIBRATE.bat')
    return cal


class HumanSession:
    def __init__(self, cfg, base='.', log=None, screen=None, link_factory=None, bridge=None):
        self.cfg, self.base = cfg, Path(base)
        self.log = log or (lambda kind, **kw: None)
        self.screen, self.link_factory, self.bridge = screen, link_factory, bridge
        self.inp = None
        self.driver = None

    def open_pico(self, need_calibration=True):
        from .pico_input import PicoInput
        if self.screen is None:
            from .winio import Screen
            self.screen = Screen()
        ps = pico_settings(self.base)
        check_protocol(ps)
        cal = load_calibration(self.screen, self.base, required=need_calibration)
        pc = (self.cfg.get('input') or {}).get('pico') or {}
        self.inp = PicoInput.open(ps['pico_port'], self.screen, cal, pc, link_factory=self.link_factory, log=self.log)
        return self.inp

    def open_bridge(self, wait=25.0):
        from .bridge import Bridge, ExtensionMissing
        hc = self.cfg.get('human_chrome') or {}
        if self.bridge is None:
            self.bridge = Bridge(int(hc.get('bridge_port', 17321)), extension_id=hc.get('extension_id', ''),
                                 log=self.log).start()
        if hasattr(self.bridge, 'wait_connected') and not self.bridge.wait_connected(wait):
            raise ExtensionMissing('the BIDGUARD reader extension did not connect within %.0fs - is Chrome open and '
                                   'the extension on (chrome://extensions)?' % wait)
        return self.bridge

    def start(self, need_calibration=True):
        from .driver import HumanChromeDriver
        self.open_pico(need_calibration)
        self.open_bridge()
        self.driver = HumanChromeDriver(self.cfg, self.bridge, self.inp, self.screen, base_dir=self.base, log=self.log)
        return self.driver

    def close(self):
        try:
            if self.inp is not None:
                self.inp.close()
        finally:
            if self.bridge is not None and hasattr(self.bridge, 'stop'):
                try:
                    self.bridge.stop()
                except Exception:  # noqa: BLE001
                    pass
