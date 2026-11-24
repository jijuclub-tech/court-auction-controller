# -*- coding: utf-8 -*-
"""Pico calibration profiles keyed by screen size + DPI scale + monitor count.

The 150% profile is kept forever. A new scale (e.g. 100%) is a sibling file, never
a replacement. pico_calibration.json is the *active* copy of the matching profile.
"""
from __future__ import annotations

import json
from pathlib import Path

PROFILES_DIR = Path('local_data') / 'pico_profiles'


def profile_key(screen=None, size=None, dpi_scale=None, monitors=None):
    if screen is not None:
        size = size or screen.screen_size()
        dpi_scale = screen.dpi_scale() if dpi_scale is None else dpi_scale
        monitors = screen.monitors() if monitors is None else monitors
    w, h = size
    scale = int(round(float(dpi_scale) * 100))
    mon = int(monitors)
    return '%dx%d_scale%d_mon%d' % (int(w), int(h), scale, mon)


def profiles_dir(base='.') -> Path:
    return Path(base) / PROFILES_DIR


def profile_path(base='.', screen=None, key=None) -> Path:
    key = key or profile_key(screen)
    return profiles_dir(base) / (key + '.json')


def load_matching(base, screen):
    """Return a Calibration if a profile (or only that profile) matches this screen. Else None."""
    from .calibration import Calibration
    p = profile_path(base, screen)
    if p.is_file():
        cal = Calibration.load(p)
        if cal.meta.get('ok') and cal.matches_screen(screen):
            return cal
    # search all profiles for a match (renames / older key formats)
    folder = profiles_dir(base)
    if folder.is_dir():
        for q in sorted(folder.glob('*.json')):
            try:
                cal = Calibration.load(q)
            except Exception:  # noqa: BLE001
                continue
            if cal.meta.get('ok') and cal.matches_screen(screen):
                return cal
    return None


def save_profile(cal, screen, base='.'):
    """Write this calibration into its screen-config profile AND the active pico_calibration.json.

    Existing profiles for other configs are left untouched.
    """
    from .setup import CALIB
    base = Path(base)
    dest = profile_path(base, screen)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cal.save(dest)
    cal.save(base / CALIB)
    # sidecar index
    idx = dest.parent / 'index.json'
    recs = []
    if idx.is_file():
        try:
            recs = json.loads(idx.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            recs = []
    key = profile_key(screen)
    recs = [r for r in recs if r.get('key') != key]
    recs.append({
        'key': key,
        'file': dest.name,
        'screen_size': list(screen.screen_size()),
        'dpi_scale': screen.dpi_scale(),
        'monitors': screen.monitors(),
        'ok': bool(cal.meta.get('ok')),
    })
    idx.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding='utf-8')
    return dest
