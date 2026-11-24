"""Pico 2 absolute units (0..10000) <-> screen pixels, measured with 5 points (4 corners near the edge + centre).

The Pico moves the REAL cursor; Windows reports where it actually landed (GetCursorPos, DPI-aware). An affine fit
of the 5 pairs absorbs the Windows display scale (125 / 150 %), the monitor layout and any offset. The fit is
checked on the points themselves and on one extra verification move: the target/actual gap must be <= tol_px.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

DEFAULT_POINTS = ((1000, 1000), (9000, 1000), (9000, 9000), (1000, 9000), (5000, 5000))
VERIFY_POINT = (3500, 6500)


def _solve3(m, v):
    """3x3 linear system (Gaussian elimination with pivoting)."""
    a = [list(m[i]) + [v[i]] for i in range(3)]
    for c in range(3):
        p = max(range(c, 3), key=lambda r: abs(a[r][c]))
        if abs(a[p][c]) < 1e-12:
            raise ValueError('calibration points are degenerate')
        a[c], a[p] = a[p], a[c]
        for r in range(3):
            if r != c:
                f = a[r][c] / a[c][c]
                for k in range(c, 4):
                    a[r][k] -= f * a[c][k]
    return [a[i][3] / a[i][i] for i in range(3)]


def fit_affine(src, dst):
    """Least squares dst = A * [sx, sy, 1] per axis. Returns ((a,b,c),(d,e,f))."""
    if len(src) < 3:
        raise ValueError('need >= 3 points')
    ata = [[0.0] * 3 for _ in range(3)]
    atx, aty = [0.0] * 3, [0.0] * 3
    for (sx, sy), (dx, dy) in zip(src, dst):
        row = (float(sx), float(sy), 1.0)
        for i in range(3):
            for j in range(3):
                ata[i][j] += row[i] * row[j]
            atx[i] += row[i] * dx
            aty[i] += row[i] * dy
    return tuple(_solve3(ata, atx)), tuple(_solve3(ata, aty))


def invert_affine(a):
    (p, q, r), (s, t, u) = a
    det = p * t - q * s
    if abs(det) < 1e-12:
        raise ValueError('calibration not invertible')
    ip, iq, is_, it = t / det, -q / det, -s / det, p / det
    return (ip, iq, -(ip * r + iq * u)), (is_, it, -(is_ * r + it * u))


def apply_affine(a, x, y):
    (p, q, r), (s, t, u) = a
    return (p * x + q * y + r, s * x + t * y + u)


class Calibration:
    def __init__(self, a, meta=None):
        self.a = (tuple(a[0]), tuple(a[1]))
        self.inv = invert_affine(self.a)
        self.meta = dict(meta or {})

    @classmethod
    def guess(cls, screen_w, screen_h):
        """Before calibration: Pico 0..10000 assumed to span the primary screen."""
        return cls(((screen_w / 10000.0, 0.0, 0.0), (0.0, screen_h / 10000.0, 0.0)), {'kind': 'guess'})

    def to_px(self, x, y):
        return apply_affine(self.a, x, y)

    def to_pico(self, px, py, clamp=True):
        x, y = apply_affine(self.inv, px, py)
        if clamp:
            x, y = min(10000, max(0, x)), min(10000, max(0, y))
        return (int(round(x)), int(round(y)))

    def px_per_unit(self):
        return (abs(self.a[0][0]), abs(self.a[1][1]))

    def to_dict(self):
        return {'affine': [list(self.a[0]), list(self.a[1])], **self.meta}

    def save(self, path):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.partial')
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(p)

    @classmethod
    def load(cls, path):
        d = json.loads(Path(path).read_text(encoding='utf-8'))
        a = d.pop('affine')
        return cls((tuple(a[0]), tuple(a[1])), d)

    def matches_screen(self, screen):
        """Still valid for this screen? (size / DPI / monitor count as measured)."""
        try:
            return (tuple(self.meta.get('screen_size') or ()) == tuple(screen.screen_size()) and
                    abs(float(self.meta.get('dpi_scale', 0)) - float(screen.dpi_scale())) < 0.01 and
                    int(self.meta.get('monitors', 0)) == int(screen.monitors()))
        except Exception:  # noqa: BLE001
            return False


def run_calibration(inp, screen, points=DEFAULT_POINTS, tol_px=3.0, settle=0.25, sleep=time.sleep, say=None):
    """inp: PicoInput (connected, setup done). Moves the real cursor to each point (start synced to the real cursor
    with the current guess), reads where it landed, fits, verifies. Returns (Calibration, report)."""
    say = say or (lambda m: None)
    w, h = screen.screen_size()
    guess = Calibration.guess(w, h)
    inp.calib = guess
    src, dst = [], []
    for i, (x, y) in enumerate(points):
        say(f'[calibrate] point {i + 1}/{len(points)} -> pico {x},{y}')
        inp.move_pico(x, y, check=False)
        sleep(settle)
        got = screen.cursor()
        src.append((x, y))
        dst.append(got)
        guess_px = guess.to_px(x, y)
        say(f'[calibrate]   landed at {got[0]},{got[1]} px (guess {guess_px[0]:.0f},{guess_px[1]:.0f})')
    a = fit_affine(src, dst)
    cal = Calibration(a)
    res = [((cal.to_px(*s)[0] - d[0]) ** 2 + (cal.to_px(*s)[1] - d[1]) ** 2) ** 0.5 for s, d in zip(src, dst)]
    inp.calib = cal
    # verification: a pixel target -> Pico units by the new fit -> where does the cursor land?
    vx, vy = cal.to_px(*VERIFY_POINT)
    target = (int(round(vx)), int(round(vy)))
    inp.move_px(*target, check=False)
    sleep(settle)
    got = screen.cursor()
    verr = ((got[0] - target[0]) ** 2 + (got[1] - target[1]) ** 2) ** 0.5
    ok = max(res) <= tol_px and verr <= tol_px
    meta = {'kind': 'measured', 'created': datetime.now().astimezone().isoformat(timespec='seconds'),
            'points': [{'pico': list(s), 'px': list(d), 'residual_px': round(r, 2)} for s, d, r in zip(src, dst, res)],
            'residual_max_px': round(max(res), 2), 'verify': {'target_px': list(target), 'got_px': list(got),
                                                              'error_px': round(verr, 2)},
            'tol_px': tol_px, 'ok': ok, 'screen_size': list(screen.screen_size()),
            'virtual_screen': list(screen.virtual_screen()), 'monitors': screen.monitors(),
            'dpi_scale': screen.dpi_scale(), 'px_per_unit': [round(v, 4) for v in cal.px_per_unit()]}
    cal.meta = meta
    return cal, meta
