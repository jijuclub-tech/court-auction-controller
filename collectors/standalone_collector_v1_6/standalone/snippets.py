# -*- coding: utf-8 -*-
"""조각그림 찾기 + 성공·실패 자동 순위 (문제 4, 9).

collector_kb/snippets/<이름>.png      : 조각그림 (여러 개면 <이름>__2.png ...)
collector_kb/snippet_rank.json        : {이름: {파일: {ok, fail, last_ok}}} 성공 많은 순으로 먼저 시도
찾는 범위는 넓은 띠(C7)로 잡고, 1/4로 줄여 훑은 뒤 1픽셀 단위로 다시 확인한다.

v1.5 다중 크기(맥락 그림):
  <이름>@2x.png / @4x.png / @8x.png  : 같은 중심에서 2·4·8배 넓게 자른 '맥락 그림'
  - 기본은 1배만 저장. 저장 시도(캡처) 10번째마다 2배, 100번째마다 4배, 300번째마다 8배를 1장 더.
  - 실패가 많은 이름은 실패 횟수가 문턱(기본 2→2배, 5→4배, 10→8배)을 넘을 때마다 그 크기를 1장 더.
  - 찾기: 큰 것→작은 것. 큰 그림은 '대략 어디쯤'만 정하고(성긴 비교), 클릭 좌표는 그 범위 안의 1배 조각으로만 정한다.
  - 저장 전에 ID/PW/캡차 입력칸(정확한 칸 상자)을 회색으로 가린다. 금지 그림(인증서 등)은 _forbidden/ 에 따로 두고
    찾기에 쓰지 않는다(근처에 금지 그림이 보이면 그 자리는 거부).
  - 성공·실패와 순위는 파일별(=크기별)로 따로 쌓고, 크기 순서도 크기별 성공률로 자동으로 바뀐다.
"""
from __future__ import annotations

import glob
import math
import json
import os
import time


def _np():
    try:
        import numpy as np
        return np
    except Exception:  # noqa: BLE001
        return None


def match(im, tmpl, box, thr=12.0, k=4):
    """im·tmpl: PIL. box 안에서 tmpl 가운데 좌표 (x, y, score) 또는 None. score 작을수록 비슷."""
    np = _np()
    reg = im.crop(box).convert("L")
    t = tmpl.convert("L")
    tw, th = t.size
    rw, rh = reg.size
    if tw > rw or th > rh or tw < 4 or th < 4:
        return None
    if np is None:
        from PIL import ImageChops, ImageStat
        best = None
        for y in range(0, rh - th + 1, 2):
            for x in range(0, rw - tw + 1, 2):
                s = ImageStat.Stat(ImageChops.difference(reg.crop((x, y, x + tw, y + th)), t)).mean[0]
                if best is None or s < best[2]:
                    best = (box[0] + x + tw // 2, box[1] + y + th // 2, round(s, 2))
        return best if best and best[2] <= thr else None
    from numpy.lib.stride_tricks import sliding_window_view as swv
    R = np.asarray(reg, dtype=np.float32)
    T = np.asarray(t, dtype=np.float32)
    kk = k if (tw // k >= 4 and th // k >= 4) else 1
    rs = np.asarray(reg.resize((max(1, rw // kk), max(1, rh // kk))), dtype=np.float32) if kk > 1 else R
    ts = np.asarray(t.resize((max(1, tw // kk), max(1, th // kk))), dtype=np.float32) if kk > 1 else T
    sc = np.abs(swv(rs, ts.shape) - ts).mean(axis=(2, 3))
    best = None
    for c in np.argsort(sc, axis=None)[:5]:
        cy, cx = divmod(int(c), sc.shape[1])
        x0, y0 = max(0, cx * kk - 2 * kk), max(0, cy * kk - 2 * kk)
        x1, y1 = min(rw, cx * kk + tw + 2 * kk), min(rh, cy * kk + th + 2 * kk)
        sub = R[y0:y1, x0:x1]
        if sub.shape[0] < th or sub.shape[1] < tw:
            continue
        f = np.abs(swv(sub, T.shape) - T).mean(axis=(2, 3))
        yy, xx = divmod(int(np.argmin(f)), f.shape[1])
        v = float(f[yy, xx])
        if best is None or v < best[2]:
            best = (box[0] + x0 + xx + tw // 2, box[1] + y0 + yy + th // 2, round(v, 2))
    return best if best and best[2] <= thr else None


def coarse_match(im, tmpl, box, thr=14.0, target_w=40):
    """큰 맥락 그림용 성긴 비교: 둘 다 같은 비율로 줄여 훑는다. (왼쪽위x, 왼쪽위y, score) 또는 None."""
    np = _np()
    reg = im.crop(box).convert("L")
    t = tmpl.convert("L")
    tw, th = t.size
    rw, rh = reg.size
    if tw > rw or th > rh or tw < 4 or th < 4:
        return None
    k = max(1, int(math.ceil(max(tw / float(target_w), th / 12.0))))
    if tw // k < 4 or th // k < 4:
        k = max(1, min(tw // 4, th // 4))
    rs, ts = reg.resize((max(1, rw // k), max(1, rh // k))), t.resize((max(1, tw // k), max(1, th // k)))
    if np is None:
        from PIL import ImageChops, ImageStat
        best = None
        for y in range(0, rs.size[1] - ts.size[1] + 1):
            for x in range(0, rs.size[0] - ts.size[0] + 1):
                v = ImageStat.Stat(ImageChops.difference(rs.crop((x, y, x + ts.size[0], y + ts.size[1])), ts)).mean[0]
                if best is None or v < best[2]:
                    best = (box[0] + x * k, box[1] + y * k, round(v, 2))
        return best if best and best[2] <= thr else None
    from numpy.lib.stride_tricks import sliding_window_view as swv
    R = np.asarray(rs, dtype=np.float32)
    T = np.asarray(ts, dtype=np.float32)
    if T.shape[0] > R.shape[0] or T.shape[1] > R.shape[1]:
        return None
    sc = np.abs(swv(R, T.shape) - T).mean(axis=(2, 3))
    yy, xx = divmod(int(np.argmin(sc)), sc.shape[1])
    v = float(sc[yy, xx])
    return (box[0] + xx * k, box[1] + yy * k, round(v, 2)) if v <= thr else None


SCALES = (2, 4, 8)
DEFAULT_SCALE_CFG = {"every": {"2": 10, "4": 100, "8": 300},      # 캡처 n번째마다 그 크기 1장
                     "fail": {"2": 2, "4": 5, "8": 10},            # 실패 횟수 문턱 → 그 크기 1장
                     "max_per_scale": 3, "mask_fill": 128}


def _scale_cfg(c):
    out = {"every": dict(DEFAULT_SCALE_CFG["every"]), "fail": dict(DEFAULT_SCALE_CFG["fail"]),
           "max_per_scale": DEFAULT_SCALE_CFG["max_per_scale"], "mask_fill": DEFAULT_SCALE_CFG["mask_fill"]}
    for k in ("every", "fail"):
        for s, v in ((c or {}).get(k) or {}).items():
            try:
                if int(s) in SCALES and int(v) >= 1:
                    out[k][str(int(s))] = int(v)
            except (TypeError, ValueError):
                pass
    for k in ("max_per_scale", "mask_fill"):
        try:
            if (c or {}).get(k) is not None:
                out[k] = int(c[k])
        except (TypeError, ValueError):
            pass
    return out


def apply_masks(crop, crop_box, mask_boxes, fill=128):
    """crop_box(화면 좌표) 안에 걸친 가림 상자를 회색으로 칠한다. 가린 비율(0~1)을 돌려줌."""
    if not mask_boxes:
        return 0.0
    from PIL import ImageDraw
    d = ImageDraw.Draw(crop)
    nb = len(crop.getbands())
    fill = fill if nb == 1 else tuple([fill] * min(3, nb) + ([255] if nb == 4 else []))
    cw, ch = crop.size
    area = 0
    for b in mask_boxes:
        x0, y0 = max(crop_box[0], int(b[0])), max(crop_box[1], int(b[1]))
        x1, y1 = min(crop_box[2], int(b[2])), min(crop_box[3], int(b[3]))
        if x1 > x0 and y1 > y0:
            d.rectangle([x0 - crop_box[0], y0 - crop_box[1], x1 - crop_box[0] - 1, y1 - crop_box[1] - 1], fill=fill)
            area += (x1 - x0) * (y1 - y0)
    return min(1.0, area / float(max(1, cw * ch)))


class SnippetBank:
    META = "__meta__"

    def __init__(self, kb_dir, now=time.time, site=None, scale_cfg=None, mask_fn=None, mem=None, gallery_max=3):
        self.root = os.path.join(kb_dir, "snippets")
        self.dir = os.path.join(self.root, site) if site else self.root   # 사이트 폴더 먼저, 없으면 공통
        self.forbid_dir = os.path.join(self.root, "_forbidden")          # 금지 그림(인증서 등): 찾기에 안 씀
        self.rank_path = os.path.join(kb_dir, "snippet_rank.json")
        self.now = now
        self.scfg = _scale_cfg(scale_cfg)
        self.mask_fn = mask_fn            # mask_fn(im) -> [(x0,y0,x1,y1), ...] ID/PW/캡차 입력칸(정확한 칸)
        self.last_scale = None
        # v1.6: mem(MemoryPack)이 있으면 작업 중에는 메모리 자료만 읽고 쓴다(디스크 안 씀)
        self.mem = mem
        self.site = site
        self.gallery_max = int(gallery_max)
        self.mrel = ("snippets/%s" % site) if site else "snippets"
        if mem is None:
            os.makedirs(self.dir, exist_ok=True)
        try:
            from .crypto import load_json
            self.rank = load_json(self.rank_path)                # v1.6(t850u): 암호화 저장(옛 평문도 읽음)
        except Exception:  # noqa: BLE001
            self.rank = {}
        m = self.rank.setdefault(self.META, {})
        for k in ("fails", "fail_saved", "scale_stats", "centers"):
            m.setdefault(k, {})
        m.setdefault("captures", 0)

    @property
    def meta(self):
        return self.rank[self.META]

    # ---------- 파일 ----------
    def files(self, name, scale=1):
        fs = []
        if scale == 1:
            pats = (name + ".png", name + "__*.png")
        else:
            pats = ("%s@%dx.png" % (name, scale), "%s@%dx__*.png" % (name, scale))
        if self.mem is not None:
            for d in dict.fromkeys((self.mrel, "snippets")):
                for pt in pats:
                    fs += ["mem:" + q for q in self.mem.list(d + "/" + pt) if "/" not in q[len(d) + 1:]]
        else:
          for d in dict.fromkeys((self.dir, self.root)):
            for pt in pats:
                fs += glob.glob(os.path.join(d, pt))
        st = self.rank.get(name, {})

        def score(p):
            r = st.get(os.path.basename(p), {})
            ok, fail = r.get("ok", 0), r.get("fail", 0)
            return (-(ok + 1) / (ok + fail + 2), -r.get("last_ok", 0))   # 성공률 높고 최근 성공한 것 먼저
        return sorted(dict.fromkeys(fs), key=score)

    def forbidden_files(self, name=None):
        pat = (name + "*.png") if name else "*.png"
        if self.mem is not None:
            return ["mem:" + q for q in self.mem.list("snippets/_forbidden/" + pat)]
        return sorted(glob.glob(os.path.join(self.forbid_dir, pat)))

    # ---------- 저장소(디스크 또는 메모리) ----------
    def _open(self, p):
        from PIL import Image
        if str(p).startswith("mem:"):
            return self.mem.open_image(p[4:])
        with Image.open(p) as f:
            return f.convert("RGB")

    def _exists(self, fn, forbidden=False, sub=""):
        if self.mem is not None:
            base = "snippets/_forbidden" if forbidden else self.mrel + (("/" + sub) if sub else "")
            return self.mem.exists(base + "/" + fn)
        d = self.forbid_dir if forbidden else os.path.join(self.dir, sub) if sub else self.dir
        return os.path.exists(os.path.join(d, fn))

    def _put(self, fn, im, forbidden=False, sub="", kind="snippet", **meta):
        if self.mem is not None:
            base = "snippets/_forbidden" if forbidden else self.mrel + (("/" + sub) if sub else "")
            self.mem.put_image(base + "/" + fn, im, kind=("forbidden" if forbidden else kind), **meta)
            return
        d = self.forbid_dir if forbidden else os.path.join(self.dir, sub) if sub else self.dir
        os.makedirs(d, exist_ok=True)
        im.save(os.path.join(d, fn))

    def _mark(self, name, fn, ok, save=True):
        r = self.rank.setdefault(name, {}).setdefault(fn, {"ok": 0, "fail": 0, "last_ok": 0})
        r["ok" if ok else "fail"] += 1
        if ok:
            r["last_ok"] = int(self.now())
        if save:
            self.save()

    def _mark_scale(self, name, scale, ok):
        st = self.meta["scale_stats"].setdefault(name, {}).setdefault(str(scale), {"ok": 0, "fail": 0})
        st["ok" if ok else "fail"] += 1

    def scale_order(self, name):
        """크기별 성공률로 순위(같으면 큰 것 먼저). 파일이 있는 크기만."""
        st = self.meta["scale_stats"].get(name, {})
        have = [s for s in SCALES if self.files(name, s)]

        def key(s):
            r = st.get(str(s), {})
            ok, fail = r.get("ok", 0), r.get("fail", 0)
            return (-(ok + 1) / (ok + fail + 2), -s)
        return sorted(have, key=key)

    def save(self):
        from .crypto import save_json
        save_json(self.rank_path, self.rank)                     # v1.6(t850u): 디스크에는 암호문만

    def note_fail(self, name):
        """이 이름을 못 찾았음(단계 실패). 실패 문턱을 넘으면 다음 learn 때 큰 그림을 더 저장."""
        f = self.meta["fails"]
        f[name] = int(f.get(name, 0)) + 1
        self.save()
        return f[name]

    # ---------- 찾기 ----------
    def _match_1x(self, im, name, box, thr, mark=True):
        from PIL import Image
        for p in self.files(name, 1):
            try:
                hit = match(im, self._open(p), box, thr)
            except Exception:  # noqa: BLE001
                hit = None
            fn = os.path.basename(p)
            if mark:
                self._mark(name, fn, bool(hit), save=False)
            if hit:
                return hit[0], hit[1], fn
        return None

    def forbidden_near(self, im, x, y, pad=(160, 60), thr=10.0):
        """금지 그림이 (x,y) 근처에 보이면 그 파일 이름."""
        from PIL import Image
        W, H = im.size
        box = (max(0, int(x) - pad[0]), max(0, int(y) - pad[1]), min(W, int(x) + pad[0]), min(H, int(y) + pad[1]))
        for p in self.forbidden_files():
            try:
                if match(im, self._open(p), box, thr):
                    return os.path.basename(p)
            except Exception:  # noqa: BLE001
                pass
        return None

    def find(self, im, name, box, thr=12.0):
        """큰 맥락 그림(순위 순)으로 범위를 좁히고, 그 안에서 1배 조각으로 좌표를 정한다.
        (x, y, 1배파일이름) 또는 None. 결과는 파일별·크기별 순위에 반영."""
        from PIL import Image
        if box is None:
            box = (0, 0) + tuple(im.size)
        W, H = im.size
        self.last_scale = None
        try:
            for s in self.scale_order(name):
                for p in self.files(name, s):
                    fn = os.path.basename(p)
                    try:
                        big = self._open(p)
                        hit = coarse_match(im, big, box, thr=thr * 1.2)
                    except Exception:  # noqa: BLE001
                        hit = None
                    if not hit:
                        self._mark(name, fn, False, save=False)
                        self._mark_scale(name, s, False)
                        continue
                    cx, cy = self.meta["centers"].get(fn, (big.size[0] // 2, big.size[1] // 2))
                    px, py = hit[0] + cx, hit[1] + cy             # 큰 그림이 말하는 1배 중심(대략)
                    ws = [self._open(q).size for q in self.files(name, 1)[:1]] or [(120, 28)]
                    tw, th = ws[0]
                    sub = (max(box[0], px - tw), max(box[1], py - th), min(box[2], px + tw), min(box[3], py + th))
                    one = self._match_1x(im, name, sub, thr) if sub[2] > sub[0] and sub[3] > sub[1] else None
                    self._mark(name, fn, bool(one), save=False)
                    self._mark_scale(name, s, bool(one))
                    if one:
                        self._mark_scale(name, 1, True)
                        self.last_scale = s
                        return one                               # 좌표는 1배 조각으로만
            one = self._match_1x(im, name, box, thr)
            self._mark_scale(name, 1, bool(one))
            if one:
                self.last_scale = 1
            return one
        finally:
            self.save()

    # ---------- 배우기 ----------
    def _masks(self, im, extra):
        out = list(extra or [])
        if self.mask_fn is not None:
            try:
                out += list(self.mask_fn(im) or [])
            except Exception:  # noqa: BLE001
                pass
        return out

    def _due_scales(self, name):
        c = int(self.meta.get("captures", 0))
        due = []
        for s in SCALES:
            every = self.scfg["every"].get(str(s))
            if every and c > 0 and c % every == 0:
                due.append((s, "every"))
        fails = int(self.meta["fails"].get(name, 0))
        saved = self.meta["fail_saved"].setdefault(name, [])
        for s in SCALES:
            thr = self.scfg["fail"].get(str(s))
            if thr and fails >= thr and s not in saved and all(s != d for d, _ in due):
                due.append((s, "fail"))
        return due

    def _save_scale(self, im, name, x, y, wh, s, masks):
        w, h = wh[0] * s, wh[1] * s
        W, H = im.size
        x0, y0 = max(0, int(x) - w // 2), max(0, int(y) - h // 2)
        x1, y1 = min(W, x0 + w), min(H, y0 + h)
        x0, y0 = max(0, x1 - w), max(0, y1 - h)       # 가장자리면 안쪽으로 밀어 같은 중심을 최대한 유지
        if len(self.files(name, s)) >= self.scfg["max_per_scale"]:
            return None
        crop = im.crop((x0, y0, x1, y1))
        apply_masks(crop, (x0, y0, x1, y1), masks, self.scfg["mask_fill"])
        k, fn = 1, "%s@%dx.png" % (name, s)
        while self._exists(fn):
            k += 1
            fn = "%s@%dx__%d.png" % (name, s, k)
        self._put(fn, crop, kind="scale%d" % s, step=name)
        self.meta["centers"][fn] = [int(x) - x0, int(y) - y0]
        return fn

    def learn(self, im, name, x, y, wh=(120, 28), max_files=5, mask_boxes=None, forbidden=False):
        """성공 위치를 새 조각그림으로 저장(같은 그림이면 저장 안 함). 저장한 1배 파일 이름 또는 None.
        캡처 횟수·실패 횟수에 따라 같은 중심의 2·4·8배 맥락 그림을 더 저장한다(self.last_extra)."""
        w, h = wh
        box1 = (max(0, int(x) - w // 2), max(0, int(y) - h // 2), int(x) + w // 2, int(y) + h // 2)
        crop = im.crop(box1)
        self.last_extra = []
        if forbidden:                                   # 금지 그림은 따로(찾기에 안 씀)
            k, fn = 1, name + ".png"
            while self._exists(fn, forbidden=True):
                k += 1
                fn = name + "__%d.png" % k
            self._put(fn, crop, forbidden=True, step=name)
            return None
        masks = self._masks(im, mask_boxes)
        apply_masks(crop, box1, masks, self.scfg["mask_fill"])
        self.meta["captures"] = int(self.meta.get("captures", 0)) + 1
        for s, why in self._due_scales(name):
            fn2 = self._save_scale(im, name, x, y, wh, s, masks)
            if fn2:
                self.last_extra.append(fn2)
                if why == "fail":
                    self.meta["fail_saved"].setdefault(name, []).append(s)
        out = None
        dup = False
        from PIL import Image as _I
        for p in self.files(name, 1):
            try:
                old = self._open(p)
                if old.size == crop.size and match(crop, old, (0, 0) + crop.size, thr=3.0):
                    dup = True
                    break
            except Exception:  # noqa: BLE001
                pass
        if not dup and len(self.files(name, 1)) < max_files:
            k = 1
            fn = name + ".png"
            while self._exists(fn):
                k += 1
                fn = name + "__%d.png" % k
            self._put(fn, crop, kind="snippet", step=name)
            out = fn
        self.save()
        return out

    # ---------- v1.6 전체 화면 화보(크기 등급 'full') ----------
    def gallery_files(self, step):
        if self.mem is not None:
            return ["mem:" + q for q in self.mem.list("%s/gallery/%s.png" % (self.mrel, step)) +
                    self.mem.list("%s/gallery/%s__*.png" % (self.mrel, step))]
        d = os.path.join(self.dir, "gallery")
        return sorted(glob.glob(os.path.join(d, step + ".png")) + glob.glob(os.path.join(d, step + "__*.png")))

    def gallery_distance(self, im, step):
        """지금 화면과 그 단계 화보들의 지각 해시 최소 거리(없으면 None)."""
        from .refpack import dhash, hamming
        h = dhash(im)
        ds = []
        for p in self.gallery_files(step):
            try:
                ds.append(hamming(h, dhash(self._open(p))))
            except Exception:  # noqa: BLE001
                pass
        return min(ds) if ds else None

    def save_gallery(self, im, step, mask_boxes=None, forbidden=False, representative=None, dup_dist=4):
        """단계 전체 화면 화보 저장. ID/PW/캡차 입력칸은 회색으로 가림. 금지 화면은 _forbidden/ 로.
        같은 단계의 첫 화보가 대표(representative). 거의 같은 화보(지각 해시 거리 ≤ dup_dist)는 다시 안 저장. 이름 또는 None."""
        from .refpack import dhash, hamming
        full = im.copy()
        masks = self._masks(im, mask_boxes)
        apply_masks(full, (0, 0) + tuple(im.size), masks, self.scfg["mask_fill"])
        h = dhash(full)
        if forbidden:
            k, fn = 1, "gallery_%s.png" % step
            while self._exists(fn, forbidden=True):
                k += 1
                fn = "gallery_%s__%d.png" % (step, k)
            self._put(fn, full, forbidden=True, step=step, phash=h)
            return None
        have = self.gallery_files(step)
        for p in have:
            try:
                if hamming(h, dhash(self._open(p))) <= dup_dist:
                    return None
            except Exception:  # noqa: BLE001
                pass
        if len(have) >= self.gallery_max:
            return None
        rep = (not have) if representative is None else bool(representative)
        k, fn = 1, step + ".png"
        while self._exists(fn, sub="gallery"):
            k += 1
            fn = "%s__%d.png" % (step, k)
        self._put(fn, full, sub="gallery", kind="gallery", step=step, representative=rep, phash=h)
        g = self.meta.setdefault("gallery", {})
        g[fn] = {"step": step, "representative": rep, "phash": h, "masked": len(masks)}
        self.save()
        return fn
