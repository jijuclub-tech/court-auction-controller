# -*- coding: utf-8 -*-
"""최신 DB(collector_kb) 받기 — 회차 시작 때 1번 (한영님 13:48 지시).

- 서버(관제기) 주소는 config.json 의 kb_server.url. v1.6 부터 필수(비었거나 접속 실패면 작업 시작 안 함).
- 서버 아이디·비밀번호는 대표가 정하고 실행 때 입력한다(SecretStore, 파일에 평문 저장 없음).
- 버전(kb_version)이 같으면 아무것도 안 받는다. 다르면 파일별 SHA-256 을 비교해 바뀐 것만 받는다.
- 받은 파일은 SHA-256 이 맞을 때만 바꿔 끼우고, 바꾸기 전 파일은 collector_kb/.backup/<시각>/ 에 둔다.
- 서버가 안 되면(주소 없음·연결 실패·로그인 실패·이상한 응답) status 로 알려 주고, 실행 쪽(refpack.start_job)이 STOP 한다(폴백 없음, v1.6).
- 프로그램 코드(.py 등)는 DB 로 받지 않는다. 받을 수 있는 폴더·확장자만 허용.
규격: SERVER_API_KO.md
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
import ssl

API = "/api/collector/v1"
# sites/·rules/ 는 동작(goto 주소·pacing·allow_hint_xy)을 정하므로 서버에서 자동으로 받지 않는다 (검수 H2).
# 받는 것은 자료뿐: 조각그림·지도·오류표·노하우.
ALLOWED_TOP = ("snippets", "map", "errors", "knowhow")   # sites/·rules/ 는 코드 배포로만 (검수 H2)
ALLOWED_EXT = (".png", ".json", ".jsonl", ".md", ".txt")
LOCAL_ONLY = ("snippet_rank.json", "kb_manifest.json",
              "screen_timing_local.json", "stall_stats_local.json", "ntp_stats_local.json")
MAX_FILE = 20 * 1024 * 1024
MAX_TOTAL = 300 * 1024 * 1024


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트(30x)를 따라가지 않는다 → 토큰이 다른 호스트로 새지 않음 (검수 H2 시험 A)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect_not_allowed", headers, fp)


def check_server_url(cfg_server):
    """(scheme, host, port) 또는 예외. https 만. http 는 allow_http=True + host 일치 때만."""
    u = urllib.parse.urlparse((cfg_server.get("url") or "").strip())
    host_cfg = (cfg_server.get("host") or "").strip().lower()
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("bad_url")
    if u.scheme == "http":
        if not cfg_server.get("allow_http"):
            raise PermissionError("http_not_allowed")   # 기본은 https 만
        if not host_cfg:
            raise PermissionError("http_needs_host")     # http 는 host 를 꼭 적어야 함
    from .refusal_log import is_banned
    if is_banned(u.hostname or ""):
        raise PermissionError("banned_host")             # 법원·iros 로는 서버 주소도 금지(v1.5)
    if host_cfg and u.hostname.lower() != host_cfg:
        raise PermissionError("host_mismatch")           # 설정한 호스트 하나만
    return u.scheme, u.hostname.lower(), u.port


def build_safe_opener(cfg_server):
    handlers = [_NoRedirect()]
    if cfg_server.get("url", "").strip().lower().startswith("https"):
        if cfg_server.get("verify_tls", True) is False:
            raise PermissionError("verify_tls_false_on_https")   # https 면 인증서 검사를 끄지 못함 (검수 H2)
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    return urllib.request.build_opener(*handlers)


def safe_rel(rel):
    """서버가 준 경로가 안전한지. 안전하면 os 경로, 아니면 None."""
    if not isinstance(rel, str) or not rel or "\\" in rel or rel.startswith("/") or ":" in rel:
        return None
    parts = rel.split("/")
    if any(p in ("", ".", "..") for p in parts) or parts[0] not in ALLOWED_TOP:
        return None
    if "_forbidden" in parts:                            # v1.5: 금지 그림(인증서 등)은 서버와 주고받지 않음
        return None
    if not rel.lower().endswith(ALLOWED_EXT) or parts[-1] in LOCAL_ONLY:
        return None
    return os.path.join(*parts)


class KBSync:
    def __init__(self, kb_dir, cfg_server, secrets, collector_version, machine="", log=lambda *a, **k: None,
                 opener=None, now=dt.datetime.now, collector_id=""):
        self.kb, self.c, self.s, self.ver, self.machine, self.log, self.now = (
            kb_dir, cfg_server, secrets, collector_version, machine, log, now)
        self.collector_id = collector_id
        self.scheme = None
        self.opener_err = None
        try:
            self.opener = opener or build_safe_opener(cfg_server)   # verify_tls=false on https 면 여기서 막음
        except PermissionError as e:
            self.opener, self.opener_err = None, str(e)             # 쓸 때 offline 로 처리
        self.token = None
        self.man_path = os.path.join(kb_dir, "kb_manifest.json")

    # ---- HTTP ----
    def _url(self, path, q=None):
        u = self.c["url"].rstrip("/") + API + path
        return u + ("?" + urllib.parse.urlencode(q) if q else "")

    def _req(self, method, path, body=None, q=None, raw=False):
        h = {"Accept": "application/json", "User-Agent": "BIDGUARD-standalone/" + self.ver}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        if self.opener is None:
            raise PermissionError(self.opener_err or "no_opener")
        url = self._url(path, q)
        if urllib.parse.urlparse(url).hostname != self.host:   # 매 요청 호스트 고정 (토큰 유출 방지)
            raise PermissionError("host_mismatch")
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        with self.opener.open(req, timeout=float(self.c.get("timeout_sec", 8))) as r:
            b = r.read(MAX_FILE + 1) if raw else r.read()
            if raw and len(b) > MAX_FILE:
                raise ValueError("file_too_big")
            return (b, dict(r.headers)) if raw else json.loads(b.decode("utf-8"))

    # ---- 로컬 ----
    def local_manifest(self):
        try:
            return json.load(open(self.man_path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"kb_version": None, "files": {}}

    def _save_manifest(self, m):
        tmp = self.man_path + ".tmp"
        json.dump(m, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, self.man_path)

    # ---- 본체 ----
    def login(self):
        user = self.s.get("KB_SERVER_USER", required=True)
        pw = self.s.get("KB_SERVER_PASSWORD", required=True)
        if not user or not pw:
            raise PermissionError("no_credentials")
        r = self._req("POST", "/auth/login", {"username": user.reveal(), "password": pw.reveal(),
                                              "machine": self.machine, "collector_version": self.ver,
                                              "collector_id": self.collector_id})
        self.token = r.get("token")
        if not self.token:
            raise PermissionError("no_token")

    def account_lock(self, tag, collector_id):
        """서버에 법원 계정 잠금 요청. True=내가 잠금, False=다른 수집기가 쓰는 중, None=서버 못 씀."""
        if not (self.c.get("url") or "").strip():
            return None
        try:
            self.scheme, self.host, _ = check_server_url(self.c)
            if not self.token:
                self.login()
            r = self._req("POST", "/account/lock", {"account_tag": tag, "collector_id": collector_id})
            return bool(r.get("locked"))
        except Exception:  # noqa: BLE001
            return None

    def account_unlock(self, tag, collector_id):
        try:
            self._req("POST", "/account/unlock", {"account_tag": tag, "collector_id": collector_id})
        except Exception:  # noqa: BLE001
            pass

    def sync(self):
        """돌려줌 dict(status=..., ...). status: skipped_no_url | offline | up_to_date | updated | partial."""
        if not (self.c.get("url") or "").strip():
            self.log("kb_sync", status="skipped_no_url")
            return {"status": "skipped_no_url"}
        try:
            self.scheme, self.host, _port = check_server_url(self.c)
        except (ValueError, PermissionError) as e:
            self.log("kb_sync", status="blocked", err=type(e).__name__ + ":" + str(e))
            return {"status": "blocked", "err": str(e)}
        if self.scheme == "http":
            self.log("kb_sync_warn", why="http_allowed_by_config(사무실 내부망 시험)")
        local = self.local_manifest()
        try:
            self.login()
            m = self._req("GET", "/kb/manifest", q={"have": local.get("kb_version") or ""})
        except (urllib.error.URLError, OSError, ValueError, PermissionError, KeyError) as e:
            code = getattr(e, "code", None)
            self.log("kb_sync", status="offline", err=type(e).__name__, http=code)
            return {"status": "offline", "err": type(e).__name__, "http": code}
        ver = m.get("kb_version")
        files = m.get("files") or []
        if not ver or not isinstance(files, list):
            self.log("kb_sync", status="offline", err="bad_manifest")
            return {"status": "offline", "err": "bad_manifest"}
        if ver == local.get("kb_version"):
            self.log("kb_sync", status="up_to_date", kb_version=ver)
            return {"status": "up_to_date", "kb_version": ver}
        want, total, rejected = [], 0, []
        for f in files:
            rel = f.get("path")
            osrel = safe_rel(rel)
            sha = str(f.get("sha256", "")).lower()
            size = int(f.get("size", 0) or 0)
            if not osrel or len(sha) != 64 or size > MAX_FILE:
                rejected.append(rel)
                continue
            p = os.path.join(self.kb, osrel)
            if os.path.exists(p) and sha256_file(p) == sha:
                continue
            total += size
            want.append((rel, osrel, sha))
        if total > MAX_TOTAL:
            self.log("kb_sync", status="offline", err="too_big", total=total)
            return {"status": "offline", "err": "too_big"}
        stamp = self.now().strftime("%Y%m%d_%H%M%S")
        staging = os.path.join(self.kb, ".staging", stamp)
        # 1) 모두 staging 에 받아 SHA 확인. 하나라도 실패하면 아무것도 안 바꾼다 (검수 M4, 전부 아니면 전무).
        staged, failed = [], []
        for rel, osrel, sha in want:
            try:
                body, _h = self._req("GET", "/kb/file", q={"path": rel}, raw=True)
                if hashlib.sha256(body).hexdigest() != sha:
                    raise ValueError("sha_mismatch")
                if rel.endswith((".json", ".jsonl")):
                    (json.loads(body.decode("utf-8")) if rel.endswith(".json")
                     else [json.loads(x) for x in body.decode("utf-8").splitlines() if x.strip()])
                sp = os.path.join(staging, osrel)
                os.makedirs(os.path.dirname(sp), exist_ok=True)
                with open(sp, "wb") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                staged.append((rel, osrel, sha))
            except Exception as e:  # noqa: BLE001
                failed.append({"path": rel, "err": type(e).__name__ if not str(e) else str(e)[:40]})
                break
        if failed:
            shutil.rmtree(staging, ignore_errors=True)
            self.log("kb_sync", status="partial", kb_version=ver, got=0, failed=len(failed), rejected=len(rejected))
            return {"status": "partial", "kb_version": local.get("kb_version"), "got": [],
                    "failed": failed, "rejected": rejected}
        # 2) 전부 받았으면 한꺼번에 교체(원본은 .backup 으로)
        bak = os.path.join(self.kb, ".backup", stamp)
        got = []
        for rel, osrel, sha in staged:
            dst = os.path.join(self.kb, osrel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if os.path.exists(dst):
                os.makedirs(os.path.dirname(os.path.join(bak, osrel)), exist_ok=True)
                shutil.copy2(dst, os.path.join(bak, osrel))
            os.replace(os.path.join(staging, osrel), dst)
            got.append(rel)
        shutil.rmtree(staging, ignore_errors=True)
        new = {"kb_version": ver, "synced_at": stamp, "server_version": ver,
               "files": {rel: sha for rel, _osrel, sha in staged}}   # 실제로 바꾼 것만
        # 안 바뀐(이미 최신) 파일도 매니페스트에 남겨 둬야 다음에 again 안 받음
        for f in files:
            r = f.get("path")
            if safe_rel(r) and r not in new["files"]:
                new["files"][r] = str(f.get("sha256", "")).lower()
        self._save_manifest(new)
        self.log("kb_sync", status="updated", kb_version=ver, got=len(got), rejected=len(rejected))
        return {"status": "updated", "kb_version": ver, "got": got, "failed": [], "rejected": rejected}
