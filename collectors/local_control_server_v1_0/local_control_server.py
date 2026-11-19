# -*- coding: utf-8 -*-
"""로컬 관제기(임시) v1.0 — 사무실 관제기 서버가 아직 없거나 답하지 않을 때, 같은 노트북(또는 사무실 PC)에서
수집기 v1.6 이 요구하는 관제기 API(SERVER_API_KO.md)를 흉내 내 준다. 표준 라이브러리만(무료).

제공: POST /api/collector/v1/auth/login · GET /kb/manifest · GET /kb/file(없음 404) ·
      GET /refpack/manifest?site_id= · GET /refpack/file?site_id=&path= · POST /refpack/report · POST /refpack/upload ·
      POST /account/lock · POST /account/unlock
자료: --packs 폴더 아래 <site_id>/manifest.json + <site_id>/files/<path> (수집기 tools/build_site_pack.py 로 만든 암호화 묶음).
받은 보고·올린 자료는 <site_id>/reports.jsonl, <site_id>/incoming/ 에 그대로(암호문) 저장.
로그인 아이디·비밀번호: 실행 때 입력(getpass) 또는 환경변수 BG_CONTROL_USER / BG_CONTROL_PASSWORD. 파일에 저장 안 함.
기본은 127.0.0.1 에만 열림(같은 PC 전용). 사무실 내부망에 열려면 --bind 0.0.0.0 (방화벽은 사무실 안만).

실행: python local_control_server.py --packs D:\\BIDGUARD\\control_local\\packs --port 8790
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

API = "/api/collector/v1"
_SAFE = re.compile(r"^[0-9A-Za-z가-힣_.@\-/]+$")


def _safe_rel(p):
    return bool(p) and _SAFE.match(p) and not p.startswith("/") and all(x not in ("", ".", "..") for x in p.split("/"))


class State:
    def __init__(self, packs, user, pw, ttl=900):
        self.packs, self.ttl = packs, ttl
        self.user_h = hashlib.sha256(user.encode()).digest()
        self.pw_h = hashlib.sha256(pw.encode()).digest()
        self.tokens = {}
        self.locks = {}
        self.mu = threading.Lock()

    def check(self, u, p):
        return hmac.compare_digest(hashlib.sha256(str(u).encode()).digest(), self.user_h) and \
            hmac.compare_digest(hashlib.sha256(str(p).encode()).digest(), self.pw_h)

    def site_dir(self, site):
        if not site or not _safe_rel(site) or "/" in site:
            return None
        return os.path.join(self.packs, site)


def make_handler(st):
    class H(BaseHTTPRequestHandler):
        server_version = "BG-local-control/1.0"

        def log_message(self, fmt, *a):          # 비밀번호·토큰은 기록 안 함(경로만)
            sys.stderr.write("[관제기] %s %s\n" % (self.command, urlparse(self.path).path))

        def _json(self, code, obj):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _auth(self):
            t = (self.headers.get("Authorization") or "")[7:]
            return bool(t) and st.tokens.get(t, 0) > time.time()

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 64 * 1024 * 1024:
                return None
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return None

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not u.path.startswith(API):
                return self._json(404, {"error": "not_found"})
            path = u.path[len(API):]
            if not self._auth():
                return self._json(401, {"error": "no_token"})
            if path == "/kb/manifest":
                return self._json(200, {"kb_version": "local-control-1", "files": []})
            if path == "/refpack/manifest":
                d = st.site_dir(q.get("site_id", ""))
                mp = os.path.join(d, "manifest.json") if d else ""
                if not d or not os.path.exists(mp):
                    return self._json(404, {"error": "no_pack", "site_id": q.get("site_id", "")})
                return self._json(200, json.load(open(mp, encoding="utf-8")))
            if path == "/refpack/file":
                d = st.site_dir(q.get("site_id", ""))
                p = q.get("path", "")
                if not d or not _safe_rel(p):
                    return self._json(400, {"error": "bad_path"})
                fp = os.path.join(d, "files", *p.split("/"))
                if not os.path.isfile(fp):
                    return self._json(404, {"error": "not_found"})
                b = open(fp, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
                return
            return self._json(404, {"error": "not_found"})

        def do_POST(self):
            u = urlparse(self.path)
            if not u.path.startswith(API):
                return self._json(404, {"error": "not_found"})
            path = u.path[len(API):]
            body = self._body()
            if body is None:
                return self._json(400, {"error": "bad_body"})
            if path == "/auth/login":
                if st.check(body.get("username", ""), body.get("password", "")):
                    t = secrets.token_urlsafe(24)
                    st.tokens[t] = time.time() + 3600
                    return self._json(200, {"token": t, "expires_in": 3600})
                time.sleep(1.0)
                return self._json(401, {"error": "bad_login"})
            if not self._auth():
                return self._json(401, {"error": "no_token"})
            if path == "/refpack/report":
                d = st.site_dir(body.get("site_id", ""))
                if not d:
                    return self._json(400, {"error": "bad_site"})
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "reports.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(body, ensure_ascii=False) + "\n")
                return self._json(200, {"ok": True})
            if path == "/refpack/upload":
                d = st.site_dir(body.get("site_id", ""))
                p = body.get("path", "")
                if not d or not _safe_rel(p):
                    return self._json(400, {"error": "bad_path"})
                b = base64.b64decode(body.get("data_b64", ""))
                if body.get("sha256_enc") and hashlib.sha256(b).hexdigest() != body["sha256_enc"]:
                    return self._json(400, {"error": "sha_mismatch"})
                fp = os.path.join(d, "incoming", *p.split("/"))
                os.makedirs(os.path.dirname(fp), exist_ok=True)
                open(fp, "wb").write(b)                      # 암호문 그대로(사람이 검수 후 묶음에 반영)
                return self._json(200, {"ok": True})
            if path in ("/account/lock", "/account/unlock"):
                tag, cid = str(body.get("account_tag", "")), str(body.get("collector_id", ""))
                with st.mu:
                    cur = st.locks.get(tag)
                    now = time.time()
                    if path == "/account/unlock":
                        if cur and cur[0] == cid:
                            st.locks.pop(tag, None)
                        return self._json(200, {"ok": True})
                    if not cur or cur[0] == cid or cur[1] < now:
                        st.locks[tag] = (cid, now + st.ttl)
                        return self._json(200, {"ok": True, "locked": True, "ttl_sec": st.ttl})
                    return self._json(409, {"ok": False, "locked": False, "holder": cur[0]})
            return self._json(404, {"error": "not_found"})
    return H


def serve(packs, user, pw, bind="127.0.0.1", port=8790):
    st = State(packs, user, pw)
    srv = ThreadingHTTPServer((bind, port), make_handler(st))
    return srv, st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--packs", required=True)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args(argv)
    user = os.environ.get("BG_CONTROL_USER") or input("관제기 아이디(수집기 keys 의 DB 서버 아이디와 같게): ").strip()
    pw = os.environ.get("BG_CONTROL_PASSWORD") or getpass.getpass("관제기 비밀번호: ")
    if not user or not pw:
        print("아이디·비밀번호가 비었어요.")
        return 2
    sites = [d for d in sorted(os.listdir(a.packs)) if os.path.exists(os.path.join(a.packs, d, "manifest.json"))] \
        if os.path.isdir(a.packs) else []
    srv, _st = serve(a.packs, user, pw, a.bind, a.port)
    print("[관제기] http://%s:%d 에서 대기. 묶음: %s" % (a.bind, a.port, ", ".join(sites) or "(없음 — 수집기가 STOP 합니다)"))
    print("[관제기] 끝내려면 Ctrl+C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
