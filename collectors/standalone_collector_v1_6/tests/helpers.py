# -*- coding: utf-8 -*-
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from standalone import config  # noqa: E402


class Clock:
    def __init__(self, t=1_763_510_400.0):   # 2025-11-19 09:00 KST 근처(날짜 계산만)
        self.t = t
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += max(0.0, float(s))
        self.slept += max(0.0, float(s))


def blank(size=(1920, 1080), color="white"):
    return Image.new("RGB", size, color)


def textured(size=(200, 60), seed=1):
    """무늬 있는 그림(빈 그림 아님). seed 가 다르면 다른 그림 → 캡차 기억 열쇠도 다름(W1 시험용)."""
    import random as _r
    from PIL import Image, ImageDraw
    rnd = _r.Random(seed)
    im = Image.new("RGB", size, (235, 235, 235))
    d = ImageDraw.Draw(im)
    for _ in range(14):
        x, y = rnd.randrange(0, size[0]), rnd.randrange(0, size[1])
        d.rectangle([x, y, x + rnd.randrange(4, 18), y + rnd.randrange(4, 18)], fill=(rnd.randrange(0, 120),) * 3)
    return im


def tmp_cfg():
    d = tempfile.mkdtemp(prefix="sc_test_")
    cfg = config.load(os.path.join(d, "none.json"))
    cfg["collector_id"] = "metro-01"
    cfg["region"] = "metro"
    cfg["court_account_window"] = []
    cfg["state_dir"] = os.path.join(d, "state")
    cfg["log_dir"] = os.path.join(d, "logs")
    cfg["stop_dir"] = os.path.join(d, "stop")
    kb = os.path.join(d, "kb")
    import shutil
    shutil.copytree(os.path.join(ROOT, "collector_kb", "errors"), os.path.join(kb, "errors"))
    os.makedirs(os.path.join(kb, "snippets"))
    cfg["kb_dir"] = kb
    open(os.path.join(d, "nas_is_a_file"), "w").close()      # 이 아래로는 폴더를 못 만듦 = NAS 안 됨
    cfg["storage"] = {"nas_root": os.path.join(d, "nas_is_a_file", "court"), "nas_subst": {"drive": "X:", "target": ""},
                      "local_root": os.path.join(d, "D"), "fallback_root": os.path.join(d, "C")}
    for k in ("state_dir", "log_dir", "stop_dir"):
        os.makedirs(cfg[k], exist_ok=True)
    return cfg, d


class FakeSecrets:
    def __init__(self, vals=None):
        from standalone.secrets_store import Secret
        self.vals = {k: Secret(v) for k, v in (vals or {}).items()}
        self.asked = []

    def get(self, name, required=True):
        self.asked.append(name)
        return self.vals.get(name)

    def values(self):
        return [v.reveal() for v in self.vals.values()]
