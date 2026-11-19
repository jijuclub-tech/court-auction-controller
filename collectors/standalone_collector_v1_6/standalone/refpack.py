# -*- coding: utf-8 -*-
"""사이트 참고자료(조각 그림·2/4/8배·전체 화면 화보·지도) — 관제기에서 받아 메모리에 적재 (v1.6, 대표 t844u).

정책(요지, 원문은 REFERENCE_POLICY_KO.md):
  * 온라인 전제. 관제기(=사무실 서버)에 못 붙으면 작업을 시작하지 않고 STOP 사유를 남긴다(폴백 없음).
  * 사이트별 용량 등급 A 500MB · B 100MB · C 10MB · D 1MB. 관제기가 관리하지 않는 사이트는 최대 100MB, 기본 C.
    법원경매(courtauction)는 기본 A(설정 site_grades 로 바꿈). 등급·사용량은 관제기 매니페스트로 정한다.
  * 실행 흐름: 접속 → 매니페스트 → 자료 받기(SHA 확인) → '해당 사이트 학습 중입니다…' 표시 → 전부 메모리 적재
    → 작업 중에는 메모리 자료만 → 끝나면 메모리 해제. 디스크는 받는 동안의 임시 캐시(같은 버전이면 다시 안 받음, SHA 확인).
  * 용량 초과: 중복·유사 화보(지각 해시 거리)부터, 그다음 오래된 것. 대표 화보·성공률 상위는 보호. 결과는 관제기 보고 JSON.
    올리기 전에도 등급 한도를 넘지 않게 한다.
"""
from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import io
import json
import os
import shutil
import time

MB = 1024 * 1024
GRADES = {"A": 500 * MB, "B": 100 * MB, "C": 10 * MB, "D": 1 * MB}
UNMANAGED_MAX = 100 * MB
DEFAULT_GRADE = "C"
DEFAULT_SITE_GRADES = {"courtauction": "A"}
KINDS = ("snippet", "scale2", "scale4", "scale8", "gallery", "map", "other")
LEARNING_MSG = "해당 사이트 학습 중입니다…"
KST = _dt.timezone(_dt.timedelta(hours=9))


class RefPackError(RuntimeError):
    """관제기·자료 문제로 작업을 시작하면 안 됨. reason 은 STOP 사유 코드."""

    def __init__(self, reason, detail=""):
        super().__init__(reason)
        self.reason, self.detail = reason, str(detail)[:200]


def _kst(ts=None):
    return _dt.datetime.fromtimestamp(time.time() if ts is None else ts, KST).isoformat(timespec="seconds")


# ---------------- 등급 ----------------
def site_grade(site_id, manifest=None, cfg=None):
    """(grade, limit_bytes, managed). 관제기 매니페스트가 우선, 없으면 설정·기본값."""
    man = manifest or {}
    managed = bool(man.get("managed", True)) if manifest is not None else False
    g = str(man.get("grade") or "").upper()
    if g not in GRADES:
        sg = dict(DEFAULT_SITE_GRADES)
        sg.update((cfg or {}).get("site_grades") or {})
        g = next((str(v).upper() for k, v in sg.items() if k and k in str(site_id).lower()), DEFAULT_GRADE)
        if g not in GRADES:
            g = DEFAULT_GRADE
    limit = GRADES[g]
    if not managed:
        limit = min(limit, UNMANAGED_MAX)       # 관제기가 관리하지 않는 사이트는 최대 100MB
    return g, limit, managed


# ---------------- 지각 해시 ----------------
def dhash(img, size=8):
    """차이 해시 64비트(정수). 비슷한 그림은 해밍 거리가 작다."""
    from PIL import Image
    g = img.convert("L").resize((size + 1, size), Image.BILINEAR)
    px = list(g.getdata())
    v = 0
    for y in range(size):
        for x in range(size):
            v = (v << 1) | (1 if px[y * (size + 1) + x] > px[y * (size + 1) + x + 1] else 0)
    return v


def hamming(a, b):
    return bin(int(a) ^ int(b)).count("1")


# ---------------- 메모리 자료 ----------------
class MemoryPack:
    """작업 중에는 이것만 읽는다. 경로는 'snippets/<site>/x.png' 처럼 슬래시."""

    def __init__(self, site_id, version, grade, limit, files=None, meta=None):
        self.site_id, self.version, self.grade, self.limit = site_id, version, grade, limit
        self.files = dict(files or {})          # path -> bytes
        self.meta = dict(meta or {})            # path -> 매니페스트 항목
        self.new = {}                           # 작업 중 새로 배운 것(올릴 후보) path -> bytes
        self.released = False
        self.index = None                       # site_pack.SiteIndex (적재 때 만듦)

    @property
    def bytes_used(self):
        return sum(len(b) for b in self.files.values())

    def list(self, pattern):
        self._alive()
        return sorted(p for p in self.files if fnmatch.fnmatchcase(p, pattern))

    def get(self, path):
        self._alive()
        return self.files.get(path)

    def open_image(self, path):
        from PIL import Image
        b = self.get(path)
        if b is None:
            raise FileNotFoundError(path)
        im = Image.open(io.BytesIO(b))
        im.load()
        return im

    def put(self, path, data, kind="snippet", **meta):
        self._alive()
        self.files[path] = data
        self.new[path] = data
        self.meta[path] = dict(meta, path=path, kind=kind, bytes=len(data),
                               sha256=hashlib.sha256(data).hexdigest(), ts=int(time.time()))

    def put_image(self, path, im, kind="snippet", **meta):
        bio = io.BytesIO()
        im.save(bio, format="PNG")
        self.put(path, bio.getvalue(), kind, **meta)

    def exists(self, path):
        return path in self.files

    def release(self):
        """메모리 해제(작업 끝). 이후 읽으면 오류."""
        n = len(self.files)
        self.files.clear()
        self.new.clear()
        self.index = None
        self.released = True
        return n

    def _alive(self):
        if self.released:
            raise RuntimeError("refpack_released")


# ---------------- 정리(용량 초과) ----------------
def plan_prune(entries, limit, dup_dist=6, protect_top_pct=20, now=None):
    """entries: [{path, bytes, kind, step, ts, representative, success_rate, phash}] .
    돌려줌 (keep_paths, removed[{path, reason, bytes, near}], protected_paths).
    순서: ① 같은 단계·종류에서 지각 해시 거리 ≤ dup_dist 인 중복·유사 → ② 오래된 것. 대표·성공률 상위는 보호."""
    es = [dict(e) for e in entries]
    total = sum(int(e.get("bytes", 0)) for e in es)
    rated = sorted((e for e in es if e.get("success_rate") is not None),
                   key=lambda e: -float(e.get("success_rate") or 0))
    k = max(1, int(round(len(rated) * protect_top_pct / 100.0))) if rated else 0
    top = {e["path"] for e in rated[:k]}
    protected = {e["path"] for e in es if e.get("representative")} | top
    removed = []
    alive = {e["path"]: e for e in es}
    if total <= limit:
        return sorted(alive), removed, sorted(protected)

    def better(a, b):     # a 가 남길 쪽인가
        ka = (bool(a.get("representative")), float(a.get("success_rate") or 0), int(a.get("ts") or 0))
        kb = (bool(b.get("representative")), float(b.get("success_rate") or 0), int(b.get("ts") or 0))
        return ka >= kb
    # ① 중복·유사
    groups = {}
    for e in es:
        if e.get("phash") is None:
            continue
        groups.setdefault((e.get("kind"), e.get("step")), []).append(e)
    for _g, items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        items.sort(key=lambda e: (-int(bool(e.get("representative"))), -float(e.get("success_rate") or 0),
                                  -int(e.get("ts") or 0)))
        kept = []
        for e in items:
            if total <= limit:
                break
            near = next((kx for kx in kept if hamming(int(kx["phash"]), int(e["phash"])) <= dup_dist), None)
            if near is not None and e["path"] not in protected and better(near, e):
                total -= int(e.get("bytes", 0))
                alive.pop(e["path"], None)
                removed.append({"path": e["path"], "reason": "duplicate", "bytes": int(e.get("bytes", 0)),
                                "near": near["path"]})
            else:
                kept.append(e)
    # ② 오래된 것
    if total > limit:
        for e in sorted(alive.values(), key=lambda e: int(e.get("ts") or 0)):
            if total <= limit:
                break
            if e["path"] in protected:
                continue
            total -= int(e.get("bytes", 0))
            removed.append({"path": e["path"], "reason": "old", "bytes": int(e.get("bytes", 0)), "near": None})
        for r in removed:
            alive.pop(r["path"], None)
    return sorted(alive), removed, sorted(protected)


def prune_report(site_id, grade, limit, entries, removed, protected, collector_id="", now=None):
    before = sum(int(e.get("bytes", 0)) for e in entries)
    after = before - sum(r["bytes"] for r in removed)
    return {"type": "refpack_prune", "site_id": site_id, "grade": grade, "limit_bytes": limit,
            "before_bytes": before, "after_bytes": after, "over_limit_after": after > limit,
            "removed": removed, "removed_count": len(removed), "protected_count": len(protected),
            "collector_id": collector_id, "ts_kst": _kst(now)}


# ---------------- 관제기에서 받기 ----------------
class RefPackClient:
    """KBSync 의 연결(호스트 고정·로그인·리다이렉트 금지)을 그대로 쓴다."""

    def __init__(self, kb, cfg, state_dir, log=lambda *a, **k: None, ui=None, now=time.time, key=None):
        from .crypto import default_key
        self.kb, self.cfg, self.log, self.now = kb, cfg, log, now
        self.key = key or default_key()        # v1.6(t850u): 캐시·올리기 암호화 키
        self.cache_root = os.path.join(state_dir, "refcache")
        self.ui = ui or (lambda msg, state="learning", **k: print(msg, flush=True))

    def _connect(self):
        from .kb_sync import check_server_url
        if not (self.kb.c.get("url") or "").strip():
            raise RefPackError("server_url_missing", "kb_server.url 이 비어 있음(관제기 접속 필수)")
        try:
            self.kb.scheme, self.kb.host, _ = check_server_url(self.kb.c)
        except (ValueError, PermissionError) as e:
            raise RefPackError("server_url_blocked", str(e))
        if not self.kb.token:
            try:
                self.kb.login()
            except Exception as e:  # noqa: BLE001
                raise RefPackError("server_unreachable", "login:" + type(e).__name__)

    def manifest(self, site_id):
        self._connect()
        try:
            m = self.kb._req("GET", "/refpack/manifest", q={"site_id": site_id})
        except Exception as e:  # noqa: BLE001
            raise RefPackError("server_unreachable", "manifest:" + type(e).__name__)
        if not isinstance(m, dict) or not m.get("version") or not isinstance(m.get("files"), list):
            raise RefPackError("bad_manifest", "version/files 없음")
        return m

    def load(self, site_id):
        """받기·SHA 확인·메모리 적재. 실패하면 RefPackError(작업 시작 안 함)."""
        m = self.manifest(site_id)
        grade, limit, managed = site_grade(site_id, m, self.cfg)
        files = m["files"]
        total = 0
        for f in files:
            p = str(f.get("path", ""))
            if not _safe(p) or len(str(f.get("sha256", ""))) != 64:
                raise RefPackError("bad_manifest", "unsafe path or sha")
            total += int(f.get("bytes", 0) or 0)
        if total > limit:
            raise RefPackError("refpack_over_grade", "%d > %d (%s)" % (total, limit, grade))
        if not any(f.get("kind") == "gallery" for f in files):
            raise RefPackError("refpack_no_gallery", "사이트 맵에 전체 화면 화보가 없음")
        self.ui(LEARNING_MSG, state="learning", site=site_id)
        self.log("refpack_learning", site=site_id, version=m["version"], grade=grade, files=len(files), bytes=total)
        vdir = os.path.join(self.cache_root, _slug(site_id), _slug(str(m["version"])))
        sdir = os.path.join(self.cache_root, _slug(site_id))
        if os.path.isdir(sdir):                          # 다른 버전 임시 캐시는 지움
            for d in os.listdir(sdir):
                if d != _slug(str(m["version"])):
                    shutil.rmtree(os.path.join(sdir, d), ignore_errors=True)
        mem, meta, fetched, reused = {}, {}, 0, 0
        for f in files:
            p, sha = f["path"], str(f["sha256"]).lower()
            cp = os.path.join(vdir, *p.split("/")) + ".bgx"
            body = None
            if os.path.exists(cp):                         # 임시 캐시(항상 암호화) — 같은 버전이면 다시 안 받음
                try:
                    stored = open(cp, "rb").read()
                    if f.get("enc"):
                        if hashlib.sha256(stored).hexdigest() == sha:
                            body = self._plain(stored, p)
                    else:
                        plain = self.key.decrypt(stored)
                        if hashlib.sha256(plain).hexdigest() == sha:
                            body = plain
                    if body is not None:
                        reused += 1
                except Exception:  # noqa: BLE001  (키가 다르거나 깨짐 → 다시 받기)
                    body = None
            if body is None:
                try:
                    b, _h = self.kb._req("GET", "/refpack/file", q={"site_id": site_id, "path": p}, raw=True)
                except Exception as e:  # noqa: BLE001
                    raise RefPackError("server_unreachable", "file:" + type(e).__name__)
                if hashlib.sha256(b).hexdigest() != sha:
                    raise RefPackError("sha_mismatch", p)
                os.makedirs(os.path.dirname(cp), exist_ok=True)
                tmp = cp + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(b if f.get("enc") else self.key.encrypt(b))   # 디스크에는 암호문만
                os.replace(tmp, cp)
                body, fetched = (self._plain(b, p) if f.get("enc") else b), fetched + 1
            mem[p] = body
            meta[p] = dict(f)
        pack = MemoryPack(site_id, m["version"], grade, limit, mem, meta)
        try:
            from .site_pack import SiteIndex
            pack.index = SiteIndex(pack)                   # v1.6(t846u): 단계·글자·그림 해시 색인
        except Exception as e:  # noqa: BLE001
            self.log("site_index_err", err=type(e).__name__)
            pack.index = None
        self.log("refpack_loaded", site=site_id, version=m["version"], grade=grade, files=len(mem),
                 bytes=pack.bytes_used, fetched=fetched, reused=reused)
        self.ui("학습 완료: %s (%d개, %.1fMB, 등급 %s)" % (site_id, len(mem), pack.bytes_used / MB, grade),
                state="loaded", site=site_id)
        return pack

    def _plain(self, b, p):
        """관제기가 암호화해 보낸 자료(enc) → 메모리용 평문."""
        from .crypto import DecryptError
        try:
            return self.key.decrypt(b)
        except DecryptError as e:
            raise RefPackError("decrypt_failed", "%s %s" % (p, e))

    def report(self, site_id, body):
        self._connect()
        try:
            return self.kb._req("POST", "/refpack/report", dict(body, site_id=site_id))
        except Exception as e:  # noqa: BLE001
            self.log("refpack_report_err", err=type(e).__name__)
            return None

    def finish(self, pack, rank=None, dup_dist=6, protect_top_pct=20, collector_id=""):
        """작업 끝: 새로 배운 것까지 합쳐 등급 한도 안으로 정리 → 보고 → 남은 새 자료만 올림 → 메모리 해제."""
        entries = []
        for p, mm in pack.meta.items():
            e = {"path": p, "bytes": int(mm.get("bytes", len(pack.files.get(p, b"")))), "kind": mm.get("kind"),
                 "step": mm.get("step"), "ts": mm.get("ts", 0), "representative": bool(mm.get("representative")),
                 "success_rate": mm.get("success_rate"), "phash": mm.get("phash")}
            if e["phash"] is None and p in pack.files and p.endswith(".png") and mm.get("kind") == "gallery":
                try:
                    e["phash"] = dhash(pack.open_image(p))
                except Exception:  # noqa: BLE001
                    pass
            if rank and e["success_rate"] is None:
                e["success_rate"] = rank.get(p)
            entries.append(e)
        keep, removed, protected = plan_prune(entries, pack.limit, dup_dist, protect_top_pct)
        rep = prune_report(pack.site_id, pack.grade, pack.limit, entries, removed, protected, collector_id)
        rep["version"] = pack.version
        rep["used_bytes"] = rep["after_bytes"]
        keep_set = set(keep)
        uploads = [(p, pack.new[p]) for p in sorted(pack.new) if p in keep_set]
        rep["upload"] = [{"path": p, "bytes": len(b), "sha256": hashlib.sha256(b).hexdigest(),
                          "kind": pack.meta.get(p, {}).get("kind")} for p, b in uploads]
        if rep["over_limit_after"]:                       # 보호 자료만으로도 넘치면 새 자료는 올리지 않음
            rep["upload"] = []
            uploads = []
        self.report(pack.site_id, rep)
        for p, b in uploads:
            try:
                eb = self.key.encrypt(b)                  # v1.6(t850u): 올리는 자료도 암호화
                self.kb._req("POST", "/refpack/upload", {"site_id": pack.site_id, "path": p,
                                                          "sha256": hashlib.sha256(b).hexdigest(),
                                                          "sha256_enc": hashlib.sha256(eb).hexdigest(), "enc": "BGX1",
                                                          "kind": pack.meta.get(p, {}).get("kind"),
                                                          "data_b64": __import__("base64").b64encode(eb).decode()})
            except Exception as e:  # noqa: BLE001
                self.log("refpack_upload_err", path=p, err=type(e).__name__)
        n = pack.release()
        self.log("refpack_released", site=pack.site_id, files=n)
        return rep


def _safe(p):
    return bool(p) and "\\" not in p and not p.startswith("/") and ":" not in p and \
        all(x not in ("", ".", "..") for x in p.split("/")) and p.split("/")[0] in ("snippets", "map", "gallery", "site_pack") and \
        p.lower().endswith((".png", ".json", ".jpg", ".jpeg", ".jsonl", ".md", ".txt"))


def _slug(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(s))[:80] or "x"


def file_ui(state_dir):
    """수집기 화면 표시: 콘솔 + state/ui_status.json (작은 창·bat 이 읽어 보여줌)."""
    p = os.path.join(state_dir, "ui_status.json")

    def ui(msg, state="learning", **k):
        print("[수집기] " + str(msg), flush=True)
        try:
            os.makedirs(state_dir, exist_ok=True)
            tmp = p + ".tmp"
            json.dump(dict({"state": state, "message": str(msg), "ts_kst": _kst()},
                           **{kk: v for kk, v in k.items() if isinstance(v, (str, int, float))}),
                      open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
            os.replace(tmp, p)
        except OSError:
            pass
    return ui


def start_job(kb, cfg, site_id, state_dir, log=lambda *a, **k: None, ui=None, key=None):
    """관제기 접속 필수: DB 받기 + 사이트 참고자료 메모리 적재. 실패면 RefPackError(호출자가 STOP)."""
    if not (kb.c.get("url") or "").strip():
        raise RefPackError("server_url_missing", "kb_server.url 이 비어 있음(관제기 접속 필수)")
    r = kb.sync()
    st = r.get("status")
    if st not in ("up_to_date", "updated"):
        raise RefPackError("server_unreachable" if st in ("offline", "skipped_no_url") else "kb_sync_" + str(st),
                           json.dumps({k: v for k, v in r.items() if k in ("status", "err", "http")}, ensure_ascii=False))
    client = RefPackClient(kb, cfg, state_dir, log, ui, key=key)
    return client, client.load(site_id)
