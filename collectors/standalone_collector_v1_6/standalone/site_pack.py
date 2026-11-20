# -*- coding: utf-8 -*-
"""v1.6(t846u): 사이트별 '상세 학습자료 묶음(site_pack)' 형식 · 메모리 색인 · 만들기(build).

묶음 안 경로(관제기 매니페스트 path, 모두 메모리에 적재):
  snippets/<site>/<el>.png, <el>@2x.png, @4x, @8x       조각 그림(입력칸 가림 유지), 크기별
  snippets/_forbidden/...                                 금지 그림(인증서 등, 찾기에 안 씀)
  site_pack/<site>/gallery/<step>/<state>_<scale>.png    단계별 화보. state=normal|loading|error|popup,
                                                          scale=full(전체 화면)|2x|4x|8x(클릭 자리 확대)
  site_pack/<site>/steps.json      단계 설명: expect_words, click_box_r, next, react_avg_sec, react_adj_sec, complexity
  site_pack/<site>/errors.json     오류 대응표(코드 → 글자·대응·담당 owner) — decision_routing 과 연결
  site_pack/<site>/decision_routing.json  판단 담당표(이벤트 → 담당·횟수·초)
  site_pack/<site>/rank.json       그림별 성공·실패와 순위
  site_pack/<site>/timing.json     단계별 반응시간 표본(적응 대기)
  site_pack/<site>/knowhow/...     노하우·규칙(비밀값 제외)
모든 파일은 crypto.DataKey 로 암호화해 보관·전송(enc=BGX1). 매니페스트(manifest.json)는 경로·SHA·크기 같은 목록만(비밀 없음).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import time

STATES = ("normal", "loading", "error", "popup")
SCALES = ("full", "2x", "4x", "8x")
_STATE_WORDS = {"loading": ("loading", "load", "로딩", "처리중", "wait"),
                "error": ("error", "err", "오류", "429", "fail"),
                "popup": ("popup", "팝업", "layer", "modal")}
_FORBID_WORDS = ("cert", "인증서", "공동인증", "금융인증", "간편인증")
SECRET_NAME = re.compile(r"(secret|password|passwd|token|api[_-]?key|\.env$|credential|cookie|login_values|DATA_KEY)", re.I)
SECRET_TEXT = re.compile(rb"(sk-[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{20,}|"
                         rb"github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|"
                         rb"\"(password|passwd|pw|api_key|token)\"\s*:\s*\"[^\"]{3,}\")", re.I)


def norm(s):
    return re.sub(r"\s+", "", str(s or ""))


def sp(site, *parts):
    return "/".join(("site_pack", site) + tuple(parts))


# ---------------- 메모리 색인 ----------------
class SiteIndex:
    """단계·글자·그림 해시로 빠른 조회. 해시는 8비트 띠 8개(LSH)로 후보를 줄인 뒤 해밍 거리 확인(거리 ≤7 은 놓치지 않음)."""

    def __init__(self, pack):
        self.site = pack.site_id
        self.steps = {}
        b = pack.get(sp(self.site, "steps.json"))
        if b:
            try:
                self.steps = json.loads(b.decode("utf-8"))
            except ValueError:
                self.steps = {}
        self.by_step, self.by_word, self.hashes = {}, {}, []
        self.bands = [dict() for _ in range(8)]
        for p, m in pack.meta.items():
            st = m.get("step")
            if not st and p.startswith(sp(self.site, "gallery") + "/"):
                st = p.split("/")[4] if len(p.split("/")) > 4 else None
            if st:
                self.by_step.setdefault(st, []).append(p)
            h = m.get("phash")
            if h is not None:
                self._add_hash(int(h), p)
        for st, d in self.steps.items():
            for w in (d or {}).get("expect_words", []) or []:
                if norm(w):
                    self.by_word.setdefault(norm(w), set()).add(st)

    def _add_hash(self, h, p):
        i = len(self.hashes)
        self.hashes.append((h, p))
        for k in range(8):
            self.bands[k].setdefault((h >> (8 * k)) & 0xFF, []).append(i)

    def step(self, name):
        return {"desc": self.steps.get(name), "files": sorted(self.by_step.get(name, []))}

    def steps_for_text(self, text):
        """화면 글자에 보이는 expect_words 로 '지금 어느 단계 화면인지' 후보(맞은 글자 수 많은 순)."""
        t = norm(text)
        score = {}
        for w, sts in self.by_word.items():
            if w in t:
                for st in sts:
                    score[st] = score.get(st, 0) + 1
        return [s for s, _n in sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))]

    def near(self, h, dist=6):
        h = int(h)
        cand = set()
        for k in range(8):
            cand.update(self.bands[k].get((h >> (8 * k)) & 0xFF, ()))
        out = []
        for i in cand:
            d = bin(self.hashes[i][0] ^ h).count("1")
            if d <= dist:
                out.append((d, self.hashes[i][1]))
        return sorted(out)


# ---------------- 만들기 ----------------
def _is_secret(rel, data):
    if SECRET_NAME.search(rel):
        return "name"
    if rel.lower().endswith((".json", ".jsonl", ".md", ".txt")) and SECRET_TEXT.search(data):
        return "content"
    return None


def classify_shot(fn):
    stem = os.path.splitext(os.path.basename(fn))[0]
    low = stem.lower()
    if any(w in low for w in _FORBID_WORDS):
        return None, None, True
    state = "normal"
    for s, ws in _STATE_WORDS.items():
        if any(w in low for w in ws):
            state = s
            break
    step = re.sub(r"(?i)(_|-)?(normal|loading|load|error|err|popup|modal|layer|로딩|오류|팝업|\d{8}_\d{4,6}|\d{6,})", "", stem)
    step = re.sub(r"[^0-9A-Za-z가-힣_]+", "_", step).strip("_") or "screen"
    return step, state, False


def _zooms(im, center, mask_boxes, fill=128):
    """전체 + 2/4/8배 확대(클릭 자리 중심으로 W/s×H/s 잘라냄, 원래 해상도 유지). 입력칸은 가림."""
    from PIL import ImageDraw
    full = im.convert("RGB").copy()
    d = ImageDraw.Draw(full)
    for b in mask_boxes or ():
        d.rectangle(tuple(int(v) for v in b), fill=(fill, fill, fill))
    W, H = full.size
    cx, cy = center if center else (W // 2, H // 2)
    out = {"full": full}
    for s in (2, 4, 8):
        w, h = max(8, W // s), max(8, H // s)
        x0 = min(max(0, int(cx - w / 2)), W - w)
        y0 = min(max(0, int(cy - h / 2)), H - h)
        out["%dx" % s] = full.crop((x0, y0, x0 + w, y0 + h))
    return out


def _png(im):
    b = io.BytesIO()
    im.save(b, format="PNG", optimize=True)
    return b.getvalue()


def steps_from_specs(specs, timing=None):
    """사이트 JSON steps → 단계 설명."""
    out = {}
    timing = timing or {}
    for spec in specs:
        sts = spec.get("steps", [])
        for i, st in enumerate(sts):
            name = st.get("name") or st.get("el") or st.get("type")
            if not name:
                continue
            ew = list(st.get("expect_words") or ([st["expect"]] if st.get("expect") else []))
            nxt = None
            for st2 in sts[i + 1:]:
                nxt = st2.get("name") or st2.get("el") or st2.get("type")
                if nxt:
                    break
            t = timing.get(name) or timing.get("after_" + name) or {}
            avg = (float(t["sum"]) / t["count"]) if t.get("count") else None
            out[name] = {"type": st.get("type"), "el": st.get("el"), "expect_words": ew,
                         "words": list(st.get("words") or []), "click_box_r": st.get("box_r"),
                         "next": nxt, "react_avg_sec": round(avg, 3) if avg is not None else None,
                         "react_adj_sec": 0.0, "complexity": None}
    return out


def build(kb_dir, site, out_dir, shots_dir=None, grade=None, version=None, key=None, cfg=None,
          dup_dist=6, protect_top_pct=20, now=time.time):
    """collector_kb(+shots) → 암호화 묶음 out_dir/{manifest.json, files/<path>, SHA256SUMS.txt, build_report.json}."""
    from PIL import Image
    from .crypto import default_key
    from .refpack import site_grade, plan_prune, prune_report, dhash
    key = key or default_key()
    items = {}                 # path -> (bytes, meta)
    excluded = []
    host_short = site.split(".")[1] if site.count(".") >= 2 else site

    def add(path, data, **meta):
        items[path] = (data, meta)

    def read(fp):
        with open(fp, "rb") as f:
            return f.read()
    # 1) 조각 그림
    sroot = os.path.join(kb_dir, "snippets")
    for d, prefix in ((os.path.join(sroot, site), "snippets/%s/" % site), (sroot, "snippets/%s/" % site),
                      (os.path.join(sroot, "_forbidden"), "snippets/_forbidden/")):
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            fp = os.path.join(d, fn)
            if not os.path.isfile(fp) or not fn.lower().endswith(".png"):
                continue
            p = prefix + fn
            if p in items:
                continue
            m = re.search(r"@(\d)x", fn)
            kind = "forbidden" if "_forbidden" in prefix else ("scale" + m.group(1) if m else "snippet")
            el = re.sub(r"(@\dx)?(__\d+)?\.png$", "", fn)
            data = read(fp)
            try:
                ph = dhash(Image.open(io.BytesIO(data)))
            except Exception:  # noqa: BLE001
                ph = None
            add(p, data, kind=kind, step=el, phash=ph)
    # 2) 사이트 단계 JSON → steps.json
    specs = []
    sdir = os.path.join(kb_dir, "sites")
    if os.path.isdir(sdir):
        for fn in sorted(os.listdir(sdir)):
            if not fn.endswith(".json"):
                continue
            try:
                spec = json.load(open(os.path.join(sdir, fn), encoding="utf-8"))
            except ValueError:
                continue
            if site in str(spec.get("site", "")) or site in fn or (host_short and host_short in str(spec.get("site", ""))):
                specs.append(spec)
                add(sp(site, "specs", fn), read(os.path.join(sdir, fn)), kind="spec")
    timing = {}
    tp = os.path.join(kb_dir, "knowhow", "screen_timing_local.json")
    if os.path.exists(tp):
        try:
            from .crypto import load_json
            timing = load_json(tp, key)
        except Exception:  # noqa: BLE001
            timing = {}
    steps = steps_from_specs(specs, timing)
    # 3) 화보(shots): 전체 + 2/4/8배, 입력칸 가림, 금지 화면 분리
    reps = set()
    if shots_dir and os.path.isdir(shots_dir):
        mask_r = []
        for spec in specs:
            for k in ("redact_box_r", "input_box_r"):
                r = (spec.get("captcha") or {}).get(k)
                if r:
                    mask_r.append(r)
            for r in spec.get("mask_boxes_r", []) or []:
                mask_r.append(r)
        for root, _ds, fs in os.walk(shots_dir):
            for fn in sorted(fs):
                if not fn.lower().endswith(".png"):
                    continue
                step, state, forbidden = classify_shot(fn)
                try:
                    im = Image.open(os.path.join(root, fn)).convert("RGB")
                except Exception:  # noqa: BLE001
                    continue
                W, H = im.size
                boxes = [(r[0] * W, r[1] * H, r[2] * W, r[3] * H) for r in mask_r]
                if forbidden:
                    add("snippets/_forbidden/gallery_%s" % re.sub(r"[^0-9A-Za-z가-힣_.]+", "_", fn),
                        _png(_zooms(im, None, boxes)["full"]), kind="forbidden")
                    continue
                cb = (steps.get(step) or {}).get("click_box_r")
                center = (int((cb[0] + cb[2]) / 2 * W), int((cb[1] + cb[3]) / 2 * H)) if cb else None
                zs = _zooms(im, center, boxes)
                k = 1
                base = sp(site, "gallery", step, "%s_full.png" % state)
                while base in items:
                    k += 1
                    base = sp(site, "gallery", step, "%s__%d_full.png" % (state, k))
                tag = os.path.basename(base)[:-len("_full.png")]
                rep = (step, state) not in reps
                reps.add((step, state))
                for sc, zim in zs.items():
                    add(sp(site, "gallery", step, "%s_%s.png" % (tag, sc)), _png(zim),
                        kind="gallery" if sc == "full" else "gallery_" + sc, step=step, state=state, scale=sc,
                        representative=rep and sc == "full", phash=dhash(zim))
    # 4) 오류 대응표 + 판단 담당 연결
    routing = {}
    rp = os.path.join(kb_dir, "knowhow", "decision_routing.json")
    if os.path.exists(rp):
        try:
            from .crypto import load_json
            routing = load_json(rp, key)
        except Exception:  # noqa: BLE001
            routing = {}
    errors = {}
    ep = os.path.join(kb_dir, "errors", "error_codes.json")
    if os.path.exists(ep):
        try:
            ec = json.load(open(ep, encoding="utf-8"))
            ents = ec.get("entries", ec) if isinstance(ec, dict) else {}
            if isinstance(ents, list):
                ents = {str(e.get("code", i)): e for i, e in enumerate(ents) if isinstance(e, dict)}
            for code, e in (ents or {}).items():
                if not isinstance(e, dict):
                    continue
                ev = "error:" + str(code)
                errors[str(code)] = {"words": e.get("words") or e.get("match") or [], "action": e.get("action"),
                                     "event": ev, "owner": (routing.get(ev) or {}).get("owner", "PROGRAM")}
        except ValueError:
            pass
    # 5) 순위
    rank = {}
    rk = os.path.join(kb_dir, "snippet_rank.json")
    if os.path.exists(rk):
        try:
            from .crypto import load_json
            raw = load_json(rk, key)
            for el, st in raw.items():
                if el.startswith("__"):
                    continue
                for fn, r in (st or {}).items():
                    ok, fail = int(r.get("ok", 0)), int(r.get("fail", 0))
                    rank["snippets/%s/%s" % (site, fn)] = {"ok": ok, "fail": fail,
                                                          "rate": round((ok + 1) / (ok + fail + 2), 4)}
        except Exception:  # noqa: BLE001
            rank = {}
    order = sorted(rank, key=lambda p: -rank[p]["rate"])
    for i, p in enumerate(order):
        rank[p]["rank"] = i + 1
        if p in items:
            items[p][1]["success_rate"] = rank[p]["rate"]
    # 6) 노하우·규칙(비밀 제외)
    for sub in ("knowhow", "rules", "map"):
        d = os.path.join(kb_dir, sub)
        if not os.path.isdir(d):
            continue
        for root, _ds, fs in os.walk(d):
            for fn in sorted(fs):
                fp = os.path.join(root, fn)
                rel = os.path.relpath(fp, kb_dir).replace(os.sep, "/")
                if not fn.lower().endswith((".json", ".jsonl", ".md", ".txt", ".png")):
                    continue
                if fn in ("screen_timing_local.json", "decision_routing.json"):
                    continue
                data = read(fp)
                why = _is_secret(rel, data)
                if why:
                    excluded.append({"path": rel, "why": why})
                    continue
                add(sp(site, rel), data, kind="knowhow")
    for p in list(items):
        why = _is_secret(p, items[p][0])
        if why:
            excluded.append({"path": p, "why": why})
            del items[p]
    add(sp(site, "steps.json"), json.dumps(steps, ensure_ascii=False, indent=1).encode("utf-8"), kind="steps")
    add(sp(site, "errors.json"), json.dumps(errors, ensure_ascii=False, indent=1).encode("utf-8"), kind="errors")
    add(sp(site, "decision_routing.json"), json.dumps(routing, ensure_ascii=False).encode("utf-8"), kind="routing")
    add(sp(site, "rank.json"), json.dumps(rank, ensure_ascii=False).encode("utf-8"), kind="rank")
    add(sp(site, "timing.json"), json.dumps(timing, ensure_ascii=False).encode("utf-8"), kind="timing")
    # 7) 등급 상한(넘으면 정리: 중복·유사 → 오래된 것, 대표·상위 보호)
    g, limit, managed = site_grade(site, {"grade": grade, "managed": True} if grade else {"managed": True}, cfg)
    ts = int(now())
    enc = {p: key.encrypt(b) for p, (b, _m) in items.items()}
    entries = [dict(m, path=p, bytes=len(enc[p]), ts=m.get("ts", ts)) for p, (b, m) in items.items()]
    for e in entries:
        if e.get("kind") in ("steps", "errors", "routing", "rank", "timing", "spec"):
            e["representative"] = True                      # 설명·표는 지우지 않음
    keep, removed, protected = plan_prune(entries, limit, dup_dist, protect_top_pct)
    rep = prune_report(site, g, limit, entries, removed, protected, "build_site_pack", now=ts)
    keep = set(keep)
    files_dir = os.path.join(out_dir, "files")
    os.makedirs(files_dir, exist_ok=True)
    man_files = []
    for p in sorted(keep):
        b, m = items[p]
        fp = os.path.join(files_dir, *p.split("/"))
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb") as f:
            f.write(enc[p])
        man_files.append(dict({k: v for k, v in m.items() if v is not None}, path=p, bytes=len(enc[p]),
                              sha256=hashlib.sha256(enc[p]).hexdigest(), sha256_plain=hashlib.sha256(b).hexdigest(),
                              enc="BGX1", ts=ts))
    man = {"site_id": site, "grade": g, "managed": True, "version": version or time.strftime("%Y%m%d-%H%M%S"),
           "files": man_files, "used_bytes": sum(f["bytes"] for f in man_files), "limit_bytes": limit,
           "format": "site_pack/1"}
    json.dump(man, open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    report = {"site_id": site, "grade": g, "limit_bytes": limit, "files": len(man_files),
              "used_bytes": man["used_bytes"], "excluded_secret": excluded, "prune": rep}
    json.dump(report, open(os.path.join(out_dir, "build_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    lines = []
    for root, _ds, fs in os.walk(out_dir):
        for fn in fs:
            if fn == "SHA256SUMS.txt":
                continue
            fp = os.path.join(root, fn)
            rel = "./" + os.path.relpath(fp, out_dir).replace(os.sep, "/")
            lines.append("%s  %s" % (hashlib.sha256(open(fp, "rb").read()).hexdigest(), rel))
    open(os.path.join(out_dir, "SHA256SUMS.txt"), "w", encoding="utf-8").write("\n".join(sorted(lines, key=lambda l: l[66:])) + "\n")
    return report
