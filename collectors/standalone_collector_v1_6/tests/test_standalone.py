# -*- coding: utf-8 -*-
"""단독 수집기 오프라인 시험 (사이트·법원·iros·노트북·피코2 접속 없음).
실행: python3 tests/run_all.py   또는  python3 -m pytest -q tests
"""
import base64
import io
import json
import sys
import os
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from helpers import ROOT, Clock, blank, tmp_cfg, FakeSecrets, textured

from standalone import config
from standalone.secrets_store import Secret, SecretStore, Redactor
from standalone.logger import JsonLog
from standalone.stops import StopFiles
from standalone.pacing import RateState, Pacer
from standalone.screen_guard import ScreenGuard
from standalone.snippets import SnippetBank
from standalone.locator import Locator
from standalone.storage import Storage
from standalone.power import PowerWatch
from standalone.kb_sync import KBSync, safe_rel
from standalone.ai_helper import AIHelper
from standalone import popup, captcha
from standalone.hw import FakeHW
from standalone.app import build
from standalone.runner import RunEnd
from standalone import runner
from standalone.account_lock import AccountLock, account_tag, in_window
from standalone import vk_login
from standalone.screen_state import ScreenState, merge_caps
from standalone import stall_diag, sntp
from PIL import Image, ImageDraw

ERR = os.path.join(ROOT, "collector_kb", "errors", "error_codes.json")
NOLOG = lambda *a, **k: None  # noqa: E731


class FakeDPAPI:
    def protect(self, b):
        return bytes(x ^ 0x5A for x in b)[::-1]

    def unprotect(self, b):
        return bytes(x ^ 0x5A for x in b[::-1])


# ---------------- 비밀값 ----------------
class T01Secrets(unittest.TestCase):
    def test_secret_hidden(self):
        s = Secret("sk-abcdef123456")
        self.assertNotIn("abcdef", repr(s) + str(s) + "%s" % s)
        self.assertEqual(s.reveal(), "sk-abcdef123456")

    def test_prompt_once_memory_only_without_dpapi(self):
        cfg, d = tmp_cfg()
        asked = []
        st = SecretStore(path=os.path.join(d, "s.json"), dpapi=None, prompt_fn=lambda l: asked.append(l) or "v-123456",
                         ask_fn=lambda q: True)
        self.assertEqual(st.get("DEEPSEEK_API_KEY").reveal(), "v-123456")
        st.get("DEEPSEEK_API_KEY")
        self.assertEqual(len(asked), 1)
        self.assertFalse(os.path.exists(os.path.join(d, "s.json")))   # 윈도우 아니면 저장 안 함

    def test_dpapi_file_not_plain(self):
        cfg, d = tmp_cfg()
        p = os.path.join(d, "s.json")
        st = SecretStore(path=p, dpapi=FakeDPAPI(), prompt_fn=lambda l: "PLAINSECRET99", ask_fn=lambda q: True)
        st.get("COURT_LOGIN_PW")
        raw = open(p, encoding="utf-8").read()
        self.assertNotIn("PLAINSECRET99", raw)
        self.assertNotIn(base64.b64encode(b"PLAINSECRET99").decode(), raw)
        st2 = SecretStore(path=p, dpapi=FakeDPAPI(), prompt_fn=lambda l: self.fail("다시 물으면 안 됨"))
        self.assertEqual(st2.get("COURT_LOGIN_PW").reveal(), "PLAINSECRET99")
        st2.forget_saved()
        self.assertFalse(os.path.exists(p))

    def test_dpapi_declined_not_saved(self):
        cfg, d = tmp_cfg()
        p = os.path.join(d, "s.json")
        SecretStore(path=p, dpapi=FakeDPAPI(), prompt_fn=lambda l: "x-1234", ask_fn=lambda q: False).get("GEMINI_API_KEY")
        self.assertFalse(os.path.exists(p))

    def test_redactor_and_log(self):
        cfg, d = tmp_cfg()
        st = SecretStore(path=os.path.join(d, "s.json"), dpapi=None, prompt_fn=lambda l: "TOPSECRET-777")
        st.get("KB_SERVER_PASSWORD")
        log = JsonLog(cfg["log_dir"], "t", Redactor(st), echo=False)
        log("x", msg="pw is TOPSECRET-777", api_key="zzz", nested={"password": "p"}, obj=Secret("q"))
        body = open(log.path, encoding="utf-8").read()
        for bad in ("TOPSECRET-777", "zzz", '"p"', '"q"'):
            self.assertNotIn(bad, body)

    def test_config_rejects_secrets_and_url_empty(self):
        cfg, d = tmp_cfg()
        self.assertEqual(cfg["kb_server"]["url"], "")
        p = os.path.join(d, "c.json")
        json.dump({"kb_server": {"url": "http://10.0.0.5", "password": "x"}}, open(p, "w"))
        with self.assertRaises(config.ConfigError):
            config.load(p)
        json.dump({"DEEPSEEK_API_KEY": "x"}, open(p, "w"))
        with self.assertRaises(config.ConfigError):
            config.load(p)

    def test_no_test_login_values_in_package(self):
        """시험용 로그인 값이 패키지에 없어야 한다 (검수 V1/W4).
        특정 값은 소스에 조각·해시로도 두지 않는다. 대신
          ① 일반 꼴 검사: 같은 값을 아이디/비번으로 함께 쓴 꼴(값/값, id="x" … pw="x")
          ② 금지값 해시는 저장소 밖(환경변수 BG_FORBIDDEN_LOGIN_SHA256, 쉼표 구분)에서만 받아 비교(없으면 ②는 건너뜀)."""
        import hashlib
        pair = re.compile(r"(?i)(?<![\w/])([A-Za-z][A-Za-z0-9_]{2,31})\s*/\s*\1(?![\w/])")
        same_idpw = re.compile(r"(?is)(?:id|login|user|아이디)[\"'\s:=]{1,6}[\"']([^\"'\s]{3,32})[\"'].{0,80}?"
                               r"(?:pw|pass|passwd|password|비밀번호)[\"'\s:=]{1,6}[\"']\1[\"']")
        env = [h.strip().lower() for h in os.environ.get("BG_FORBIDDEN_LOGIN_SHA256", "").split(",") if h.strip()]
        tok = re.compile(r"[A-Za-z0-9_!@#$%^&*.\-]{3,32}")
        hits = []
        for dp, dn, fn in os.walk(ROOT):
            if ".backup" in dp or "__pycache__" in dp or os.sep + "knowhow" in dp:
                continue
            for f in fn:
                if f.endswith((".py", ".md", ".json", ".txt")):
                    fp = os.path.join(dp, f)
                    txt = open(fp, encoding="utf-8", errors="ignore").read()
                    for m in pair.finditer(txt):
                        if m.group(1).lower() not in ("and", "or", "true", "false", "yes", "no", "on", "off"):
                            hits.append((fp, "pair", m.group(0)))
                    for m in same_idpw.finditer(txt):
                        hits.append((fp, "same_idpw", m.group(1)[:2] + "…"))
                    if env:
                        for t in set(tok.findall(txt)):
                            if hashlib.sha256(t.encode()).hexdigest() in env:
                                hits.append((fp, "env_hash", "***"))
        self.assertEqual(hits, [], hits)


# ---------------- 멈춤 파일 ----------------
class T02Stops(unittest.TestCase):
    def test_per_job(self):
        cfg, d = tmp_cfg()
        s = StopFiles(cfg["stop_dir"])
        self.assertIsNone(s.check("court_dongbu"))
        open(os.path.join(cfg["stop_dir"], "STOP"), "w").write("old")
        self.assertIsNotNone(s.check("court_dongbu"))          # 예전 STOP 은 법원만
        self.assertIsNone(s.check("map_yanolja"))              # 지도는 안 막음 (문제 8)
        s.write("map_yanolja", "x")
        self.assertIsNotNone(s.check("map_yanolja"))
        self.assertIsNone(s.check("map_naver_flight"))
        open(os.path.join(cfg["stop_dir"], "STOP_ALL"), "w").write("x")
        self.assertIsNotNone(s.check("map_naver_flight"))


# ---------------- 간격·429 ----------------
class T03Pacing(unittest.TestCase):
    def test_429_rest_then_day_over(self):
        cfg, d = tmp_cfg()
        c = Clock()
        rs = RateState(os.path.join(d, "r.json"), c.now)
        self.assertEqual(rs.on_429(120, 2.0, 1), "rest")
        ok, why = rs.can_start()
        self.assertFalse(ok)
        self.assertIn("HTTP_429", why)
        self.assertEqual(rs.d["gap_mult"], 2.0)
        c.sleep(121 * 60)
        self.assertTrue(rs.can_start()[0])
        self.assertEqual(rs.on_429(120, 2.0, 1), "day_over")
        c.sleep(200 * 60)
        self.assertEqual(rs.can_start(), (False, "day_over_429_twice"))
        rs2 = RateState(os.path.join(d, "r.json"), c.now)          # 다시 켜도 이어짐
        self.assertEqual(rs2.d["gap_mult"], 4.0)

    def test_gaps_hour_run(self):
        cfg, d = tmp_cfg()
        c = Clock()
        rs = RateState(os.path.join(d, "r.json"), c.now)
        p = Pacer(cfg["pacing"], rs, c.now, c.sleep, rand_fn=lambda a, b: a)
        stamps = []
        while True:
            w = p.before_item()
            if w is None:
                break
            stamps.append(c.now())
        self.assertEqual(len(stamps), 40)                          # 회차당 40건
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertGreaterEqual(min(gaps), 60)                     # 물건 사이 60초 이상
        for i, t in enumerate(stamps):
            self.assertLessEqual(sum(1 for s in stamps if t - 3600 < s <= t), 20)   # 시간당 20건
        self.assertGreaterEqual(p.breaks, 1)                       # 매시간 쉼

    def test_mult_after_429(self):
        cfg, d = tmp_cfg()
        c = Clock()
        rs = RateState(os.path.join(d, "r.json"), c.now)
        rs.on_429()
        p = Pacer(cfg["pacing"], rs, c.now, c.sleep, rand_fn=lambda a, b: a)
        (lo, hi), ph = p.limits()
        self.assertEqual((lo, hi, ph), (120, 240, 10))


# ---------------- 화면 확인 ----------------
class T04Guard(unittest.TestCase):
    def setUp(self):
        self.g = ScreenGuard(ERR)

    def test_429(self):
        d = self.g.check_text("HTTP ERROR 429")
        self.assertEqual((d.kind, d.logout, d.rest_min), ("rate_limit", True, 120))
        self.assertEqual(self.g.check_text("", "429 Too Many Requests - Chrome").kind, "rate_limit")

    def test_numbers_not_errors(self):
        self.assertTrue(self.g.check_text("2025타경429 감정평가액 429,000,000원 429호 매각기일 2026.11.27").ok)

    def test_login_tab_not_stop_but_cert_dialog_stops(self):
        self.assertTrue(self.g.check_text("인증서 로그인 아이디 로그인 공동인증서", login_phase=True).ok)
        self.assertEqual(self.g.check_text("인증서 선택 인증서 암호", login_phase=True).kind, "stop")
        self.assertEqual(self.g.check_text("수수료 결제하기", login_phase=True).kind, "stop")
        self.assertEqual(self.g.check_text("공동인증서", login_phase=False).kind, "stop")

    def test_danger_stop(self):
        for t in ("공동인증서 선택", "수수료 결제하기", "발급하기", "인증서 로그인"):
            d = self.g.check_text(t)
            self.assertEqual(d.kind, "stop", t)
            self.assertTrue(d.write_stop)

    def test_captcha_split(self):
        self.assertEqual(self.g.check_text("보안문자를 입력해 주세요").kind, "captcha_number")
        self.assertEqual(self.g.check_text("로봇이 아닙니다 reCAPTCHA").kind, "captcha_human")
        self.assertEqual(self.g.check_text("이미지를 모두 선택하세요").kind, "captcha_human")

    def test_server_twice(self):
        self.assertEqual(self.g.check_text("503 Service Unavailable").kind, "rest")
        self.assertEqual(self.g.check_text("503 Service Unavailable").kind, "stop")

    def test_block(self):
        d = self.g.check_text("비정상적인 접근이 감지되어 접속이 제한되었습니다")
        self.assertEqual((d.kind, d.rest_min), ("stop", 180))

    def test_no_save_dialog(self):
        r = [self.g.on_save_result(False) for _ in range(3)]
        self.assertEqual(r, ["skip", "suspect_rest", "rate_limit"])
        self.assertEqual(self.g.on_save_result(True), "ok")
        self.assertEqual(self.g.on_save_result(False), "skip")


# ---------------- 조각그림·위치 ----------------
def pattern_img(x, y, seed=1):
    import random
    im = blank()
    rnd = random.Random(seed)
    for i in range(0, 120, 6):
        for j in range(0, 28, 4):
            c = rnd.randint(0, 255)
            for dx in range(6):
                for dy in range(4):
                    im.putpixel((x - 60 + i + dx, y - 14 + j + dy), (c, c, c))
    return im


class T05Locate(unittest.TestCase):
    def test_snippet_then_rank(self):
        cfg, d = tmp_cfg()
        bank = SnippetBank(cfg["kb_dir"])
        im = pattern_img(700, 300)
        im.crop((640, 286, 760, 314)).save(os.path.join(bank.dir, "btn__2.png"))
        pattern_img(100, 100, seed=9).crop((40, 86, 160, 114)).save(os.path.join(bank.dir, "btn.png"))
        hit = bank.find(im, "btn", (0, 0, 1920, 1080))
        self.assertIsNotNone(hit)
        self.assertLessEqual(abs(hit[0] - 700) + abs(hit[1] - 300), 2)
        self.assertEqual(os.path.basename(bank.files("btn")[0]), "btn__2.png")   # 성공한 것이 앞으로

    def test_ocr_learn_forbid_ai_order(self):
        cfg, d = tmp_cfg()
        bank = SnippetBank(cfg["kb_dir"])
        words = [("공동인증서 로그인", 500, 300), ("아이디 로그인", 900, 300)]
        loc = Locator(bank, lambda im, b: [w for w in words if b[0] <= w[1] <= b[2] and b[1] <= w[2] <= b[3]])
        im = pattern_img(900, 300)
        r = loc.find(im, "idlogin", ["로그인"], (0, 0, 1, 1))
        self.assertEqual(r[:2], (900, 300))                      # 인증서 줄은 건너뜀
        self.assertTrue(os.path.exists(os.path.join(bank.dir, "idlogin.png")))   # 배움
        r2 = loc.find(im, "idlogin", ["로그인"], (0, 0, 1, 1))
        self.assertTrue(r2[2].startswith("snip:"))                # 다음엔 조각그림으로

    def test_ai_max_two_rounds(self):
        calls = []

        def post(url, body, headers, **k):
            calls.append(url)
            raise OSError("down")
        s = FakeSecrets({"DEEPSEEK_API_KEY": "dk-1", "GEMINI_API_KEY": "gk-1"})
        ai = AIHelper(s, dict(config.DEFAULTS["ai"]), NOLOG, post)
        self.assertIsNone(ai.pick("x", [("a", 1, 1)], blank((100, 100))))
        self.assertEqual(len(calls), 2)
        self.assertIn("deepseek", calls[0])
        self.assertIn("gemini", calls[1])

    def test_ai_pick_and_key_not_logged(self):
        logs = []
        s = FakeSecrets({"DEEPSEEK_API_KEY": "dk-SECRET"})
        seen = {}

        def post(url, body, headers, **k):
            seen.update(headers)
            return {"choices": [{"message": {"content": "1"}}]}
        ai = AIHelper(s, dict(config.DEFAULTS["ai"]), lambda *a, **k: logs.append((a, k)), post)
        r = ai.pick("검색", [("a", 1, 1), ("b", 2, 2)])
        self.assertEqual(r[:3], ("b", 2, 2))
        self.assertEqual(seen["Authorization"], "Bearer dk-SECRET")
        self.assertNotIn("dk-SECRET", json.dumps(logs, default=str))


# ---------------- 팝업·캡차 ----------------
class T06PopupCaptcha(unittest.TestCase):
    def test_two_popups(self):
        state = {"n": 2}

        def lines(im):
            if state["n"] == 2:
                return [("오늘 하루 열지 않음", 300, 700), ("닫기", 520, 702), ("오늘 하루 열지 않음", 1100, 650), ("닫기", 1320, 652)]
            if state["n"] == 1:
                return [("오늘 하루 열지 않음", 1100, 650), ("닫기", 1320, 652)]
            return [("법원경매정보", 400, 170)]
        clicks = []

        def click(x, y, note):
            clicks.append((x, y, note))
            if "close" in note:
                state["n"] -= 1
        self.assertTrue(popup.close_all(lambda: None, lines, click, lambda k, n: None))
        self.assertEqual([c[:2] for c in clicks], [(1100, 650), (1320, 652), (1100, 650), (1320, 652)])

    def test_popup_bad_words(self):
        self.assertEqual(popup.plan([("인증서 설치 오늘 하루 보지 않기", 100, 100)]), [])

    def test_captcha_prepare_human(self):
        cfg, d = tmp_cfg()
        rec = captcha.prepare_human(os.path.join(d, "ask"), blank((200, 60)), guess="376689")
        self.assertIn("376689가 맞나요?", rec["question"])
        self.assertTrue(os.path.exists(rec["image"]))

    def test_number_captcha_memory_ai_human(self):
        cfg, d = tmp_cfg()
        typed = []
        ask = os.path.join(d, "ask")
        mem = captcha.CaptchaMemory(os.path.join(d, "mem.json"))
        # AI 가 틀리게 읽음 → 멈추고 사람 답(376689) → 통과, 기억에 저장
        def sleep(s):
            json.dump({"digits": "376689"}, open(os.path.join(ask, "answer.json"), "w"))
        r = captcha.solve_number(textured((200, 60), 7), typed.append, lambda: typed[-1] == "376689",
                                 ai_read=lambda im: "376680", memory=mem, ask_dir=ask, sleep=sleep)
        self.assertEqual(r, "human_ok")
        self.assertEqual(typed, ["376680", "376689"])
        self.assertIn("376680가 맞나요?", json.load(open(os.path.join(ask, "pending.json"), encoding="utf-8"))["question"])
        # 같은 그림이면 이제 기억이 먼저 (AI 안 부름)
        typed2 = []
        r2 = captcha.solve_number(textured((200, 60), 7), typed2.append, lambda: True,
                                  ai_read=lambda im: self.fail("기억이 먼저여야 함"), memory=mem, ask_dir=ask)
        self.assertEqual((r2, typed2), ("mem_ok", ["376689"]))


# ---------------- 저장·전원 ----------------
class T07StoragePower(unittest.TestCase):
    def test_local_then_flush(self):
        cfg, d = tmp_cfg()
        st = Storage(cfg["storage"], cfg["state_dir"], platform="linux")
        p, where = st.save_bytes("dongbu/2025타경1/a.pdf", b"%PDF-1")
        self.assertEqual(where, "local")
        self.assertTrue(p.startswith(cfg["storage"]["local_root"]))
        self.assertEqual(st.flush_to_nas(), 0)                   # NAS 없음 → 그대로
        st.c["nas_root"] = os.path.join(d, "NAS")
        self.assertEqual(st.flush_to_nas(), 1)
        self.assertTrue(os.path.exists(os.path.join(d, "NAS", "dongbu", "2025타경1", "a.pdf")))
        self.assertFalse(os.path.exists(p))
        p2, w2 = st.save_bytes("x/b.pdf", b"1")
        self.assertEqual(w2, "nas")                              # NAS 먼저

    def test_power(self):
        msgs = []
        vals = iter([(80, True), (19, True), (19, False), (9, False)])
        pw = PowerWatch(config.DEFAULTS["power"], reader=lambda: next(vals), alert=msgs.append)
        self.assertEqual(pw.check(), [])
        self.assertEqual(pw.check(), ["battery_low"])
        self.assertEqual(pw.check(), ["battery_low", "unplugged"])
        self.assertIn("stop", pw.check())
        self.assertEqual(len(msgs), 3)


# ---------------- 최신 DB 받기(가짜 서버) ----------------
class FakeKB:
    def __init__(self):
        self.user, self.pw = "u-test", "p-test"     # 시험 전용 임의 값
        self.files = {"snippets/a.png": b"PNG-A2", "errors/error_codes.json": b'{"v":2}',
                      "knowhow/tip.json": b'{"k":1}'}
        self.version = "kb-2"
        self.extra = []
        self.bad_sha = set()
        self.hits = []

    def manifest(self):
        import hashlib
        fs = [{"path": p, "sha256": hashlib.sha256(b).hexdigest() if p not in self.bad_sha else "0" * 64,
               "size": len(b)} for p, b in self.files.items()]
        return {"kb_version": self.version, "files": fs + self.extra}


def serve(kb):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            b = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_POST(self):
            kb.hits.append(("POST", self.path))
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/collector/v1/auth/login" and body.get("username") == kb.user and body.get("password") == kb.pw:
                return self._json(200, {"token": "tok-1", "expires_in": 3600})
            return self._json(401, {"error": "bad_login"})

        def do_GET(self):
            kb.hits.append(("GET", self.path))
            if self.headers.get("Authorization") != "Bearer tok-1":
                return self._json(401, {"error": "no_token"})
            from urllib.parse import urlparse, parse_qs
            u = urlparse(self.path)
            if u.path == "/api/collector/v1/kb/manifest":
                return self._json(200, kb.manifest())
            if u.path == "/api/collector/v1/kb/file":
                p = parse_qs(u.query).get("path", [""])[0]
                if p in kb.files:
                    b = kb.files[p]
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.end_headers()
                    self.wfile.write(b)
                    return
            return self._json(404, {"error": "not_found"})
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class T08KBSync(unittest.TestCase):
    def mk(self, url, user="u-test", pw="p-test"):
        cfg, d = tmp_cfg()
        os.makedirs(os.path.join(cfg["kb_dir"], "snippets"), exist_ok=True)
        open(os.path.join(cfg["kb_dir"], "snippets", "a.png"), "wb").write(b"PNG-A1")
        open(os.path.join(cfg["kb_dir"], "snippets", "mine.png"), "wb").write(b"LOCAL")
        s = FakeSecrets({"KB_SERVER_USER": user, "KB_SERVER_PASSWORD": pw})
        host = ""
        if url.startswith("http://"):
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
        cs = {"url": url, "timeout_sec": 3, "allow_http": bool(host), "host": host, "verify_tls": True}
        return KBSync(cfg["kb_dir"], cs, s, "1.1.0-review", "jijuclub5"), cfg

    def test_no_url(self):
        k, cfg = self.mk("")
        self.assertEqual(k.sync()["status"], "skipped_no_url")

    def test_offline(self):
        k, cfg = self.mk("http://127.0.0.1:9")
        self.assertEqual(k.sync()["status"], "offline")
        self.assertEqual(open(os.path.join(cfg["kb_dir"], "snippets", "a.png"), "rb").read(), b"PNG-A1")

    def test_update_only_changed_then_up_to_date(self):
        kb = FakeKB()
        srv = serve(kb)
        try:
            k, cfg = self.mk("http://127.0.0.1:%d" % srv.server_port)
            open(os.path.join(cfg["kb_dir"], "sites_x"), "w").close()
            r = k.sync()
            self.assertEqual(r["status"], "updated")
            self.assertEqual(sorted(r["got"]), ["errors/error_codes.json", "knowhow/tip.json", "snippets/a.png"])   # 바뀐 것만
            self.assertEqual(open(os.path.join(cfg["kb_dir"], "snippets", "a.png"), "rb").read(), b"PNG-A2")
            self.assertTrue(os.path.exists(os.path.join(cfg["kb_dir"], "snippets", "mine.png")))   # 로컬 것 안 지움
            baks = os.listdir(os.path.join(cfg["kb_dir"], ".backup"))
            self.assertEqual(len(baks), 1)
            n = len(kb.hits)
            self.assertEqual(k.sync()["status"], "up_to_date")
            self.assertFalse([h for h in kb.hits[n:] if "/kb/file" in h[1]])          # 다시 안 받음
        finally:
            srv.shutdown()

    def test_bad_sha_and_unsafe_paths(self):
        kb = FakeKB()
        kb.bad_sha.add("snippets/a.png")
        kb.extra = [{"path": "../evil.png", "sha256": "a" * 64, "size": 1},
                    {"path": "sites/run.py", "sha256": "a" * 64, "size": 1},
                    {"path": "snippet_rank.json", "sha256": "a" * 64, "size": 1}]
        srv = serve(kb)
        try:
            k, cfg = self.mk("http://127.0.0.1:%d" % srv.server_port)
            r = k.sync()
            self.assertEqual(r["status"], "partial")
            self.assertEqual(len(r["rejected"]), 3)
            self.assertEqual(open(os.path.join(cfg["kb_dir"], "snippets", "a.png"), "rb").read(), b"PNG-A1")
            self.assertIsNone(k.local_manifest()["kb_version"])        # 다음 회차에 다시 시도
        finally:
            srv.shutdown()

    def test_wrong_login_offline(self):
        kb = FakeKB()
        srv = serve(kb)
        try:
            k, cfg = self.mk("http://127.0.0.1:%d" % srv.server_port, pw="wrong")
            r = k.sync()
            self.assertEqual(r["status"], "offline")
        finally:
            srv.shutdown()

    def test_safe_rel(self):
        self.assertIsNotNone(safe_rel("snippets/www.yanolja.com/a.png"))
        for bad in ("/etc/x.png", "snippets/../x.png", "C:/x.png", "snippets\\x.png", "code/x.png", "snippets/x.exe",
                    "sites/court.json", "rules/vk.json",      # sites/·rules/ 는 서버에서 안 받음 (검수 H2)
                    "knowhow/stall_stats_local.json", "knowhow/ntp_stats_local.json",
                    "knowhow/screen_timing_local.json", "snippets/snippet_rank.json"):   # 내 노트북 전용(검수 R10)
            self.assertIsNone(safe_rel(bad), bad)


# ---------------- 실행기 (가짜 화면) ----------------
NORMAL = [("법원경매정보", 400, 170), ("로그아웃", 1200, 165)]


def screen(words, title="법원경매정보 - Chrome"):
    return (blank(), list(words), title)


def court_spec(**kw):
    s = json.load(open(os.path.join(ROOT, "collector_kb", "sites", "court_dongbu.json"), encoding="utf-8"))
    s.update(kw)
    return s


class T09Runner(unittest.TestCase):
    def mk(self, spec, screens, secrets=None):
        cfg, d = tmp_cfg()
        c = Clock()
        hw = FakeHW(screens)
        logs = []
        log = lambda ev, **k: logs.append(dict(k, ev=ev)) or k  # noqa: E731
        r = build(spec.get("job", "court_dongbu"), spec, cfg, hw, secrets or FakeSecrets(), log, now=c.now, sleep=c.sleep)
        r.power.read = lambda: (90, True)
        r.console = None                      # 시험에선 콘솔 입력 안 씀(answer.json 로)
        return r, hw, logs, cfg, c

    def list_words(self, n=3):
        return NORMAL + [("2025타경%d" % (100 + i), 700, 450 + 40 * i) for i in range(n)] + [("매각예정물건", 700, 320)]

    def test_login_net_recover_waits_no_stop_then_relogin(self):
        # V2: 로그인 회차에서 망이 끊기면 STOP 없이 복구를 기다리고, 복구되면 로그아웃 확인 후 net_recovered_relogin.
        r, hw, logs, cfg, c = self.mk(court_spec(steps=[]), [screen(NORMAL)])
        r.cfg["stall"]["net_down_wait_min"] = 10
        r.logged_in = True
        beats = {"n": 0}
        r.account_lock = type("L", (), {"heartbeat": lambda self: beats.__setitem__("n", beats["n"] + 1)})()
        lo = {"n": 0}
        r.safe_logout = lambda *a, **k: lo.__setitem__("n", lo["n"] + 1)
        calls = {"n": 0}

        def diag(where, net_skip=False):
            calls["n"] += 1
            return ("LOCAL_NET_DOWN" if calls["n"] < 2 else "OK"), {}
        r._diagnose = diag
        with self.assertRaises(RunEnd) as ctx:
            r._wait_net_recover("w", None, None, None, "LOCAL_NET_DOWN")
        self.assertEqual(ctx.exception.why, "net_recovered_relogin")
        self.assertGreaterEqual(beats["n"], 1)                     # 대기 중 heartbeat
        self.assertEqual(lo["n"], 1)                               # 복구 후 로그아웃(=로그인 상태 확인)
        self.assertFalse(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))   # 대기 중 STOP 없음

    def test_session_expired_in_captcha_aborts_and_no_memory_save(self):
        # V3: 캡차 입력 중 세션 만료 → 입력 중단(session_lost_in_captcha), 확인 안 된 답 기억 저장 금지.
        r, hw, logs, cfg, c = self.mk(court_spec(login=None, steps=[]),
                                      [screen([("세션이 만료되었습니다. 다시 로그인", 900, 400)])])
        r.solving_captcha = True
        with self.assertRaises(RunEnd) as ctx:
            r.input_check("click", "captcha input")
        self.assertEqual(ctx.exception.why, "session_lost_in_captcha")
        self.assertFalse(r.solving_captcha)
        self.assertTrue(any(l["ev"] == "session_lost_in_captcha" for l in logs))
        self.assertFalse(os.path.exists(os.path.join(cfg["kb_dir"], "captcha_memory.json")))   # 기억 저장 안 됨

    def test_items_loop_heartbeat_each_item(self):
        # H5: 40건 루프 안에서 건마다 heartbeat 를 부른다(잠금이 '오래됨'으로 안 되게).
        spec = court_spec(steps=[{"name": "items", "type": "items_loop", "rows": {"box_r": [0, 0, 1, 1]},
                                  "per_item": [], "back": None}])
        r, hw, logs, cfg, c = self.mk(spec, [screen(self.list_words(3))])
        r.logged_in = True
        hits = {"n": 0}
        r._heartbeat = lambda: hits.__setitem__("n", hits["n"] + 1)
        r.do_items_loop(spec["steps"][0])
        self.assertGreaterEqual(hits["n"], 3)                      # 건(3)마다 한 번 이상

    def test_429_after_item_logout_and_rest(self):
        spec = court_spec(steps=[{"name": "items", "type": "items_loop", "rows": {"box_r": [0, 0, 1, 1]},
                                  "per_item": [], "back": None}])
        s_list = screen(self.list_words())
        s_429 = screen([("HTTP ERROR 429", 900, 500), ("로그아웃", 1200, 165)], "www.courtauction.go.kr")
        s_yes = screen([("로그아웃 하시겠습니까", 960, 450), ("예", 900, 520), ("아니요", 1020, 520)])
        s_out = screen([("로그인", 1150, 165)])
        r, hw, logs, cfg, c = self.mk(spec, [s_list, s_429, s_yes, s_out])

        def on(h, kind, note):
            if note.startswith("item"):
                h.i = 1
            elif note == "logout":
                h.i = 2
            elif note == "logout yes":
                h.i = 3
        hw.on_input = on
        r.logged_in = True
        res = r.run()
        self.assertEqual(res["end"], "rate_limited")
        notes = [a[3] for a in hw.actions if a[0] == "click"]
        self.assertEqual(sum(1 for n in notes if n.startswith("item")), 1)        # 429 뒤 더 안 누름
        self.assertIn("logout", notes)
        self.assertIn("logout yes", notes)
        self.assertFalse(r.rs.can_start()[0])
        self.assertEqual(r.rs.d["gap_mult"], 2.0)
        res2 = r.run()
        self.assertEqual(res2["end"], "rest_wait")                                 # 120분 안엔 시작 안 함

    def test_danger_no_click_and_stop_file(self):
        spec = court_spec(steps=[{"name": "c", "type": "click", "el": "x", "words": ["다음"], "box_r": [0, 0, 1, 1]}])
        r, hw, logs, cfg, c = self.mk(spec, [screen([("공동인증서 선택", 900, 400), ("다음", 900, 600)])])
        res = r.run()
        self.assertEqual(res["end"], "blocked")
        self.assertEqual([a for a in hw.actions if a[0] == "click"], [])
        self.assertTrue(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))

    def test_cert_button_never_clicked(self):
        spec = court_spec(steps=[{"name": "c", "type": "click", "el": "y", "words": ["로그인"], "box_r": [0, 0, 1, 1],
                                  "ai": False}])
        r, hw, logs, cfg, c = self.mk(spec, [screen([("인증서로그인", 900, 400)])])
        res = r.run()
        self.assertNotEqual(res["end"], "done")
        self.assertEqual([a for a in hw.actions if a[0] == "click"], [])

    def test_map_not_blocked_by_legacy_stop(self):
        spec = {"site": "www.yanolja.com", "job": "map_yanolja", "family": "map", "guard_site": "reservation",
                "ai": False, "steps": [{"name": "s", "type": "shot", "shot": "01"}]}
        r, hw, logs, cfg, c = self.mk(spec, [screen([("NOL", 100, 100)])])
        open(os.path.join(cfg["stop_dir"], "STOP"), "w").write("court stop")
        self.assertEqual(r.run()["end"], "done")
        open(os.path.join(cfg["stop_dir"], "STOP_map_yanolja"), "w").write("x")
        self.assertEqual(r.run()["end"], "stop_file")

    def test_no_save_dialog_three_times_rate_limit(self):
        spec = court_spec(steps=[{"name": "items", "type": "items_loop", "rows": {"box_r": [0, 0, 1, 1]},
                                  "per_item": [{"type": "download", "el": "dl", "words": ["다운로드"], "box_r": [0, 0, 1, 1],
                                                "ai": False, "save_dialog_wait": 1}]}])
        words = self.list_words(4) + [("다운로드", 1200, 300)]
        r, hw, logs, cfg, c = self.mk(spec, [screen(words)])
        r.logged_in = True
        res = r.run()
        self.assertEqual(res["end"], "rate_limited")
        evs = [l["ev"] for l in logs]
        self.assertEqual(evs.count("no_save_dialog"), 3)
        self.assertIn("rate_suspect", evs)
        self.assertEqual(res["stats"]["items"], 3)

    def test_login_only_idpw_and_secret_hidden(self):
        sec = FakeSecrets({"COURT_LOGIN_ID": "myid99", "COURT_LOGIN_PW": "Pw!2345"})
        s_home = screen([("법원경매정보", 400, 170), ("로그인", 1150, 165)])
        s_login = screen([("통합로그인", 960, 255), ("인증서 로그인", 780, 330), ("아이디 로그인", 1130, 330),
                          ("아이디", 900, 420), ("비밀번호", 900, 480), ("로그인", 960, 560)])
        s_ok = screen(NORMAL)
        spec = court_spec(steps=[{"name": "login", "type": "login"}])
        spec["login"] = dict(spec.get("login") or {}, password_mode="keyboard")   # 이 화면은 키보드 로그인
        r, hw, logs, cfg, c = self.mk(spec, [s_home, s_login, s_ok], sec)

        def on(h, kind, note):
            if note == "top_login":
                h.i = 1
            if kind == "key" and note == "ENTER":
                h.i = 2
        hw.on_input = on
        res = r.run()
        self.assertEqual(res["end"], "done", logs[-3:])
        clicks = [a for a in hw.actions if a[0] == "click"]
        self.assertFalse([a for a in clicks if 700 <= a[1] <= 860 and 310 <= a[2] <= 350])   # 인증서 탭 안 누름
        types = [a for a in hw.actions if a[0] == "type"]
        self.assertEqual([t[1] for t in types], ["***", "***"])
        self.assertNotIn("myid99", json.dumps(logs, default=str))
        self.assertIn("logout", [a[3] for a in clicks])                         # 끝나면 로그아웃
        self.assertFalse([f for f in os.listdir(r.shots) if "login" in f])      # 로그인 화면 사진 없음

    def test_robot_check_stops_without_any_input(self):
        # 체크박스/그림고르기 등 '다른 봇 확인'은 지금처럼 멈춤(입력 0번).
        s = screen([("로봇이 아닙니다 reCAPTCHA", 960, 400), ("확인", 960, 600)])
        spec = court_spec(login=None, steps=[{"name": "chk", "type": "click", "el": "z", "words": ["확인"],
                                               "box_r": [0, 0, 1, 1], "ai": False}])
        r, hw, logs, cfg, c = self.mk(spec, [s])
        res = r.run()
        self.assertEqual(res["end"], "captcha_human_required")
        self.assertEqual(hw.actions, [])                          # 클릭·키 0번
        self.assertTrue(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))

    def test_number_captcha_solved_by_human_answer(self):
        # t761u: 숫자 그림 캡차는 AI 1번 → 틀리면(여기선 AI 없음) 멈추고 질문 저장 → 사람 답 입력.
        s_cap = screen([("보안문자를 입력해 주세요", 960, 400), ("입력", 960, 520), ("확인", 960, 600)])
        s_ok = screen(NORMAL + [("확인", 960, 600)])
        spec = court_spec(login=None, steps=[{"name": "chk", "type": "click", "el": "z", "words": ["확인"],
                                               "box_r": [0, 0, 1, 1], "ai": False}])
        r, hw, logs, cfg, c = self.mk(spec, [s_cap, s_ok, s_ok])
        os.makedirs(os.path.join(cfg["state_dir"], "captcha_ask"), exist_ok=True)
        json.dump({"digits": "376689"}, open(os.path.join(cfg["state_dir"], "captcha_ask", "answer.json"), "w"))

        def on(h, kind, note):
            if note == "captcha ok" or (kind == "key" and note == "captcha enter"):
                h.i = 1
        hw.on_input = on
        res = r.run()
        evs = [l["ev"] for l in logs]
        self.assertIn("captcha_solved", evs)                      # 사람 답으로 통과
        typed = [a[1] for a in hw.actions if a[0] == "type"]
        self.assertIn("376689", typed)                            # 그 숫자를 한 번 입력
        self.assertFalse(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))

    def test_net_recover_relogin_uses_settings_and_heartbeat(self):
        # R6: 망 복구 대기는 설정값(net_down_wait_min)을 쓰고, 대기 중 heartbeat 를 보내고,
        #     복구되면 net_recovered_relogin 으로 끝낸다(상한 하나, 재귀 없음).
        spec = court_spec(login=None, steps=[])
        r, hw, logs, cfg, c = self.mk(spec, [screen(NORMAL)])
        cfg["stall"]["net_down_wait_min"] = 10
        beats = {"n": 0}
        r.account_lock = type("L", (), {"heartbeat": lambda self: beats.__setitem__("n", beats["n"] + 1)})()
        calls = {"n": 0}

        def diag(where, net_skip=False):
            calls["n"] += 1
            return ("ISP_DOWN" if calls["n"] < 2 else "OK"), {}
        r._diagnose = diag
        with self.assertRaises(RunEnd) as ctx:
            r._wait_net_recover("w", None, None, None, "ISP_DOWN")
        self.assertEqual(ctx.exception.why, "net_recovered_relogin")
        self.assertGreaterEqual(beats["n"], 1)                 # 대기 중 heartbeat 보냄

    def test_required_false_expect_continues(self):
        # R4: required:false 단계에서 기대 글자가 안 보여도 멈추지 않고 trouble 만 기록하고 계속 간다.
        spec = court_spec(login=None, steps=[{"name": "c", "type": "click", "el": "x", "words": ["다음"],
                                              "box_r": [0, 0, 1, 1], "ai": False,
                                              "expect_words": ["절대안나오는글자"], "required": False}])
        r, hw, logs, cfg, c = self.mk(spec, [screen([("다음", 900, 600)])])
        res = r.run()
        self.assertEqual(res["end"], "done")                          # 회차는 끝까지 감
        self.assertTrue(any(l["ev"] == "trouble" and l.get("what") == "expect_not_seen_optional" for l in logs))
        self.assertFalse(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))

    def test_429_during_number_captcha_logs_out_and_rests(self):
        # R2: 숫자 그림 캡차 입력 도중 429 가 보이면 반드시 로그아웃 + 휴식(120분) + rate_limited 로 끝낸다.
        s_429 = screen([("HTTP ERROR 429", 900, 500), ("로그아웃", 1200, 165)], "www.courtauction.go.kr")
        s_yes = screen([("로그아웃 하시겠습니까", 960, 450), ("예", 900, 520), ("아니요", 1020, 520)])
        s_out = screen([("로그인", 1150, 165)])
        r, hw, logs, cfg, c = self.mk(court_spec(steps=[]), [s_429, s_yes, s_out])

        def on(h, kind, note):
            if note == "logout":
                h.i = 1
            elif note == "logout yes":
                h.i = 2
        hw.on_input = on
        r.logged_in = True
        r.solving_captcha = True          # 숫자 캡차 입력 중 상태
        with self.assertRaises(RunEnd) as ctx:
            r.input_check("click", "captcha input")   # 예전엔 handle 인자 누락으로 TypeError 가 났음(R2)
        self.assertEqual(ctx.exception.why, "rate_limited")
        self.assertFalse(r.logged_in)                             # 로그아웃됨
        notes = [a[3] for a in hw.actions if a[0] == "click"]
        self.assertIn("logout", notes)
        self.assertIn("logout yes", notes)
        self.assertFalse(r.rs.can_start()[0])                     # 휴식 중이라 못 시작
        self.assertGreaterEqual(r.rs.d["rest_until"], c.now())    # 최소 120분 휴식 기록
        self.assertEqual(r.rs.d["rest_why"], "HTTP_429")

    def test_captcha_not_found_goes_human_no_ai(self):
        # R3: 캡차 그림 위치를 못 찾으면 AI 로 안 보내고 사람에게. (조각그림·앵커 모두 못 찾음)
        s_cap = screen([("보안문자", 960, 400), ("입력", 960, 520), ("확인", 960, 600)])
        s_ok = screen(NORMAL + [("확인", 960, 600)])
        spec = court_spec(login=None, steps=[{"name": "chk", "type": "click", "el": "z", "words": ["확인"],
                                              "box_r": [0, 0, 1, 1], "ai": False}])
        # image_anchor_words 를 화면에 없는 말로 바꿔 '못 찾음' 을 만든다
        spec["captcha"] = dict(spec.get("captcha") or {}, image_anchor_words=["존재하지않는앵커단어"])
        r, hw, logs, cfg, c = self.mk(spec, [s_cap, s_ok, s_ok])
        used = {"ai": False}
        r.ai = type("AI", (), {"read_digits": lambda self, *a, **k: used.__setitem__("ai", True) or "000000",
                               "red": None})()
        os.makedirs(os.path.join(cfg["state_dir"], "captcha_ask"), exist_ok=True)
        json.dump({"digits": "123456"}, open(os.path.join(cfg["state_dir"], "captcha_ask", "answer.json"), "w"))

        def on(h, kind, note):
            if note == "captcha ok" or (kind == "key" and note == "captcha enter"):
                h.i = 1
        hw.on_input = on
        r.run()
        self.assertFalse(used["ai"])                              # AI 를 전혀 부르지 않음
        self.assertTrue(any(l["ev"] == "captcha_ai_skipped" for l in logs))

    def test_input_checked_every_time(self):
        """첫 화면은 정상이어도 클릭 직전에 429 화면으로 바뀌면 그 클릭은 안 나간다."""
        spec = court_spec(login=None, steps=[{"name": "c", "type": "click", "el": "x", "words": ["다음"],
                                               "box_r": [0, 0, 1, 1], "ai": False}])
        ok = screen([("다음", 900, 600)])
        bad = screen([("HTTP ERROR 429", 900, 500)])
        r, hw, logs, cfg, c = self.mk(spec, [ok, bad])
        real = r.page_check
        n = {"k": 0}

        def flip(where, ignore=(), _depth=0):
            n["k"] += 1
            if where == "before_click":
                hw.i = 1
            return real(where, ignore, _depth)
        r.page_check = flip
        res = r.run()
        self.assertEqual(res["end"], "rate_limited")
        self.assertFalse([a for a in hw.actions if a[0] == "click" and a[3] == "x"])


class T11AccountLock(unittest.TestCase):
    def _lock(self, d, kb=None, windows=None, cid="nonmetro-01"):
        return AccountLock(d, cid, "tag1", windows=windows or [], kb=kb, now=lambda: 1000.0)

    def test_no_server_falls_to_local(self):
        # N1: 서버가 없으면(None) account_busy 로 멈추지 않고 로컬 잠금으로 진행.
        cfg, d = tmp_cfg()
        class KBNone:
            def account_lock(self, *a): return None
            def account_unlock(self, *a): pass
        ok, why = self._lock(d, kb=KBNone()).acquire()
        self.assertTrue(ok, why)
        self.assertIn("local", why)

    def test_server_denies_stops(self):
        cfg, d = tmp_cfg()
        class KBDeny:
            def account_lock(self, *a): return False
            def account_unlock(self, *a): pass
        ok, why = self._lock(d, kb=KBDeny()).acquire()
        self.assertFalse(ok)
        self.assertEqual(why, "server_lock_held_elsewhere")

    def test_server_allows(self):
        cfg, d = tmp_cfg()
        class KBOk:
            def account_lock(self, *a): return True
            def account_unlock(self, *a): pass
        ok, why = self._lock(d, kb=KBOk()).acquire()
        self.assertTrue(ok)
        self.assertEqual(why, "server_lock")

    def test_local_other_collector_blocks(self):
        cfg, d = tmp_cfg()
        a = self._lock(d, cid="metro-01"); self.assertTrue(a.acquire()[0])
        b = self._lock(d, cid="nonmetro-01")       # 다른 수집기, 같은 tag
        ok, why = b.acquire()
        self.assertFalse(ok)
        self.assertIn("local_lock_held", why)

    def test_window_blocks_outside(self):
        # 서버 없음 + 시간대 밖이면 거부 (now=1000s=09:16 UTC+9 근처는 환경무관하게 창 밖으로)
        cfg, d = tmp_cfg()
        al = AccountLock(d, "nonmetro-01", "tag9", windows=[["03:00", "03:01"]], kb=None, now=lambda: 1000.0)
        ok, why = al.acquire()
        self.assertFalse(ok)
        self.assertEqual(why, "outside_account_window")

    def test_account_tag_is_hash(self):
        t = account_tag(Secret("myid99"))
        self.assertEqual(len(t), 12)
        self.assertNotIn("myid99", t)


class T12VKRepeat(unittest.TestCase):
    def test_same_char_pause_and_jitter_no_leak(self):
        pauses = {"n": 0}
        notes, clicks = [], []
        keys = {str(i): (100 + i, 200) for i in range(10)}
        keys["A"] = (300, 200); keys["shift"] = (50, 250)
        def find_key(label): return keys.get(str(label))
        def click_key(x, y, note): clicks.append((x, y)); notes.append(note)
        stars = {"n": 0}
        def count_stars(): return stars["n"]
        def clear(): stars["n"] = 0; return True
        # 입력하면 별표가 하나씩 늘게 (조작 흉내)
        orig = click_key
        def ck(x, y, note):
            orig(x, y, note)
            if note == "vk key": stars["n"] += 1
        res = vk_login.type_password("1122", click_key=ck, count_stars=count_stars, clear=clear,
                                     find_key=find_key, same_char_pause=lambda: pauses.__setitem__("n", pauses["n"] + 1),
                                     jitter_px=5)
        self.assertTrue(res.ok)
        self.assertGreaterEqual(pauses["n"], 2)                 # 같은 글자 이어질 때(1->1, 2->2) 쉼
        self.assertTrue(all(n in ("vk key", "vk shift") for n in notes))   # note 에 비번 글자 없음


class T13CaptchaPolicy(unittest.TestCase):
    def test_number_auto_off_goes_human(self):
        # N2: number_auto=false 면 숫자 캡차도 사람 멈춤.
        s_cap = screen([("보안문자를 입력해 주세요", 960, 400), ("확인", 960, 600)])
        spec = court_spec(login=None, steps=[{"name": "chk", "type": "click", "el": "z", "words": ["확인"],
                                               "box_r": [0, 0, 1, 1], "ai": False}])
        cfg, d = tmp_cfg(); cfg["captcha"] = dict(cfg.get("captcha", {}), number_auto=False)
        c = Clock(); hw = FakeHW([s_cap]); logs = []
        r = build("court_dongbu", spec, cfg, hw, FakeSecrets(), lambda ev, **k: logs.append(dict(k, ev=ev)),
                  now=c.now, sleep=c.sleep)
        r.power.read = lambda: (90, True); r.console = None
        res = r.run()
        self.assertEqual(res["end"], "captcha_human_required")
        self.assertEqual(hw.actions, [])                        # 입력 0번


def _gray(v, patch=None):
    im = Image.new("RGB", (400, 240), (v, v, v))
    if patch:
        ImageDraw.Draw(im).rectangle([0, 0, 8, 8], fill=(0, 0, 0))
    return im


class T14ScreenState(unittest.TestCase):
    def _run(self, timeline, expect=None, rows_fn=None, caps=None):
        c = Clock(); t0 = c.now()
        def which():
            t = c.now() - t0; acc = 0
            for dur, im, txt in timeline:
                acc += dur
                if t < acc:
                    return im, txt
            return timeline[-1][1], timeline[-1][2]
        ss = ScreenState(lambda: which()[0], lambda im, box=None: which()[1], c.now, c.sleep)
        return ss.settle(expect=expect, caps=caps or merge_caps(None), rows_fn=rows_fn)

    def test_loading_then_done(self):
        tl = [(1.0, _gray(100), "조회중 잠시만"), (1.0, _gray(160), "조회중"),
              (60.0, _gray(128), "검색결과 목록 2025타경1")]
        state, waited, info = self._run(tl, expect=["목록"])
        self.assertEqual(state, "done")
        self.assertGreaterEqual(waited, 1.5)

    def test_frozen_no_expect(self):
        tl = [(60.0, _gray(128), "빈 페이지 설명문구 많음")]
        state, _w, _i = self._run(tl, expect=["목록"])
        self.assertEqual(state, "frozen")

    def test_white_screen_is_loading(self):
        tl = [(60.0, Image.new("RGB", (400, 240), "white"), "")]
        state, _w, _i = self._run(tl, expect=["목록"])
        self.assertEqual(state, "loading_timeout")

    def test_ad_blink_not_fooled(self):
        # 거의 같은 화면(작은 패치만 깜빡) + 기대 글자 있음 → 완료로 본다(로딩 아님).
        tl = [(0.6, _gray(128), "목록 표"), (0.6, _gray(128, patch=True), "목록 표"),
              (60.0, _gray(128), "목록 표")]
        state, _w, _i = self._run(tl, expect=["목록"])
        self.assertEqual(state, "done")

    def test_caps_increase_only(self):
        caps = merge_caps({"load_max_sec": 10, "frozen_sec": 5})     # 기본(60/20)보다 작게 주면 무시
        self.assertEqual(caps["load_max_sec"], 60.0)
        self.assertEqual(caps["frozen_sec"], 20.0)
        caps2 = merge_caps({"load_max_sec": 90})
        self.assertEqual(caps2["load_max_sec"], 90.0)


class T15StallDiag(unittest.TestCase):
    def _d(self, **kw):
        base = dict(move_mouse=lambda dx, dy: None, grab=lambda: blank(), read_clock=lambda: "10:00",
                    now=Clock().now, sleep=lambda s: None)
        base.update(kw)
        return stall_diag.StallDiag(**base)

    def test_pc_frozen(self):
        # 커서 고정 + 시계 고정 + 화면 동일 + NTP 시계 점프(히컵) 까지 확실할 때만 PC_FROZEN (R1)
        seq = [(0.02, 1000), (0.02, 1100)]
        st = {"i": 0}; mono = {"t": 0.0}
        nc = sntp.NtpChecker(query=lambda sv, to=2: seq[min(st["i"], 1)],
                             monotonic=lambda: mono["t"], hiccup_sec=30)
        nc.sweep(); st["i"] = 1; mono["t"] = 5.0      # 95초 점프 준비
        same = blank()
        d = self._d(read_clock=lambda: "10:00", cursor_pos=lambda: (5, 5),
                    grab=lambda: same, ntp=nc)
        code, _ = d.diagnose(site="s")
        self.assertEqual(code, "PC_FROZEN")

    def test_pc_frozen_defer_when_no_cursor(self):
        # 커서를 못 보면(None) 화면이 똑같아도 PC_FROZEN 으로 단정하지 않는다 (R1)
        clk = Clock()
        d = self._d(read_clock=lambda: "10:00", net_bytes=lambda: 5, now=clk.now, sleep=clk.sleep,
                    ping=lambda h, t=2: False, gateway=lambda: "192.168.0.1",
                    dns=lambda: False, neutral=lambda: False)
        code, _ = d.diagnose(site="s")
        self.assertNotEqual(code, "PC_FROZEN")

    def test_normal_screen_not_frozen(self):
        # 정상 화면: 커서가 움직이고 자료도 들어옴 → 절대 PC_FROZEN 아님 (R1)
        clk = Clock()
        vals = [1_000_000, 3_000_000]; st = {"i": 0}
        def nb():
            v = vals[min(st["i"], 1)]; st["i"] += 1; return v
        d = self._d(read_clock=lambda: "10:0%d" % (int(clk.now()) % 9 + 1),
                    cursor_pos=lambda: (int(clk.now()), 0), now=clk.now, sleep=clk.sleep,
                    net_bytes=nb)
        code, _ = d.diagnose(site="s")
        self.assertNotEqual(code, "PC_FROZEN")
        self.assertEqual(code, "DATA_FLOWING")

    def test_data_flowing(self):
        clk = Clock()
        seq = iter([1_000_000, 1_000_000, 2_000_000])
        vals = [1_000_000, 2_000_000]
        st = {"i": 0}
        def nb():
            v = vals[min(st["i"], 1)]; st["i"] += 1; return v
        d = self._d(read_clock=lambda: "10:0%d" % clk.now(), cursor_pos=lambda: (clk.now(), 0),
                    now=clk.now, sleep=clk.sleep, net_bytes=nb)
        code, _ = d.diagnose(site="s")
        self.assertEqual(code, "DATA_FLOWING")

    def test_site_no_response_with_ntp_ok(self):
        clk = Clock()
        ntp = sntp.NtpChecker(query=lambda sv, to=2: (0.01, 1700), now=clk.now, monotonic=lambda: clk.now())
        d = self._d(read_clock=lambda: "10:0%d" % int(clk.now() % 9 + 1), cursor_pos=lambda: (clk.now(), 0),
                    now=clk.now, sleep=clk.sleep, net_bytes=lambda: 5, ntp=ntp,
                    site_head=lambda: True)
        code, detail = d.diagnose(site="s")
        self.assertEqual(code, "SITE_NO_RESPONSE")
        self.assertTrue(detail.get("server_alive"))

    def test_local_net_down_when_all_ntp_down(self):
        clk = Clock()
        ntp = sntp.NtpChecker(query=lambda sv, to=2: None, now=clk.now, monotonic=lambda: clk.now())
        d = self._d(read_clock=lambda: "10:0%d" % int(clk.now() % 9 + 1), cursor_pos=lambda: (clk.now(), 0),
                    now=clk.now, sleep=clk.sleep, net_bytes=lambda: 5, ntp=ntp, ping=lambda h, t=2: False,
                    gateway=lambda: "192.168.0.1")
        code, _ = d.diagnose(site="s")
        self.assertEqual(code, "LOCAL_NET_DOWN")


class T16Sntp(unittest.TestCase):
    def test_one_server_down(self):
        down = {"time.bora.net"}
        def q(sv, to=2): return None if sv in down else (0.02, 1700)
        r = sntp.NtpChecker(query=q).sweep()
        self.assertEqual(r["code"], "SERVER_DOWN")

    def test_region_blocked(self):
        def q(sv, to=2): return None if sv.endswith((".jp", "nict.jp")) or "jp" in sv else (0.02, 1700)
        r = sntp.NtpChecker(query=q).sweep()
        self.assertEqual(r["code"], "REGION_BLOCKED")
        self.assertIn("JP", r["down_regions"])

    def test_all_down(self):
        r = sntp.NtpChecker(query=lambda sv, to=2: None).sweep()
        self.assertEqual(r["code"], "ALL_DOWN")

    def test_time_jump_hiccup(self):
        clk = Clock()
        # 1차: ntp=1000, mono=0. 2차: ntp=1100(100초 흐름), mono=+5초 → 95초 점프
        seq = [(0.02, 1000), (0.02, 1100)]
        st = {"i": 0}
        mono = {"t": 0.0}
        def q(sv, to=2):
            return seq[min(st["i"], 1)]
        def mnow():
            return mono["t"]
        nc = sntp.NtpChecker(query=q, monotonic=mnow, hiccup_sec=30)
        nc.sweep(); st["i"] = 1; mono["t"] = 5.0
        r = nc.sweep()
        self.assertGreaterEqual(r["hiccup"], 30)

    def test_court_iros_host_rejected(self):
        # R5: 지역 목록에 법원·iros 호스트를 넣어도 NTP 대상에서 빠진다.
        r = dict(sntp.merge_regions([["KR", ["www.courtauction.go.kr", "iros.go.kr", "my.ntp.local"]]]))
        self.assertIn("my.ntp.local", r["KR"])
        self.assertNotIn("www.courtauction.go.kr", r["KR"])
        self.assertFalse([h for h in r["KR"] if "courtauction" in h or "iros" in h or ".go.kr" in h])

    def test_merge_regions_increase_only(self):
        r = sntp.merge_regions([["KR", ["my.ntp.local"]], ["AU", ["au.pool.ntp.org"]]])
        kr = dict(r)["KR"]
        self.assertIn("time.kriss.re.kr", kr)        # 기본 유지
        self.assertIn("my.ntp.local", kr)            # 더해짐
        self.assertIn("AU", dict(r))                 # 새 지역


class T17SiteAliveRule(unittest.TestCase):
    def test_court_never_head(self):
        cfg, d = tmp_cfg()
        spec = court_spec(site_alive_check=True)
        c = Clock(); hw = FakeHW([screen(NORMAL)]); logs = []
        r = build("court_dongbu", spec, cfg, hw, FakeSecrets(), lambda ev, **k: logs.append(dict(k, ev=ev)),
                  now=c.now, sleep=c.sleep)
        self.assertIsNone(r._site_head())            # 법원은 site_alive_check 켜도 HEAD 안 만듦
        self.assertTrue(any(l["ev"] == "site_alive_check_refused" for l in logs))


class T18ReviewV13(unittest.TestCase):
    def test_number_auto_string_off(self):
        # R8: 'false'/'0'/'no' 같은 문자열도 끔으로 본다. 미설정이면 기본 True(t761u).
        import tempfile
        for v, want in [("false", False), ("0", False), ("no", False), ("off", False),
                        ("", False), ("true", True), ("1", True), (True, True)]:
            d = tempfile.mkdtemp(prefix="sc_na_")
            fp = os.path.join(d, "c.json")
            json.dump({"captcha": {"number_auto": v}}, open(fp, "w"))
            cfg = config.load(fp)
            self.assertEqual(cfg["captcha"]["number_auto"], want, v)
        d2 = tempfile.mkdtemp(prefix="sc_na2_")
        fp2 = os.path.join(d2, "c.json")
        json.dump({}, open(fp2, "w"))
        self.assertTrue(config.load(fp2)["captcha"]["number_auto"])      # 기본 켜짐

    def test_ntp_check_string_off(self):
        import tempfile
        d = tempfile.mkdtemp(prefix="sc_ntp_")
        fp = os.path.join(d, "c.json")
        json.dump({"ntp_check": "false"}, open(fp, "w"))
        self.assertFalse(config.load(fp)["ntp_check"])

    def test_ai_skipped_when_key_absent(self):
        # R7: 키가 없으면 AI 를 건너뛰고(묻지 않고) None 을 돌려준다.
        asked = []
        post_called = {"n": 0}

        def post(url, body, headers, **k):
            post_called["n"] += 1
            return {"choices": [{"message": {"content": "1"}}]}
        s = FakeSecrets({})                        # 키 없음
        ai = AIHelper(s, dict(config.DEFAULTS["ai"]), NOLOG, post)
        self.assertIsNone(ai.pick("검색", [("a", 1, 1), ("b", 2, 2)], blank((80, 80))))
        self.assertEqual(post_called["n"], 0)      # 요청 자체를 안 보냄
        self.assertEqual(ai.read_digits(blank((40, 40)), "123456"), "123456")   # OCR 보조만

    def test_old_state_file_mult_resets_after_24h(self):
        # R9: 옛 상태 파일(last_429_ts 없음)이어도 last_429_day 로 24시간 지나면 배수 1 로 되돌림.
        import tempfile
        d = tempfile.mkdtemp(prefix="sc_p_")
        fp = os.path.join(d, "r.json")
        json.dump({"gap_mult": 2.0, "last_429_day": "2025-11-01"}, open(fp, "w"))   # ts 없음(옛 파일)
        c = Clock()                                 # now ~ 2025-11-19 (여기선 미래 날짜 기준이라 24h+ 경과)
        rs = RateState(fp, c.now)
        # last_429_day 가 과거(= 24시간보다 전)라면 배수 1 로
        self.assertEqual(rs.effective_mult(), 1.0)

    def test_ntp_timeout_clamped(self):
        # R5: 설정의 ntp.timeout_sec 를 0.5~3.0 으로 조인다.
        import tempfile
        for v, want in [(0.1, 0.5), (10, 3.0), (2, 2.0)]:
            d = tempfile.mkdtemp(prefix="sc_to_")
            fp = os.path.join(d, "c.json")
            json.dump({"ntp": {"timeout_sec": v}}, open(fp, "w"))
            self.assertEqual(config.load(fp)["ntp"]["timeout_sec"], want)

    def test_ntp_all_down_but_internet_ok_is_site_no_response(self):
        # R5: UDP 123 만 막힌 경우(ALL_DOWN) 중립 사이트가 되면 ISP/LOCAL 이 아니라 SITE_NO_RESPONSE.
        clk = Clock()
        ntp = sntp.NtpChecker(query=lambda sv, to=2: None, now=clk.now, monotonic=lambda: clk.now())
        d = stall_diag.StallDiag(move_mouse=lambda dx, dy: None, grab=lambda: blank(),
                                 read_clock=lambda: "10:0%d" % (int(clk.now()) % 9 + 1),
                                 cursor_pos=lambda: (int(clk.now()), 0), now=clk.now, sleep=clk.sleep,
                                 net_bytes=lambda: 5, ntp=ntp, neutral=lambda: True,
                                 ping=lambda h, t=2: False, gateway=lambda: "192.168.0.1")
        code, _ = d.diagnose(site="s")
        self.assertEqual(code, "SITE_NO_RESPONSE")

    def test_old_state_file_mult_kept_within_24h(self):
        import tempfile
        import datetime as dt
        d = tempfile.mkdtemp(prefix="sc_p2_")
        fp = os.path.join(d, "r.json")
        c = Clock()
        today = dt.datetime.fromtimestamp(c.now()).strftime("%Y-%m-%d")
        json.dump({"gap_mult": 2.0, "last_429_day": today}, open(fp, "w"))
        rs = RateState(fp, c.now)
        self.assertEqual(rs.effective_mult(), 2.0)   # 오늘(23:59 기준) 이면 아직 24h 안 지남 → 유지


class T19ReviewV14(unittest.TestCase):
    class _FakeHW:
        def __init__(self, words):
            self.words = words

        def ocr_words(self, im, box=None):
            return list(self.words)

    def _runner(self, words, in_login=False, red=None):
        import types
        r = runner.Runner.__new__(runner.Runner)
        r.hw = self._FakeHW(words)
        r.in_login = in_login
        r.ai = types.SimpleNamespace(red=red)
        r.locator = types.SimpleNamespace(bank=None, find=lambda *a, **k: None)
        return r

    def test_uppercase_id_blocks_ai(self):
        # V4 ①: 대문자 "ID"/"PASSWORD" 도 개인정보로 보고 AI 금지.
        r = self._runner([("ID", 10, 10), ("12345", 20, 20)])
        self.assertFalse(r._captcha_crop_safe_for_ai(blank((60, 30)), (0, 0, 60, 30)))
        r2 = self._runner([("PASSWORD", 10, 10)])
        self.assertFalse(r2._captcha_crop_safe_for_ai(blank((60, 30)), (0, 0, 60, 30)))

    def test_plain_digits_allowed(self):
        # 숫자만 보이면 AI 허용(개인정보 없음).
        r = self._runner([("483920", 10, 10)])
        self.assertTrue(r._captcha_crop_safe_for_ai(blank((60, 30)), (0, 0, 60, 30)))

    def test_redactor_partial_match_blocks(self):
        # V4 ③: 비밀값이 부분으로라도 보이면 AI 금지(정확히 같지 않아도).
        class Red:
            def text(self, t):
                return t.replace("myid99", "***")
        r = self._runner([("login myid99 here", 10, 10)], red=Red())
        self.assertFalse(r._captcha_crop_safe_for_ai(blank((80, 30)), (0, 0, 80, 30)))

    def test_captcha_box_offset_not_centered_on_label(self):
        # V4 ②: 라벨만 찾으면 상자를 그림 방향(아래)으로 옮겨 자른다(라벨 가운데 아님).
        import types
        r = runner.Runner.__new__(runner.Runner)
        r.locator = types.SimpleNamespace(
            bank=None, find=lambda im, el, words, box, use_ai=False, learn=False: (960, 300, "ocr"))
        im = blank((1920, 1080))
        cs = {"image_offset_r": [0.0, 0.1, 0.26, 0.11]}
        box, by = r._captcha_box(im, cs)
        self.assertEqual(by, "ocr")
        cy = (box[1] + box[3]) / 2
        self.assertGreater(cy, 300 + 50)                  # 중심이 라벨(300)보다 충분히 아래


# ---------------- v1.5 ----------------
def _court_nonmetro():
    return json.load(open(os.path.join(ROOT, "collector_kb", "sites", "court_nonmetro.json"), encoding="utf-8"))


def _paste_captcha(im, box, seed):
    t = textured((box[2] - box[0], box[3] - box[1]), seed)
    im.paste(t, (box[0], box[1]))
    return im


class T20ReviewV15(unittest.TestCase):
    mk = T09Runner.mk

    # ---- W1 ----
    def _captcha_screen(self, seed=3, inp_xy=(960, 650)):
        im = Image.new("RGB", (1920, 1080), (200, 200, 200))
        _paste_captcha(im, (720, 470, 1200, 575), seed)
        words = [("보안문자", 960, 430), ("입력", inp_xy[0], inp_xy[1])]
        return (im, words, "법원경매정보 - Chrome")

    def test_w1_real_court_json_ai_gets_real_image_not_white(self):
        # W1: 실제 법원 JSON 값(input_box_r 넓은 범위)으로도 AI 그림이 하얗게 지워지지 않는다.
        spec = _court_nonmetro()
        spec["login"] = None
        sc = self._captcha_screen()
        r, hw, logs, cfg, c = self.mk(spec, [sc, screen(NORMAL)])
        seen = []
        r.ai = type("A", (), {"red": None, "read_digits": lambda self_, im, *a: seen.append(im.copy()) or "483920"})()
        hw.on_input = lambda h, kind, note: h.next_screen() if (kind == "key" and note == "ENTER") else None
        r.do_captcha_number(sc[0], "t", 0)
        self.assertEqual(len(seen), 1)
        g = seen[0].convert("L")
        white = sum(1 for v in g.getdata() if v >= 250) / float(g.size[0] * g.size[1])
        self.assertLess(white, 0.5)                                   # 예전엔 1.000
        self.assertFalse(captcha.is_blank(seen[0]))
        self.assertIn("captcha_input", r.input_boxes)
        ib = r.input_boxes["captcha_input"]
        self.assertLess(ib[2] - ib[0], 0.2 * 1920)                    # 가리기 상자는 칸 하나 크기(넓은 범위 아님)
        self.assertTrue(any(l["ev"] == "captcha_solved" and l["by"] == "ai_ok" for l in logs))

    def test_w1_memory_key_differs_and_blank_has_no_key(self):
        a, b = textured((240, 60), 1), textured((240, 60), 2)
        self.assertNotEqual(captcha._img_key(a), captcha._img_key(b))
        self.assertIsNone(captcha._img_key(blank((240, 60))))
        self.assertIsNone(captcha._img_key(blank((240, 60), "gray")))
        import tempfile
        m = captcha.CaptchaMemory(os.path.join(tempfile.mkdtemp(prefix="sc_m_"), "m.json"))
        m.put(blank((240, 60)), "123456")
        self.assertEqual(m.d, {})                                     # 빈 그림은 기억 안 함
        self.assertIsNone(m.get(blank((240, 60))))

    def test_w1_overlap_with_input_goes_human_not_ai(self):
        # W1: 정확한 입력칸이 캡차 상자와 겹치면 AI 로 보내지 않고 사람에게.
        spec = _court_nonmetro()
        spec["login"] = None
        sc = self._captcha_screen(inp_xy=(960, 540))                  # 입력칸이 그림 한가운데(겹침)
        r, hw, logs, cfg, c = self.mk(spec, [sc, screen(NORMAL)])
        r.ai = type("A", (), {"red": None, "read_digits": lambda self_, im, *a: self.fail("AI 금지")})()
        ask = os.path.join(cfg["state_dir"], "captcha_ask")
        base_sleep = r.sleep

        def sl(sec):
            if os.path.exists(os.path.join(ask, "pending.json")) and not os.path.exists(os.path.join(ask, "answer.json")):
                json.dump({"digits": "112233"}, open(os.path.join(ask, "answer.json"), "w"))
            base_sleep(sec)
        r.sleep = sl
        hw.on_input = lambda h, kind, note: h.next_screen() if (kind == "key" and note == "ENTER") else None
        r.do_captcha_number(sc[0], "t", 0)
        sk = [l for l in logs if l["ev"] == "captcha_ai_skipped"]
        self.assertTrue(sk and sk[0]["overlap"] is True)
        self.assertTrue(any(l["ev"] == "captcha_solved" and l["by"] == "human_ok" for l in logs))

    def test_w1_blank_crop_no_ai_no_memory(self):
        spec = _court_nonmetro()
        spec["login"] = None
        im = Image.new("RGB", (1920, 1080), (200, 200, 200))          # 그림이 없음(한 색)
        sc = (im, [("보안문자", 960, 430), ("입력", 960, 650)], "t")
        r, hw, logs, cfg, c = self.mk(spec, [sc, screen(NORMAL)])
        r.ai = type("A", (), {"red": None, "read_digits": lambda self_, im, *a: self.fail("AI 금지")})()
        ask = os.path.join(cfg["state_dir"], "captcha_ask")
        base_sleep = r.sleep

        def sl(sec):
            if os.path.exists(os.path.join(ask, "pending.json")) and not os.path.exists(os.path.join(ask, "answer.json")):
                json.dump({"digits": "445566"}, open(os.path.join(ask, "answer.json"), "w"))
            base_sleep(sec)
        r.sleep = sl
        hw.on_input = lambda h, kind, note: h.next_screen() if (kind == "key" and note == "ENTER") else None
        r.do_captcha_number(sc[0], "t", 0)
        self.assertTrue(any(l["ev"] == "captcha_crop_blank" for l in logs))
        self.assertFalse(os.path.exists(os.path.join(cfg["kb_dir"], "captcha_memory.json")))   # 기억 저장 안 함

    # ---- W2 / V2 ----
    def test_w2_already_logged_out_no_stop(self):
        r, hw, logs, cfg, c = self.mk(court_spec(steps=[]), [screen([("법원경매정보", 400, 170), ("로그인", 1200, 165)])])
        r.logged_in = True
        self.assertTrue(r._safe_logout())
        self.assertFalse(r.logged_in)
        self.assertFalse(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))
        self.assertTrue(any(l["ev"] == "logout" and l.get("already") for l in logs))

    def test_v2_recover_then_already_logged_out_no_stop(self):
        # V2 c: 망 복구 뒤 이미 로그아웃된 화면 → STOP 없이 net_recovered_relogin.
        r, hw, logs, cfg, c = self.mk(court_spec(steps=[]), [screen([("법원경매정보", 400, 170), ("로그인", 1200, 165)])])
        r.cfg["stall"]["net_down_wait_min"] = 10
        r.logged_in = True
        calls = {"n": 0}

        def diag(where, net_skip=False):
            calls["n"] += 1
            return ("LOCAL_NET_DOWN" if calls["n"] < 2 else "OK"), {}
        r._diagnose = diag
        with self.assertRaises(RunEnd) as ctx:
            r._wait_net_recover("w", None, None, None, "LOCAL_NET_DOWN")
        self.assertEqual(ctx.exception.why, "net_recovered_relogin")
        self.assertFalse(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))

    # ---- W3 ----
    def test_w3_bool_settings_strict(self):
        import tempfile
        for v, want in [("false", False), ("0", False), ("no", False), ("off", False), ("", False),
                        ("true", True), ("yes", True), (1, True), (True, True)]:
            fp = os.path.join(tempfile.mkdtemp(prefix="sc_b_"), "c.json")
            json.dump({"ntp_check": v, "kb_server": {"verify_tls": v}}, open(fp, "w"))
            cfg = config.load(fp)
            self.assertIs(cfg["ntp_check"], want, v)
            self.assertIs(cfg["kb_server"]["verify_tls"], want, v)
        fp = os.path.join(tempfile.mkdtemp(prefix="sc_b2_"), "c.json")
        json.dump({}, open(fp, "w"))
        cfg = config.load(fp)
        self.assertIs(cfg["ntp_check"], True)
        self.assertIs(cfg["kb_server"]["verify_tls"], True)

    def test_h5_heartbeat_during_long_break(self):
        r, hw, logs, cfg, c = self.mk(court_spec(steps=[]), [screen(NORMAL)])
        beats = {"n": 0}
        r.account_lock = type("L", (), {"heartbeat": lambda self_: beats.__setitem__("n", beats["n"] + 1)})()
        r._got_lock = True
        r.pacer.sleep(20 * 60)                                         # 20분 휴식
        self.assertGreaterEqual(beats["n"], 19)

    # ---- C8 스크롤 법원 고르기 ----
    class DropHW(FakeHW):
        def __init__(self, pages, selected="서울중앙지방법원"):
            super().__init__([(blank(), [], "법원경매정보 - Chrome")])
            self.pages, self.page, self.open, self.sel = pages, 0, False, selected
            self.clicked = []

        def ocr_words(self, im, box=None, scale=2):
            ws = [("매각예정물건", 400, 300), (self.sel, 1000, 430)]
            if self.open:
                ws += [(t, 900, 660 + 40 * i) for i, t in enumerate(self.pages[self.page])]
            if box is None:
                return ws
            return [w for w in ws if box[0] <= w[1] <= box[2] and box[1] <= w[2] <= box[3]]

        def _click(self, x, y, note):
            self.clicked.append(note)
            if self.open and y >= 640:
                i = (y - 660 + 20) // 40
                items = self.pages[self.page]
                if 0 <= i < len(items):
                    self.sel = items[i]
                self.open = False
            else:
                self.open = True

        def _wheel(self, x, y, ticks):
            if self.open and ticks < 0:
                self.page = min(len(self.pages) - 1, self.page + 1)

        def _key(self, k):
            super()._key(k)
            if k == "DOWN":
                flat = [t for p in self.pages for t in p]
                i = flat.index(self.sel) if self.sel in flat else -1
                self.sel = flat[min(len(flat) - 1, i + 1)]
            if k == "ENTER":
                self.open = False

    PAGES = [["서울중앙지방법원", "서울동부지방법원", "서울남부지방법원"],
             ["의정부지방법원", "인천지방법원", "수원지방법원"],
             ["대전지방법원 천안지원", "대전지방법원 홍성지원", "청주지방법원"],
             ["대전지방법원", "대구지방법원", "부산지방법원"]]

    def _sel_runner(self, hw, target, refuse_metro=True):
        spec = _court_nonmetro()
        st = [x for x in spec["steps"] if x.get("name") == "court_select"][0]
        st = dict(st, target=target, refuse_metro=refuse_metro, wait=[0, 0])
        cfg, d = tmp_cfg()
        c = Clock()
        logs = []
        r = build("court_nonmetro", dict(spec, steps=[]), cfg, hw, FakeSecrets(),
                  lambda ev, **k: logs.append(dict(k, ev=ev)) or k, now=c.now, sleep=c.sleep)
        r.power.read = lambda: (90, True)
        return r, st, logs, cfg

    def test_c8_found_on_third_wheel_exact_not_trap(self):
        hw = self.DropHW(self.PAGES)
        r, st, logs, cfg = self._sel_runner(hw, "대전지방법원")
        r.do_select_scroll(st)
        self.assertEqual(hw.sel, "대전지방법원")                         # 천안지원·홍성지원 함정 안 고름
        self.assertEqual(sum(1 for a in hw.actions if a[0] == "wheel"), 3)
        ev = [l for l in logs if l["ev"] == "select_scroll"]
        self.assertTrue(any(l.get("ev") == "select_scroll" for l in ev))
        self.assertIn("대전지방법원", hw.clicked)

    def test_c8_not_in_list_stops(self):
        hw = self.DropHW(self.PAGES[:3])
        r, st, logs, cfg = self._sel_runner(hw, "제주지방법원")
        with self.assertRaises(RunEnd) as ctx:
            r.do_select_scroll(st)
        self.assertEqual(ctx.exception.why, "COURT_NOT_FOUND")
        self.assertTrue(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_nonmetro")))
        self.assertIn("DOWN", [a[1] for a in hw.actions if a[0] == "key"])   # 휠 끝 → 아래키도 시도

    def test_c8_metro_never_clicked(self):
        hw = self.DropHW(self.PAGES)
        r, st, logs, cfg = self._sel_runner(hw, "서울동부지방법원", refuse_metro=True)
        with self.assertRaises(RunEnd):
            r.do_select_scroll(st)
        self.assertNotIn("서울동부지방법원", hw.clicked)

    # ---- 사이트 거부 기록 ----
    def test_refusal_court_policy_refused_no_request(self):
        spec = _court_nonmetro()
        spec["site_alive_check"] = True
        r, hw, logs, cfg, c = self.mk(spec, [screen(NORMAL)])
        self.assertIsNone(r._site_head())
        recs = r.refusals.read()
        self.assertEqual(len(recs), 1)
        x = recs[0]
        self.assertEqual((x["actor"], x["reason"], x["kind"]), ("POLICY", "POLICY_REFUSED", "site_alive"))
        self.assertIn("courtauction", x["host"])
        self.assertTrue(x["ts_kst"].endswith("+09:00"))
        self.assertIsNone(x["next_allowed_kst"])
        for k in ("http_status", "socket_error", "attempt"):
            self.assertIn(k, x)

    def _http(self, code):
        hits = {"n": 0}

        class H(BaseHTTPRequestHandler):
            def do_HEAD(self_):
                hits["n"] += 1
                self_.send_response(code)
                self_.end_headers()

            def log_message(self_, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return srv.server_address[1], hits

    def test_refusal_remote_http_503_and_next_allowed(self):
        port, hits = self._http(503)
        spec = {"site": "http://127.0.0.1:%d/" % port, "family": "map", "job": "map_x", "site_alive_check": True,
                "steps": []}
        r, hw, logs, cfg, c = self.mk(spec, [screen(NORMAL)])
        head = r._site_head()
        self.assertFalse(head())
        self.assertFalse(head())                                       # 다음 허용 시각 전 → 요청 안 보냄
        self.assertEqual(hits["n"], 1)
        x = r.refusals.read()[0]
        self.assertEqual((x["actor"], x["reason"], x["http_status"], x["attempt"]), ("REMOTE", "HTTP_5XX", 503, 1))
        self.assertEqual(x["host"], "127.0.0.1")
        self.assertIsNotNone(x["next_allowed_kst"])
        c.sleep(301)
        head()
        recs = r.refusals.read()
        self.assertEqual([y["attempt"] for y in recs], [1, 2])          # 시도 번호
        self.assertEqual(recs[0], x)                                    # 덧붙이기만(앞줄 그대로)

    def test_refusal_socket_error(self):
        import socket as _s
        so = _s.socket()
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
        so.close()                                                     # 닫힌 포트 → 연결 거절
        spec = {"site": "http://127.0.0.1:%d/" % port, "family": "map", "job": "map_x", "site_alive_check": True,
                "steps": []}
        r, hw, logs, cfg, c = self.mk(spec, [screen(NORMAL)])
        self.assertFalse(r._site_head()())
        x = r.refusals.read()[0]
        self.assertEqual(x["actor"], "REMOTE")
        self.assertEqual(x["reason"], "CONN_REFUSED")
        self.assertEqual(x["socket_error"], "ConnectionRefusedError")
        self.assertIsNone(x["http_status"])

    def test_refusal_ntp_banned_and_no_response(self):
        from standalone.refusal_log import RefusalLog
        import tempfile
        rl = RefusalLog(os.path.join(tempfile.mkdtemp(prefix="sc_rl_"), "r.jsonl"), now=Clock().now)
        regs = sntp.merge_regions([["kr", ["time.scourt.go.kr", "ntp.example.org"]]],
                                  on_ban=lambda sv: rl.policy_refused(sv, "ntp"))
        self.assertNotIn("time.scourt.go.kr", [sv for _n, ss in regs for sv in ss])
        nc = sntp.NtpChecker(regions=[["x", ["ntp.example.org"]]], query=lambda sv, to=2: None,
                             on_fail=lambda sv, why: rl.remote_fail(sv, "ntp", reason=why, retry_sec=600))
        nc.sweep()
        recs = rl.read()
        self.assertEqual((recs[0]["actor"], recs[0]["host"], recs[0]["kind"]), ("POLICY", "time.scourt.go.kr", "ntp"))
        self.assertEqual((recs[1]["actor"], recs[1]["reason"]), ("REMOTE", "NTP_NO_RESPONSE"))
        self.assertIsNotNone(recs[1]["next_allowed_kst"])

    def test_kb_server_banned_host_refused(self):
        from standalone.kb_sync import check_server_url
        with self.assertRaises(PermissionError):
            check_server_url({"url": "https://www.courtauction.go.kr/api"})

    # ---- 조각 그림 다중 크기 ----
    def _scene(self, seed=11, size=(1600, 1000)):
        import random as _r
        rnd = _r.Random(seed)
        im = Image.new("L", (size[0] // 8, size[1] // 8))
        im.putdata([rnd.randrange(0, 256) for _ in range(im.size[0] * im.size[1])])
        return im.resize(size, Image.NEAREST).convert("RGB")

    def _bank(self, **kw):
        import tempfile
        return SnippetBank(tempfile.mkdtemp(prefix="sc_ms_"), **kw)

    def _scales(self, bank, name):
        return {s: len(bank.files(name, s)) for s in (1, 2, 4, 8)}

    def test_ms_every_10_100_300(self):
        im = self._scene()
        b = self._bank()
        for i in range(9):
            b.learn(im, "btn", 700, 500)
        self.assertEqual(self._scales(b, "btn")[2], 0)
        b.learn(im, "btn", 700, 500)                                    # 10번째
        self.assertEqual(self._scales(b, "btn")[2], 1)
        self.assertEqual(Image.open(b.files("btn", 2)[0]).size, (240, 56))
        for i in range(90):
            b.learn(im, "btn", 700, 500)                                # 100번째
        sc = self._scales(b, "btn")
        self.assertEqual(sc[4], 1)
        self.assertEqual(sc[2], 3)                                      # 크기마다 최대 3장
        self.assertEqual(sc[8], 0)
        for i in range(200):
            b.learn(im, "btn", 700, 500)                                # 300번째
        self.assertEqual(self._scales(b, "btn")[8], 1)
        self.assertEqual(Image.open(b.files("btn", 8)[0]).size, (960, 224))
        self.assertEqual(self._scales(b, "btn")[1], 1)                  # 같은 그림 1배는 중복 저장 안 함

    def test_ms_fail_based_2_5_10(self):
        im = self._scene()
        b = self._bank()
        for n, want in ((2, {2: 1, 4: 0, 8: 0}), (5, {2: 1, 4: 1, 8: 0}), (10, {2: 1, 4: 1, 8: 1})):
            while b.meta["fails"].get("menu", 0) < n:
                b.note_fail("menu")
            b.learn(im, "menu", 400, 300)
            b.learn(im, "menu", 400, 300)                               # 문턱마다 한 장씩만
            got = self._scales(b, "menu")
            self.assertEqual({k: got[k] for k in (2, 4, 8)}, want, n)

    def test_ms_fail_thresholds_from_config(self):
        im = self._scene()
        b = self._bank(scale_cfg={"fail": {"2": 1, "4": 2, "8": 3}})
        b.note_fail("x")
        b.learn(im, "x", 400, 300)
        self.assertEqual(self._scales(b, "x")[2], 1)

    def test_ms_find_big_first_click_from_1x(self):
        im = self._scene()
        b = self._bank(scale_cfg={"fail": {"2": 1, "4": 1, "8": 1}})
        b.note_fail("go")
        b.learn(im, "go", 700, 500)
        self.assertEqual({k: v for k, v in self._scales(b, "go").items()}, {1: 1, 2: 1, 4: 1, 8: 1})
        from standalone.snippets import match
        one = match(im, Image.open(b.files("go", 1)[0]), (0, 0) + im.size)
        hit = b.find(im, "go", None)
        self.assertEqual(b.last_scale, 8)                               # 큰 것부터
        self.assertEqual(hit[:2], one[:2])                              # 좌표는 1배 조각 기준
        self.assertEqual(hit[2], "go.png")
        self.assertLessEqual(abs(hit[0] - 700) + abs(hit[1] - 500), 2)
        st = b.meta["scale_stats"]["go"]
        self.assertEqual(st["8"]["ok"], 1)

    def test_ms_rank_per_scale_changes_order(self):
        im = self._scene()
        b = self._bank(scale_cfg={"fail": {"2": 1, "4": 1, "8": 1}})
        b.note_fail("go")
        b.learn(im, "go", 700, 500)
        other = self._scene(seed=99)                                    # 8배 그림을 엉뚱한 그림으로 바꿈
        p8 = b.files("go", 8)[0]
        other.crop((0, 0, 960, 224)).save(p8)
        b.find(im, "go", None)
        st = b.meta["scale_stats"]["go"]
        self.assertEqual(st["8"]["fail"], 1)
        self.assertEqual(b.last_scale, 4)
        self.assertEqual(b.scale_order("go")[0], 4)                     # 순위 자동 변경(크기별)
        self.assertEqual(b.rank["go"][os.path.basename(p8)]["fail"], 1)

    def test_ms_big_alone_never_gives_coordinates(self):
        im = self._scene()
        b = self._bank(scale_cfg={"fail": {"2": 1, "4": 1, "8": 1}})
        b.note_fail("go")
        b.learn(im, "go", 700, 500)
        for p in b.files("go", 1):
            os.remove(p)                                                # 1배 없음
        self.assertIsNone(b.find(im, "go", None))

    def test_ms_masks_id_pw_captcha_fields_in_big(self):
        im = self._scene()
        field = (760, 490, 860, 510)                                    # 아이디 입력칸(정확한 칸)
        b = self._bank(scale_cfg={"fail": {"2": 1, "4": 1, "8": 1}}, mask_fn=lambda _im: [field])
        b.note_fail("go")
        b.learn(im, "go", 700, 500)
        for s in (2, 4, 8):
            p = b.files("go", s)[0]
            big = Image.open(p).convert("L")
            ox, oy = b.meta["centers"][os.path.basename(p)]
            x0, y0 = 700 - ox, 500 - oy
            self.assertEqual(big.getpixel((800 - x0, 500 - y0)), 128, s)        # 칸 안 = 가림
            self.assertNotEqual(big.getpixel((700 - x0, 470 - y0 if s > 2 else 495 - y0)), None)
        g = im.convert("L")
        b8 = Image.open(b.files("go", 8)[0]).convert("L")
        ox, oy = b.meta["centers"][os.path.basename(b.files("go", 8)[0])]
        self.assertEqual(b8.getpixel((ox - 300, oy)), g.getpixel((400, 500)))   # 칸 밖은 그대로

    def test_ms_runner_masks_found_login_fields(self):
        r, hw, logs, cfg, c = self.mk(court_spec(steps=[]), [screen(NORMAL)])
        r.input_boxes["login_id"] = r._field_box(blank(), 900, 400)
        self.assertIs(r.locator.bank.mask_fn.__func__, runner.Runner._mask_boxes)
        bx = r._mask_boxes(blank())
        self.assertEqual(len(bx), 1)
        self.assertLess(bx[0][2] - bx[0][0], 400)

    def test_ms_forbidden_kept_apart(self):
        im = self._scene()
        b = self._bank()
        b.learn(im, "cert", 300, 200, forbidden=True)
        self.assertEqual(b.files("cert", 1), [])
        self.assertEqual(len(b.forbidden_files("cert")), 1)
        self.assertIsNone(b.find(im, "cert", None))
        self.assertEqual(b.forbidden_near(im, 300, 200), "cert.png")
        L = Locator(b, lambda _im, box=None: [("확인", 305, 205)], None, NOLOG)
        self.assertIsNone(L.find(im, "ok_btn", ["확인"], (0, 0, 1, 1)))   # 금지 그림 근처면 거부
        self.assertIsNone(safe_rel("snippets/_forbidden/cert.png"))      # 서버와 안 주고받음

    def test_ms_locator_counts_fail(self):
        im = self._scene()
        b = self._bank()
        L = Locator(b, lambda _im, box=None: [], None, NOLOG)
        L.find(im, "nothing", ["없음"], (0, 0, 1, 1))
        L.find(im, "nothing", ["없음"], (0, 0, 1, 1), learn=False)       # 탐색용(learn=False)은 안 셈
        self.assertEqual(b.meta["fails"]["nothing"], 1)



# ---------------- v1.6 참고자료(관제기 필수·등급·메모리·화보) ----------------
import io as _io
import hashlib as _hl
from standalone import refpack
from standalone.app import prepare_online


def _png(im):
    b = _io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


def _noise(seed, size=(160, 100)):
    import random as _r
    rnd = _r.Random(seed)
    im = Image.new("L", (size[0] // 4, size[1] // 4))
    im.putdata([rnd.randrange(0, 256) for _ in range(im.size[0] * im.size[1])])
    return im.resize(size, Image.NEAREST).convert("RGB")


class FakeControl:
    """시험용 가짜 관제기: 로그인·DB 매니페스트·참고자료 매니페스트·자료·보고·올리기."""
    user, pw = "u-test", "p-test"

    def __init__(self, site="www.courtauction.go.kr", grade="A", managed=True, gallery=True):
        self.site, self.grade, self.managed = site, grade, managed
        self.hits, self.reports, self.uploads = [], [], []
        self.version = "rp-1"
        self.bad_sha = set()
        self.files = {"snippets/%s/search_btn.png" % site: (_png(_noise(1, (120, 28))), "snippet", "search", False)}
        if gallery:
            self.files["snippets/%s/gallery/search.png" % site] = (_png(_noise(2)), "gallery", "search", True)
        self.extra_bytes = 0

    def manifest(self):
        fs = []
        for p, (b, kind, step, rep) in self.files.items():
            fs.append({"path": p, "sha256": _hl.sha256(b).hexdigest() if p not in self.bad_sha else "0" * 64,
                       "bytes": len(b) + (self.extra_bytes if kind == "gallery" else 0), "kind": kind,
                       "step": step, "representative": rep, "ts": 1})
        m = {"site_id": self.site, "version": self.version, "files": fs, "managed": self.managed}
        if self.grade:
            m["grade"] = self.grade
        return m

    def serve(self):
        ctl = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_POST(self):
                ctl.hits.append(("POST", self.path))
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path.endswith("/auth/login"):
                    if body.get("username") == ctl.user and body.get("password") == ctl.pw:
                        return self._json(200, {"token": "tok-1"})
                    return self._json(401, {"error": "bad_login"})
                if self.headers.get("Authorization") != "Bearer tok-1":
                    return self._json(401, {"error": "no_token"})
                if self.path.endswith("/refpack/report"):
                    ctl.reports.append(body)
                    return self._json(200, {"ok": True})
                if self.path.endswith("/refpack/upload"):
                    ctl.uploads.append(body)
                    return self._json(200, {"ok": True})
                return self._json(404, {})

            def do_GET(self):
                ctl.hits.append(("GET", self.path))
                if self.headers.get("Authorization") != "Bearer tok-1":
                    return self._json(401, {"error": "no_token"})
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path)
                q = parse_qs(u.query)
                if u.path.endswith("/kb/manifest"):
                    return self._json(200, {"kb_version": "kb-1", "files": []})
                if u.path.endswith("/refpack/manifest"):
                    return self._json(200, ctl.manifest())
                if u.path.endswith("/refpack/file"):
                    p = q.get("path", [""])[0]
                    if p in ctl.files:
                        b = ctl.files[p][0]
                        self.send_response(200)
                        self.send_header("Content-Type", "application/octet-stream")
                        self.end_headers()
                        self.wfile.write(b)
                        return
                return self._json(404, {})
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv


class T22RefPack(unittest.TestCase):
    def kb(self, url, pw="p-test"):
        cfg, d = tmp_cfg()
        host = ""
        if url.startswith("http://"):
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
        cs = {"url": url, "timeout_sec": 3, "allow_http": bool(host), "host": host, "verify_tls": True}
        cfg["kb_server"] = cs
        k = KBSync(cfg["kb_dir"], cs, FakeSecrets({"KB_SERVER_USER": "u-test", "KB_SERVER_PASSWORD": pw}), "1.6", "pc")
        return k, cfg

    def live(self, ctl=None, pw="p-test"):
        ctl = ctl or FakeControl()
        srv = ctl.serve()
        self.addCleanup(srv.shutdown)
        k, cfg = self.kb("http://127.0.0.1:%d" % srv.server_port, pw)
        return ctl, k, cfg

    def _ui(self):
        seen = []
        return seen, (lambda msg, state="learning", **kw: seen.append((state, msg)))

    # ---- 접속 실패면 STOP ----
    def test_no_url_stops_with_reason(self):
        k, cfg = self.kb("")
        seen, ui = self._ui()
        self.assertIsNone(prepare_online("court_x", {"site": "www.courtauction.go.kr"}, cfg, k, NOLOG, ui))
        stop = open(os.path.join(cfg["stop_dir"], "STOP_court_x"), encoding="utf-8").read()
        self.assertIn("server_required server_url_missing", stop)
        self.assertEqual(seen[-1][0], "stopped")

    def test_unreachable_and_bad_login_stop(self):
        k, cfg = self.kb("http://127.0.0.1:9")
        self.assertIsNone(prepare_online("court_x", {"site": "s"}, cfg, k, NOLOG, self._ui()[1]))
        self.assertIn("server_unreachable", open(os.path.join(cfg["stop_dir"], "STOP_court_x"), encoding="utf-8").read())
        ctl, k2, cfg2 = self.live(pw="wrong")
        self.assertIsNone(prepare_online("court_x", {"site": ctl.site}, cfg2, k2, NOLOG, self._ui()[1]))
        self.assertIn("server_unreachable", open(os.path.join(cfg2["stop_dir"], "STOP_court_x"), encoding="utf-8").read())
        self.assertFalse([h for h in ctl.hits if "/refpack/file" in h[1]])        # 자료는 받지 않음

    def test_runner_on_real_hw_needs_refpack(self):
        r, hw, logs, cfg, c = T09Runner.mk(self, court_spec(steps=[]), [screen(NORMAL)])
        r.require_refpack, r.refpack = True, None
        self.assertEqual(r.run()["end"], "server_required")
        self.assertTrue(os.path.exists(os.path.join(cfg["stop_dir"], "STOP_court_dongbu")))

    # ---- 등급 ----
    def test_grades(self):
        MB = refpack.MB
        self.assertEqual(refpack.site_grade("www.courtauction.go.kr", {"grade": "A"}), ("A", 500 * MB, True))
        self.assertEqual(refpack.site_grade("www.courtauction.go.kr", {}), ("A", 500 * MB, True))   # 법원 기본 A
        self.assertEqual(refpack.site_grade("x.com", {"grade": "A", "managed": False})[1], 100 * MB)  # 비관리 최대 100MB
        self.assertEqual(refpack.site_grade("x.com", None), ("C", 10 * MB, False))                 # 기본 C
        self.assertEqual(refpack.site_grade("x.com", {"grade": "D"})[1], 1 * MB)
        self.assertEqual(refpack.site_grade("www.courtauction.go.kr", {}, {"site_grades": {"courtauction": "B"}})[0], "B")

    def test_over_grade_refused(self):
        ctl = FakeControl(site="simple.example", grade="D")
        ctl.extra_bytes = 2 * refpack.MB
        ctl, k, cfg = self.live(ctl)
        self.assertIsNone(prepare_online("map_x", {"site": ctl.site}, cfg, k, NOLOG, self._ui()[1]))
        self.assertIn("refpack_over_grade", open(os.path.join(cfg["stop_dir"], "STOP_map_x"), encoding="utf-8").read())

    def test_no_gallery_refused_and_sha_mismatch(self):
        ctl, k, cfg = self.live(FakeControl(gallery=False))
        self.assertIsNone(prepare_online("court_x", {"site": ctl.site}, cfg, k, NOLOG, self._ui()[1]))
        self.assertIn("refpack_no_gallery", open(os.path.join(cfg["stop_dir"], "STOP_court_x"), encoding="utf-8").read())
        ctl2 = FakeControl()
        ctl2.bad_sha.add(next(iter(ctl2.files)))
        ctl2, k2, cfg2 = self.live(ctl2)
        self.assertIsNone(prepare_online("court_x", {"site": ctl2.site}, cfg2, k2, NOLOG, self._ui()[1]))
        self.assertIn("sha_mismatch", open(os.path.join(cfg2["stop_dir"], "STOP_court_x"), encoding="utf-8").read())

    # ---- 메모리 적재·학습 중 표시·해제 ----
    def test_load_learning_ui_memory_and_release(self):
        ctl, k, cfg = self.live()
        ui = refpack.file_ui(cfg["state_dir"])
        states = []
        orig = ui

        def ui2(msg, state="learning", **kw):
            states.append((state, msg))
            if state == "learning":
                st = json.load(open(os.path.join(cfg["state_dir"], "ui_status.json"), encoding="utf-8")) \
                    if os.path.exists(os.path.join(cfg["state_dir"], "ui_status.json")) else {}
                states.append(("file_before", st.get("state")))
            orig(msg, state=state, **kw)
        client, pack = prepare_online("court_x", {"site": ctl.site}, cfg, k, NOLOG, ui2)
        self.assertEqual(states[0], ("learning", refpack.LEARNING_MSG))                 # '해당 사이트 학습 중입니다…'
        self.assertEqual(json.load(open(os.path.join(cfg["state_dir"], "ui_status.json"), encoding="utf-8"))["state"],
                         "loaded")
        self.assertEqual((pack.grade, pack.limit), ("A", 500 * refpack.MB))
        self.assertEqual(len(pack.files), 2)
        self.assertEqual(pack.open_image("snippets/%s/gallery/search.png" % ctl.site).size, (160, 100))
        n = len([h for h in ctl.hits if "/refpack/file" in h[1]])
        k.token = None
        client2, pack2 = prepare_online("court_x", {"site": ctl.site}, cfg, k, NOLOG, ui2)   # 같은 버전 → 다시 안 받음
        self.assertEqual(len([h for h in ctl.hits if "/refpack/file" in h[1]]), n)
        rep = client.finish(pack)
        self.assertTrue(pack.released)
        self.assertEqual(pack.files, {})
        with self.assertRaises(RuntimeError):
            pack.get("x")
        self.assertEqual(ctl.reports[-1]["type"], "refpack_prune")
        self.assertEqual(rep["site_id"], ctl.site)

    def test_job_reads_memory_only(self):
        ctl, k, cfg = self.live()
        client, pack = prepare_online("court_x", {"site": ctl.site}, cfg, k, NOLOG, self._ui()[1])
        bank = SnippetBank(cfg["kb_dir"], site=ctl.site, mem=pack)
        scr = Image.new("RGB", (800, 600), "white")
        scr.paste(pack.open_image("snippets/%s/search_btn.png" % ctl.site), (300, 200))
        hit = bank.find(scr, "search_btn", None)
        self.assertEqual((hit[0], hit[1], hit[2]), (360, 214, "search_btn.png"))
        before = sorted(os.listdir(cfg["kb_dir"])) if os.path.isdir(cfg["kb_dir"]) else []
        bank.learn(_noise(9, (800, 600)), "next_btn", 400, 300)
        self.assertIn("snippets/%s/next_btn.png" % ctl.site, pack.new)                 # 메모리에만
        self.assertFalse(os.path.exists(os.path.join(cfg["kb_dir"], "snippets", ctl.site, "next_btn.png")))
        client.finish(pack)
        self.assertEqual([u["path"] for u in ctl.uploads], ["snippets/%s/next_btn.png" % ctl.site])   # 한도 안 → 올림

    # ---- 정리 순서 ----
    def test_prune_duplicates_first_then_old_protects(self):
        base = refpack.dhash(_noise(1))
        near = base ^ 0b11                                                             # 거리 2 = 유사
        far = refpack.dhash(_noise(50))
        es = [
            {"path": "g/rep.png", "bytes": 100, "kind": "gallery", "step": "s", "ts": 1, "representative": True, "phash": base},
            {"path": "g/dup.png", "bytes": 100, "kind": "gallery", "step": "s", "ts": 9, "phash": near},
            {"path": "g/old.png", "bytes": 100, "kind": "gallery", "step": "t", "ts": 2, "phash": far},
            {"path": "g/new.png", "bytes": 100, "kind": "gallery", "step": "u", "ts": 8, "phash": far ^ (2 ** 40 - 1)},
            {"path": "s/top.png", "bytes": 100, "kind": "snippet", "step": "x", "ts": 0, "success_rate": 0.99},
            {"path": "s/low.png", "bytes": 100, "kind": "snippet", "step": "y", "ts": 3, "success_rate": 0.1},
        ]
        keep, removed, prot = refpack.plan_prune(es, 400, dup_dist=6, protect_top_pct=50)
        self.assertEqual([(r["path"], r["reason"]) for r in removed], [("g/dup.png", "duplicate"), ("g/old.png", "old")])
        self.assertEqual(removed[0]["near"], "g/rep.png")
        self.assertIn("g/rep.png", keep)
        self.assertIn("s/top.png", keep)                 # 성공률 상위 보호(가장 오래됐어도)
        rep = refpack.prune_report("site", "D", 400, es, removed, prot)
        self.assertEqual((rep["before_bytes"], rep["after_bytes"], rep["over_limit_after"]), (600, 400, False))
        keep2, removed2, _p = refpack.plan_prune(es, 10_000)
        self.assertEqual(removed2, [])                   # 한도 안이면 정리 안 함

    def test_upload_never_exceeds_grade(self):
        ctl = FakeControl(site="simple.example", grade="D")
        ctl, k, cfg = self.live(ctl)
        client, pack = prepare_online("map_x", {"site": ctl.site}, cfg, k, NOLOG, self._ui()[1])
        for i in range(12):                                                            # 큰 새 화보를 잔뜩
            pack.put("snippets/%s/gallery/s%d.png" % (ctl.site, i), os.urandom(150_000), kind="gallery",
                     step="s%d" % i, phash=i)
        rep = client.finish(pack)
        up = sum(len(__import__("base64").b64decode(u["data_b64"])) for u in ctl.uploads)
        self.assertLessEqual(rep["after_bytes"], refpack.MB)
        self.assertGreater(rep["removed_count"], 0)
        self.assertLessEqual(up, refpack.MB)
        self.assertEqual(ctl.reports[-1]["removed_count"], rep["removed_count"])       # 보고 JSON 에 정리 결과
        self.assertTrue(all(r["reason"] in ("duplicate", "old") for r in rep["removed"]))

    # ---- 화보 ----
    def test_gallery_masking_forbidden_and_dedupe(self):
        import tempfile
        field = (300, 200, 420, 230)
        bank = SnippetBank(tempfile.mkdtemp(prefix="sc_g_"), site="s", mask_fn=lambda _im: [field])
        im = _noise(3, (800, 600))
        fn = bank.save_gallery(im, "login_page")
        g = Image.open(bank.gallery_files("login_page")[0]).convert("RGB")
        self.assertEqual(g.size, (800, 600))                                           # 전체 화면 크기 등급
        self.assertEqual(g.getpixel((350, 210)), (128, 128, 128))                      # ID/PW/캡차 칸 가림
        self.assertEqual(g.getpixel((10, 10)), im.getpixel((10, 10)))
        self.assertTrue(bank.meta["gallery"][fn]["representative"])
        self.assertIsNone(bank.save_gallery(im, "login_page"))                         # 거의 같은 화보는 다시 안 저장
        self.assertIsNone(bank.save_gallery(_noise(4, (800, 600)), "cert", forbidden=True))
        self.assertEqual(bank.gallery_files("cert"), [])
        self.assertEqual(len(bank.forbidden_files("gallery_cert")), 1)                 # 금지 그림은 따로
        self.assertLessEqual(bank.gallery_distance(im, "login_page"), 4)

    def test_runner_gallery_skips_unlocated_login_fields(self):
        r, hw, logs, cfg, c = T09Runner.mk(self, court_spec(steps=[]),
                                           [screen(NORMAL + [("아이디", 900, 400), ("비밀번호", 900, 450)])])
        self.assertIsNone(r._gallery("login_page"))
        self.assertTrue(any(l["ev"] == "gallery_skip" for l in logs))
        r2, hw2, logs2, cfg2, c2 = T09Runner.mk(self, court_spec(steps=[]), [screen(NORMAL)])
        self.assertEqual(r2._gallery("home"), "home.png")

    def test_unsafe_manifest_paths(self):
        for p in ("../x.png", "/abs.png", "sites/run.py", "snippets/a.exe", "snippets\\\\a.png"):
            self.assertFalse(refpack._safe(p), p)
        self.assertTrue(refpack._safe("snippets/s/gallery/a.png"))



# ---------------- v1.6 검수 수정(E1~E5·P7) ----------------
from standalone import court_select as CS


class _WordSim:
    """실제 PicoHW.ocr_words 처럼 줄 전체 + 단어를 함께 돌려주고, 클릭은 좌표(y)로 줄을 고른다."""
    def __init__(self, courts, window=4, list_blind=False, misclick=0):
        self.courts, self.window, self.list_blind, self.misclick = list(courts), window, list_blind, misclick
        self.scroll = self.sel = 0
        self.clicks, self.keys, self.wheels = [], [], 0

    @staticmethod
    def row(name, x, y):
        out = [(name, x, y)]
        ws = name.split()
        if len(ws) > 1:
            out += [(w, x - 40 + 60 * i, y) for i, w in enumerate(ws)]
        return out

    def grab(self):
        return object()

    def lines(self, im, box):
        if box[3] - box[1] > 500:
            if self.list_blind:
                return []
            r = []
            for i, n in enumerate(self.courts[self.scroll:self.scroll + self.window]):
                r += self.row(n, 800, 340 + 30 * i)
            return r
        return self.row(self.courts[self.sel], 900, 380)

    def click(self, x, y, note):
        self.clicks.append((x, y, note))
        if 320 <= y <= 340 + 30 * self.window:
            self.sel = max(0, min(self.scroll + int(round((y - 340) / 30.0)) + self.misclick, len(self.courts) - 1))

    def wheel(self, x, y, t, note):
        self.wheels += 1
        self.scroll = min(self.scroll + 1, max(0, len(self.courts) - self.window))

    def move(self, x, y):
        pass

    def key(self, k, note):
        self.keys.append(k)
        if k == "DOWN":
            self.sel = min(self.sel + 1, len(self.courts) - 1)

    def pick(self, court, **kw):
        return CS.pick_court(court, self.grab, self.lines, self.click, self.wheel, self.move, self.key,
                             sleep=lambda s: None, **kw)


DJ = ["대전지방법원 천안지원", "대전지방법원 홍성지원", "대전지방법원 공주지원", "대전지방법원 논산지원",
      "대전지방법원 서산지원", "대전지방법원"]


class T23ReviewFixes(unittest.TestCase):
    def test_e1_word_and_line_ocr_picks_exact_court(self):
        s = _WordSim(DJ + ["부산지방법원"])
        self.assertEqual(s.pick("대전지방법원")["status"], "OK")
        self.assertEqual(s.courts[s.sel], "대전지방법원")
        self.assertIsNone(CS.find_exact(_WordSim.row("대전지방법원 천안지원", 800, 340), "대전지방법원"))

    def test_e1_branch_only_not_found_and_confirm_equal(self):
        s = _WordSim(DJ[:5] + ["부산지방법원"])
        self.assertEqual(s.pick("대전지방법원", max_wheel=8, max_key=12)["status"], "COURT_NOT_FOUND")
        self.assertNotIn("ENTER", s.keys)
        m = _WordSim(["부산지방법원", "대전지방법원", "대전지방법원 천안지원"], misclick=1)
        self.assertEqual(m.pick("대전지방법원")["status"], "SELECT_NOT_CONFIRMED")   # 칸이 천안지원이면 OK 아님

    def test_e1_key_path_exact_only(self):
        s = _WordSim(DJ + ["부산지방법원"], list_blind=True)
        r = s.pick("대전지방법원", max_wheel=3, max_key=20)
        self.assertEqual((r["status"], s.courts[s.sel], s.keys.count("ENTER")), ("OK", "대전지방법원", 1))

    def test_e2_metro_target_refused_without_input(self):
        s = _WordSim(["서울중앙지방법원", "서울동부지방법원", "대전지방법원"], list_blind=True)
        self.assertEqual(s.pick("서울동부지방법원", max_wheel=3, max_key=10)["status"], "COURT_NOT_FOUND")
        self.assertEqual((s.clicks, s.keys), ([], []))
        a = _WordSim(["춘천지방법원", "대전지방법원"], list_blind=True)
        self.assertEqual(a.pick("춘천지방법원", avoid=("춘천",), max_key=5)["status"], "COURT_NOT_FOUND")

    def test_e3_head_redirect_not_followed_and_policy_checked(self):
        hits = {"n": 0}

        class H(BaseHTTPRequestHandler):
            def do_HEAD(self_):
                hits["n"] += 1
                self_.send_response(302)
                self_.send_header("Location", "http://www.courtauction.go.kr/x")
                self_.end_headers()

            def log_message(self_, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        spec = {"site": "http://127.0.0.1:%d/" % srv.server_address[1], "family": "map", "job": "map_x",
                "site_alive_check": True, "steps": []}
        r, hw, logs, cfg, c = T09Runner.mk(self, spec, [screen(NORMAL)])
        import socket as _so
        looked = []
        real = _so.getaddrinfo

        def spy(host, *a, **k):
            looked.append(host)
            return real(host, *a, **k)
        _so.getaddrinfo = spy
        try:
            self.assertTrue(r._site_head()())
        finally:
            _so.getaddrinfo = real
        self.assertEqual(hits["n"], 1)
        self.assertFalse([h for h in looked if "courtauction" in str(h)])         # 법원 호스트 조회 0
        recs = r.refusals.read()
        self.assertEqual((recs[-1]["host"], recs[-1]["actor"]), ("www.courtauction.go.kr", "POLICY"))

    def test_e5_mask_only_on_screen_where_found(self):
        r, hw, logs, cfg, c = T09Runner.mk(self, court_spec(steps=[]), [screen(NORMAL)])
        login_im = _noise(11, (800, 600))
        r._set_input_box("login_id", login_im, (10, 10, 50, 30))
        self.assertEqual(r._mask_boxes(login_im), [(10, 10, 50, 30)])
        self.assertEqual(r._mask_boxes(_noise(12, (800, 600))), [])               # 다른 화면에서는 안 가림

    def test_p7_deepseek_url_pinned_and_local_llm_localhost_only(self):
        from standalone.ai_helper import AIHelper
        sent = []
        ai = AIHelper(FakeSecrets({"DEEPSEEK_API_KEY": "k1"}), {"deepseek_url": "https://evil.example/chat",
                                                               "deepseek_model": "m", "gemini_model": "g",
                                                               "local_llm_url": "http://10.0.0.5:11434"},
                      post=lambda *a, **k: sent.append(a) or {})
        self.assertFalse(ai.deepseek_ok)
        self.assertFalse(ai.local_ok)
        self.assertIsNone(ai.pick("x", [("a", 1, 1)], order=["local", "deepseek"]))
        self.assertEqual(sent, [])                                                # 아무 데도 안 보냄


# ---------------- v1.6 t845u: 적응 대기·판단 담당표·시간 예산 ----------------
from standalone.adaptive_wait import ReactionStats, AdaptiveWait, complexity
from standalone.decision_routing import DecisionRouter, LADDER_STANDALONE, LADDER_POPULAR
from standalone.time_budget import TimeBudget


class T24Adaptive(unittest.TestCase):
    def test_complexity_score(self):
        self.assertLess(complexity(5, 0, 0), complexity(200, 30, 20))
        self.assertEqual(complexity(1000, 1000, 1000), 1.0)

    def test_complex_waits_longer_simple_faster_after_samples(self):
        st = ReactionStats()
        for _ in range(6):
            st.add("after_btn", 1.0)
        aw = AdaptiveWait(st, "map", rand=lambda a, b: (a + b) / 2)
        simple, i1 = aw.plan("after_btn", 2.0, 2.0, 0.0)
        hard, i2 = aw.plan("after_btn", 2.0, 2.0, 1.0)
        self.assertLess(simple, 2.0)                       # 실제 반응 1초 + 단순 → 빨리
        self.assertGreater(hard, simple)
        self.assertFalse(i1["conservative"])
        self.assertLess(i1["adj"], 0)

    def test_few_samples_conservative_never_shorter(self):
        st = ReactionStats()
        st.add("k", 0.1)
        aw = AdaptiveWait(st, "map", rand=lambda a, b: a)
        sec, info = aw.plan("k", 2.0, 2.0, 0.0)
        self.assertTrue(info["conservative"])
        self.assertGreaterEqual(sec, 2.0)
        st.add("slow", 5.0)
        self.assertGreaterEqual(aw.plan("slow", 2.0, 2.0, 0.0)[0], 5.0)   # 느린 표본은 늘림

    def test_floor_court_and_429_mult(self):
        st = ReactionStats()
        for _ in range(10):
            st.add("k", 0.01)
        self.assertGreaterEqual(AdaptiveWait(st, "court", rand=lambda a, b: a).plan("k", 0.2, 0.2, 0.0)[0], 1.0)
        self.assertGreaterEqual(AdaptiveWait(st, "court", gap_mult=2.0, rand=lambda a, b: a).plan("k", 0.2, 0.2, 0)[0], 2.0)
        self.assertGreaterEqual(AdaptiveWait(st, "map", rand=lambda a, b: a).plan("k", 0.2, 0.2, 0.0)[0], 0.3)

    def test_ewma_and_quantile_update_each_visit(self):
        st = ReactionStats()
        for v in (1, 1, 1, 1, 10):
            st.add("k", v)
        est, n = st.estimate("k")
        self.assertEqual(n, 5)
        self.assertGreater(est, 1.0)                        # 늦은 표본 반영(보수적)

    def test_runner_wait_after_and_reaction_record(self):
        r, hw, logs, cfg, c = T09Runner.mk(self, court_spec(steps=[]), [screen(NORMAL)])
        t0 = c.slept
        r.wait_after("after_x", 1.5, 2.5, im=hw.grab())
        self.assertGreaterEqual(c.slept - t0, 1.0)          # 법원 바닥 1초 이상
        self.assertTrue(any(l["ev"] == "adaptive_wait" for l in logs))

    def test_screen_state_caps_still_increase_only(self):
        from standalone.screen_state import merge_caps, DEFAULTS
        self.assertEqual(merge_caps({"load_max_sec": 5})["load_max_sec"], DEFAULTS["load_max_sec"])


class T24Routing(unittest.TestCase):
    def test_new_event_climbs_and_records_owner(self):
        d = DecisionRouter()
        self.assertEqual(d.plan("locate:x"), list(LADDER_STANDALONE))
        d.record("locate:x", "GEMINI", 4.0, ["PROGRAM", "DEEPSEEK"])
        self.assertEqual(d.owner("locate:x"), "GEMINI")
        self.assertEqual(d.plan("locate:x"), ["PROGRAM", "GEMINI"])          # 하위 모델 건너뜀

    def test_promote_after_n_fails_and_demote_on_program_streak(self):
        d = DecisionRouter(promote_after=2, demote_after=3)
        d.record("e", "DEEPSEEK", 1.0, ["PROGRAM"])
        self.assertEqual(d.owner("e"), "DEEPSEEK")
        d.record("e", None, 1.0, ["PROGRAM", "DEEPSEEK"])
        d.record("e", None, 1.0, ["PROGRAM", "DEEPSEEK"])
        self.assertEqual(d.owner("e"), "GEMINI")                              # 2번 실패 → 승격
        for _ in range(3):
            d.record("e", "PROGRAM", 0.1)
        self.assertEqual(d.owner("e"), "DEEPSEEK")                            # 프로그램 3연속 → 강등

    def test_popular_ladder_ends_with_human(self):
        d = DecisionRouter(ladder=LADDER_POPULAR)
        self.assertEqual(d.plan("q")[-1], "HUMAN")
        self.assertNotIn("HUMAN", DecisionRouter().plan("q"))                 # 단독판은 기존 순서(사람 없음)

    def test_export_has_no_secrets(self):
        d = DecisionRouter()
        d.record("locate:btn", "PROGRAM", 0.1)
        self.assertIsNone(d.record(LEAK, "PROGRAM", 0.1))
        self.assertIsNone(d.record("password=123", "PROGRAM", 0.1))
        self.assertEqual(list(d.export()), ["locate:btn"])

    def test_locator_skips_lower_models_for_gemini_owned_event(self):
        from standalone.locator import Locator
        calls = []

        class AI:
            def pick(self, goal, words, im=None, order=None, tried=None):
                calls.append(list(order))
                tried.extend(order[:1])
                return (words[0][0], words[0][1], words[0][2], order[0])
        d = DecisionRouter()
        d.t["locate:btn"] = {"owner": "GEMINI", "n": 3, "fails": {}, "prog_streak": 0, "solved": {}, "sec_sum": 0}
        loc = Locator(None, lambda im, b: [("다른글자", 10, 10)], AI(), router=d, budget=TimeBudget(Clock().now))
        r = loc.find(Image.new("RGB", (100, 100)), "btn", words=["없음"])
        self.assertEqual(calls, [["gemini"]])
        self.assertEqual(r[2], "ai:gemini")

    def test_deepseek_once_per_decision(self):
        from standalone.ai_helper import AIHelper
        n = {"ds": 0}

        def post(url, body, headers, **k):
            n["ds"] += 1
            return {"choices": [{"message": {"content": "-1"}}]}
        ai = AIHelper(FakeSecrets({"DEEPSEEK_API_KEY": "k"}), {"deepseek_url": "https://api.deepseek.com/chat/completions",
                                                               "deepseek_model": "m", "gemini_model": "g", "max_rounds": 2},
                      post=post)
        ai.pick("x", [("a", 1, 1)], order=["deepseek", "deepseek", "deepseek"])
        self.assertEqual(n["ds"], 1)


class T24Budget(unittest.TestCase):
    def test_slack_reduced_only_after_deficit_and_not_below_floor(self):
        c = Clock()
        b = TimeBudget(c.now, deficit_sec=10, slack_factor=0.5)
        self.assertEqual(b.slack_wait(4.0, 1.0), 4.0)
        b.add_decision("GEMINI", 30)
        self.assertEqual(b.slack_wait(4.0, 1.0), 2.0)
        self.assertEqual(b.slack_wait(1.2, 1.0), 1.0)
        rep = b.report()
        self.assertEqual((rep["decision_sec"], rep["saved_sec"]), (30.0, 2.2))
        self.assertEqual(rep["by_owner"]["GEMINI"], {"n": 1, "sec": 30.0})

    def test_safety_gaps_untouched(self):
        from standalone.page_guard import RateGate
        c = Clock()
        g = RateGate(gap_sec=(60, 60), per_hour=100, per_run=10, now_fn=c.now, sleep_fn=c.sleep, rand_fn=lambda a, b: a)
        b = TimeBudget(c.now, deficit_sec=0)
        b.add_decision("GEMINI", 999)
        g.wait_before_item()
        self.assertEqual(g.wait_before_item(), 60)          # 예산과 무관하게 60초 그대로

    def test_run_report_has_owner_counts_and_saved(self):
        r, hw, logs, cfg, c = T09Runner.mk(self, court_spec(steps=[]), [screen(NORMAL)])
        res = r.run()
        self.assertIn("time_report", res)
        for k in ("run_sec", "decision_sec", "saved_sec", "by_owner", "routing_counts"):
            self.assertIn(k, res["time_report"])
        self.assertTrue(any(l["ev"] == "run_report" for l in logs))


# ---------------- v1.6 t846u: site_pack ----------------
from standalone import site_pack as SP


LEAK = "".join(["s", "k-", "ABCDEFGHIJKL", "MNOPQRSTUVWX"])     # 가짜 키 꼴(조각으로 만듦)


def _mk_kb(tmp, n_snips=3, shots=True, shot_size=(1920, 1080), extra_shots=0):
    kb = os.path.join(tmp, "kb")
    os.makedirs(os.path.join(kb, "snippets", "www.courtauction.go.kr"))
    os.makedirs(os.path.join(kb, "sites"))
    os.makedirs(os.path.join(kb, "errors"))
    os.makedirs(os.path.join(kb, "knowhow"))
    for i in range(n_snips):
        _noise(100 + i, (120, 28)).save(os.path.join(kb, "snippets", "www.courtauction.go.kr", "btn%d.png" % i))
    json.dump({"site": "www.courtauction.go.kr", "steps": [
        {"type": "click", "name": "open_maegak", "el": "btn0", "words": ["매각예정물건"], "box_r": [0.1, 0.1, 0.3, 0.2],
         "expect_words": ["매각예정물건", "사건번호"]},
        {"type": "click", "name": "search", "el": "btn1", "expect_words": ["검색결과"]}],
        "captcha": {"input_box_r": [0.0, 0.0, 0.1, 0.05]}},
        open(os.path.join(kb, "sites", "court_x.json"), "w", encoding="utf-8"), ensure_ascii=False)
    json.dump({"entries": {"E429": {"words": ["요청이 많"], "action": "rest"}}},
              open(os.path.join(kb, "errors", "error_codes.json"), "w", encoding="utf-8"))
    open(os.path.join(kb, "knowhow", "note.md"), "w", encoding="utf-8").write("메뉴는 위쪽")
    open(os.path.join(kb, "knowhow", "leak.json"), "w", encoding="utf-8").write('{"api_key": "%s"}' % LEAK)
    open(os.path.join(kb, "knowhow", "court_password.txt"), "w", encoding="utf-8").write("x")
    sh = os.path.join(tmp, "shots")
    os.makedirs(sh)
    if shots:
        _noise(1, shot_size).save(os.path.join(sh, "open_maegak_20261119_1010.png"))
        _noise(2, shot_size).save(os.path.join(sh, "open_maegak_loading.png"))
        _noise(3, shot_size).save(os.path.join(sh, "search_error.png"))
        _noise(4, shot_size).save(os.path.join(sh, "search_popup.png"))
        _noise(5, shot_size).save(os.path.join(sh, "인증서_login.png"))
        for i in range(extra_shots):                                   # 같은 단계 화보 여러 장(오래된 것·유사)
            _noise(1 if i % 2 else 200 + i, shot_size).save(os.path.join(sh, "open_maegak_2026111%d_0900.png" % i))
    return kb, sh


class T25SitePack(unittest.TestCase):
    def setUp(self):
        import tempfile
        import shutil
        self.tmp = tempfile.mkdtemp(prefix="sc_sp_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def build(self, **kw):
        kb, sh = _mk_kb(self.tmp, **{k: v for k, v in kw.items() if k in ("n_snips", "shots", "shot_size", "extra_shots")})
        out = os.path.join(self.tmp, "pack")
        rep = SP.build(kb, "www.courtauction.go.kr", out, shots_dir=sh, version="v1",
                       **{k: v for k, v in kw.items() if k in ("grade",)})
        return rep, out

    def test_build_format_secrets_sha_encryption(self):
        rep, out = self.build()
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        paths = {f["path"] for f in man["files"]}
        base = "site_pack/www.courtauction.go.kr/"
        for st in ("normal", "loading"):
            for sc in ("full", "2x", "4x", "8x"):
                self.assertIn(base + "gallery/open_maegak/%s_%s.png" % (st, sc), paths)
        self.assertIn(base + "gallery/search/error_full.png", paths)
        self.assertIn(base + "gallery/search/popup_full.png", paths)
        for n in ("steps.json", "errors.json", "decision_routing.json", "rank.json", "timing.json"):
            self.assertIn(base + n, paths)
        self.assertTrue(any(p.startswith("snippets/_forbidden/gallery_") for p in paths))     # 금지 화면 분리
        self.assertEqual(sorted(e["path"] for e in rep["excluded_secret"]),
                         ["knowhow/court_password.txt", "knowhow/leak.json"])
        self.assertTrue(all(f["enc"] == "BGX1" and len(f["sha256"]) == 64 for f in man["files"]))
        from standalone.crypto import is_encrypted
        fp = os.path.join(out, "files", *(base + "steps.json").split("/"))
        self.assertTrue(is_encrypted(open(fp, "rb").read()))
        import subprocess
        r = subprocess.run(["sha256sum", "-c", "--quiet", "SHA256SUMS.txt"], cwd=out, capture_output=True)
        self.assertEqual(r.returncode, 0)
        blob = b"".join(open(os.path.join(d, f), "rb").read() for d, _s, fs in os.walk(out) for f in fs)
        self.assertNotIn(LEAK.encode(), blob)

    def test_steps_json_fields_and_error_routing_link(self):
        rep, out = self.build()
        from standalone.crypto import default_key
        rd = lambda p: json.loads(default_key().decrypt(open(os.path.join(out, "files", *p.split("/")), "rb").read()))  # noqa: E731
        steps = rd("site_pack/www.courtauction.go.kr/steps.json")
        s = steps["open_maegak"]
        for k in ("expect_words", "click_box_r", "next", "react_avg_sec", "react_adj_sec", "complexity"):
            self.assertIn(k, s)
        self.assertEqual(s["next"], "search")
        err = rd("site_pack/www.courtauction.go.kr/errors.json")
        self.assertEqual((err["E429"]["event"], err["E429"]["owner"]), ("error:E429", "PROGRAM"))

    def test_gallery_masks_input_box(self):
        rep, out = self.build()
        from standalone.crypto import default_key
        b = default_key().decrypt(open(os.path.join(out, "files", "site_pack", "www.courtauction.go.kr", "gallery",
                                                   "open_maegak", "normal_full.png"), "rb").read())
        im = Image.open(io.BytesIO(b)).convert("RGB")
        self.assertEqual(im.size, (1920, 1080))
        self.assertEqual(im.getpixel((50, 20)), (128, 128, 128))          # 캡차 입력칸 가림

    def test_over_limit_prunes_with_report(self):
        rep, out = self.build(grade="D", n_snips=3, shot_size=(800, 450), extra_shots=12)
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        self.assertLessEqual(man["used_bytes"], refpack.MB)
        self.assertGreater(rep["prune"]["removed_count"], 0)
        paths = {f["path"] for f in man["files"]}
        self.assertIn("site_pack/www.courtauction.go.kr/steps.json", paths)          # 설명·표는 보호
        self.assertIn("site_pack/www.courtauction.go.kr/gallery/open_maegak/normal_full.png", paths)   # 대표 보호

    def test_load_speed_and_index_lookup(self):
        import time as _t
        from standalone.refpack import MemoryPack
        from standalone.crypto import default_key
        k = default_key()
        files, meta = {}, {}
        blob = os.urandom(8000)
        enc = [k.encrypt(blob) for _ in range(2000)]                       # 2000개 ≈ 16MB
        t0 = _t.perf_counter()
        for i in range(2000):
            p = "site_pack/s/gallery/st%d/normal_full.png" % (i % 200)
            files[p + str(i)] = k.decrypt(enc[i])
            meta[p + str(i)] = {"step": "st%d" % (i % 200), "phash": (i * 2654435761) & (2 ** 64 - 1)}
        files["site_pack/s/steps.json"] = json.dumps({"st5": {"expect_words": ["매각예정물건", "사건번호"]},
                                                      "st6": {"expect_words": ["검색결과"]}}).encode()
        meta["site_pack/s/steps.json"] = {"kind": "steps"}
        pack = MemoryPack("s", "v", "A", 500 * refpack.MB, files, meta)
        idx = SP.SiteIndex(pack)
        load_sec = _t.perf_counter() - t0
        self.assertLess(load_sec, 5.0)
        self.assertEqual(len(idx.step("st5")["files"]), 10)
        self.assertEqual(idx.steps_for_text("매각예정물건 목록 사건번호 2025타경1"), ["st5"])
        h = meta["site_pack/s/gallery/st7/normal_full.png7"]["phash"]
        t1 = _t.perf_counter()
        for _ in range(200):
            res = idx.near(h ^ 0b101, dist=4)
        self.assertLess((_t.perf_counter() - t1) / 200, 0.01)              # 한 번 조회 10ms 아래
        self.assertEqual(res[0], (2, "site_pack/s/gallery/st7/normal_full.png7"))
        sys.stdout.write(" [load2000=%.2fs] " % load_sec)

    def test_build_then_load_through_control_server(self):
        rep, out = self.build()
        man = json.load(open(os.path.join(out, "manifest.json"), encoding="utf-8"))
        ctl = FakeControl()
        ctl.files = {}
        for f in man["files"]:
            ctl.files[f["path"]] = (open(os.path.join(out, "files", *f["path"].split("/")), "rb").read(),
                                    f.get("kind"), f.get("step"), f.get("representative", False))
        ctl.manifest = lambda: man
        srv = ctl.serve()
        self.addCleanup(srv.shutdown)
        k, cfg = T22RefPack.kb(self, "http://127.0.0.1:%d" % srv.server_port)
        client, pack = prepare_online("court_x", {"site": "www.courtauction.go.kr"}, cfg, k, NOLOG, lambda *a, **kw: None)
        self.assertEqual(pack.index.steps_for_text("사건번호 매각예정물건"), ["open_maegak"])
        self.assertTrue(pack.get("site_pack/www.courtauction.go.kr/steps.json").startswith(b"{"))   # 메모리는 평문
        cache = [os.path.join(d, f) for d, _s, fs in os.walk(os.path.join(cfg["state_dir"], "refcache")) for f in fs]
        from standalone.crypto import is_encrypted
        self.assertTrue(cache and all(is_encrypted(open(p, "rb").read()) for p in cache))   # 디스크는 암호문
        client.finish(pack)


# ---------------- v1.6 t850u·t851u: 암호화·키 갱신 ----------------
from standalone import crypto as CR


class T26Crypto(unittest.TestCase):
    def test_roundtrip_tamper_wrong_key(self):
        k = CR.default_key()
        b = k.encrypt(b"hello" * 1000)
        self.assertTrue(CR.is_encrypted(b))
        self.assertEqual(k.decrypt(b), b"hello" * 1000)
        bad = bytearray(b)
        bad[30] ^= 1
        with self.assertRaises(CR.DecryptError):
            k.decrypt(bytes(bad))
        with self.assertRaises(CR.DecryptError):
            CR.DataKey("other-key").decrypt(b)
        self.assertNotEqual(k.encrypt(b"x"), k.encrypt(b"x"))                # 매번 다른 nonce

    def test_default_is_test_key_and_config_rejects_real_key_in_file(self):
        from standalone import config as C
        self.assertTrue(CR.key_from_config(C.DEFAULTS).is_test)
        bad = C._secret_like({"crypto": {"key": "real-key-123"}})
        self.assertEqual(bad, ["crypto.key"])
        self.assertEqual(C._secret_like({"crypto": {"key": CR.TEST_KEY, "key_source": "config"}}), [])

    def test_dpapi_source_uses_data_key(self):
        k = CR.key_from_config({"crypto": {"key_source": "dpapi"}}, FakeSecrets({"DATA_KEY": "new-strong-key"}))
        self.assertFalse(k.is_test)
        self.assertTrue(k.check_upgrade(CR.UPGRADE_AT.timestamp() + 10))

    def test_upgrade_time_stops_with_test_key(self):
        k = CR.default_key()
        self.assertTrue(k.check_upgrade(CR.UPGRADE_AT.timestamp() - 1))     # 11/30 23:59:59 → 아직 됨
        with self.assertRaises(CR.KeyUpgradeRequired):
            k.check_upgrade(CR.UPGRADE_AT.timestamp())                       # 12/1 00:00 KST → 멈춤

    def test_runner_stops_after_upgrade_time(self):
        r, hw, logs, cfg, c = T09Runner.mk(self, court_spec(steps=[]), [screen(NORMAL)])
        c.t = CR.UPGRADE_AT.timestamp() + 60
        res = r.run()
        self.assertEqual(res["end"], "key_upgrade_required")
        self.assertIn(CR.KEY_MARK, open(os.path.join(cfg["stop_dir"], "STOP_court_dongbu"), encoding="utf-8").read())

    def test_app_check_data_key_stops(self):
        from standalone.app import check_data_key
        cfg, d = tmp_cfg()
        self.assertIsNone(check_data_key("court_x", cfg, FakeSecrets(), NOLOG, now=lambda: CR.UPGRADE_AT.timestamp() + 1))
        self.assertIn("key_upgrade_required", open(os.path.join(cfg["stop_dir"], "STOP_court_x"), encoding="utf-8").read())
        self.assertIsNotNone(check_data_key("court_y", cfg, FakeSecrets(), NOLOG, now=lambda: CR.UPGRADE_AT.timestamp() - 1))

    def test_disk_tables_are_encrypted(self):
        import tempfile
        b = SnippetBank(tempfile.mkdtemp(prefix="sc_e_"), site="s")
        b.save()
        self.assertTrue(CR.is_encrypted(open(b.rank_path, "rb").read()))
        open(b.rank_path, "w").write('{"old": {}}')                           # 옛 평문도 읽힘
        self.assertIn("old", SnippetBank(os.path.dirname(b.rank_path), site="s").rank)

    def test_key_upgrade_audit_catches_all_sites(self):
        import subprocess
        import tempfile
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tool = os.path.join(root, "tools", "key_upgrade_audit.py")
        r = subprocess.run([sys.executable, tool, root, "--now", "2026-11-30T23:59:00+09:00"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout[-500:])
        self.assertIn("unmarked=0", r.stdout)
        for must in ("standalone/crypto.py", "standalone/config.py"):
            self.assertIn("MARKED   " + must, r.stdout)
        r2 = subprocess.run([sys.executable, tool, root, "--now", "2026-12-01T00:00:00+09:00"], capture_output=True, text=True)
        self.assertEqual(r2.returncode, 2)                                    # 갱신 시각 뒤 시험 키 남음
        t = tempfile.mkdtemp(prefix="sc_audit_")
        q = "'"
        open(os.path.join(t, "x.py"), "w").write("data_key = " + q + "te" + "st" + q + "\n")
        open(os.path.join(t, "y.py"), "w").write("# KEY-UPGRADE-" + "BEFORE-2026-12-01\n")
        r3 = subprocess.run([sys.executable, tool, t], capture_output=True, text=True)
        self.assertEqual(r3.returncode, 1)
        self.assertIn("UNMARKED x.py:1", r3.stdout)
        self.assertIn("OLD_MARK y.py:1", r3.stdout)



class T10SecretScan(unittest.TestCase):
    def test_scan_package(self):
        pat = re.compile(r"sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY")
        hits = []
        for dp, dn, fn in os.walk(ROOT):
            for f in fn:
                p = os.path.join(dp, f)
                try:
                    if pat.search(open(p, encoding="utf-8", errors="ignore").read()):
                        hits.append(p)
                except OSError:
                    pass
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
