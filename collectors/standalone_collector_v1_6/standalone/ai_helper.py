# -*- coding: utf-8 -*-
"""조각그림·화면글자로 못 찾을 때만 쓰는 AI (문제 9).

순서: 딥시크(화면 글자 목록으로 고르기) → 제미나이(그림). 한 번 막힘에 최대 2라운드.
키는 SecretStore 에서 실행 때 받는다. 키는 요청 머리(Authorization / x-goog-api-key)에만 쓰고
기록에는 남기지 않는다. 무료 표준 라이브러리(urllib)만 쓴다.
"""
from __future__ import annotations

import base64
import io
import json
import re
import urllib.request

PROMPT = ("화면 글자 목록에서 '{goal}' 에 맞는 항목 번호 하나만 답해. 인증서·결제·발급·로그인 버튼(아이디/비밀번호 칸 제외)은 절대 고르지 마. "
          "맞는 게 없으면 -1. 답은 숫자만.")


def _post(url, body, headers, timeout=20, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers=dict({"Content-Type": "application/json"}, **headers), method="POST")
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _num(s):
    m = re.search(r"-?\d+", str(s or ""))
    return int(m.group(0)) if m else -1


DEEPSEEK_HOST = "api.deepseek.com"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _host(url):
    from urllib.parse import urlparse
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


class AIHelper:
    def __init__(self, secrets, cfg_ai, log=lambda *a, **k: None, post=_post, redactor=None):
        self.s, self.cfg, self.log, self.post, self.red = secrets, dict(cfg_ai or {}), log, post, redactor
        self.calls = 0
        # v1.6(검수 P7): 딥시크 주소는 api.deepseek.com(https) 만. 다르면 딥시크를 끈다(키·화면 글자를 보내지 않음).
        du = self.cfg.get("deepseek_url", "")
        self.deepseek_ok = bool(du) and du.startswith("https://") and _host(du) == DEEPSEEK_HOST
        if du and not self.deepseek_ok:
            self.log("ai_url_refused", api="deepseek", host=_host(du))
        # v1.6(t845u): 로컬 LLM(ollama, 무료·선택). 주소가 비었거나 이 PC(127.0.0.1/localhost)가 아니면 건너뜀.
        lu = self.cfg.get("local_llm_url", "") or ""
        self.local_ok = bool(lu) and _host(lu) in LOCAL_HOSTS
        if lu and not self.local_ok:
            self.log("ai_url_refused", api="local", host=_host(lu))

    def _local(self, goal, words):
        if not self.local_ok:
            return None
        words = self._safe_words(words)
        lst = "\n".join("%d: %s" % (i, w[0]) for i, w in enumerate(words[:120]))
        r = self.post(self.cfg["local_llm_url"].rstrip("/") + "/api/generate",
                      {"model": self.cfg.get("local_llm_model", "qwen2.5:3b"), "stream": False,
                       "prompt": PROMPT.format(goal=goal) + "\n" + lst}, {}, timeout=15)
        return _num(r.get("response"))

    def _safe_words(self, words):
        if not self.red:
            return words
        return [(self.red.text(w[0]),) + tuple(w[1:]) for w in words]

    def _deepseek(self, goal, words):
        if not self.deepseek_ok:
            return None
        key = self.s.get("DEEPSEEK_API_KEY", required=False)  # R7: 없으면 AI 건너뜀(묻지 않음)
        if not key:
            return None
        words = self._safe_words(words)
        lst = "\n".join("%d: %s" % (i, w[0]) for i, w in enumerate(words[:120]))
        r = self.post(self.cfg["deepseek_url"], {
            "model": self.cfg["deepseek_model"], "temperature": 0,
            "messages": [{"role": "user", "content": PROMPT.format(goal=goal) + "\n" + lst}]},
            {"Authorization": "Bearer " + key.reveal()})
        return _num(r["choices"][0]["message"]["content"])

    def _gemini(self, goal, words, im):
        key = self.s.get("GEMINI_API_KEY", required=False)  # R7
        if not key:
            return None
        buf = io.BytesIO()
        words = self._safe_words(words)
        im.convert("RGB").resize((max(1, im.width // 2), max(1, im.height // 2))).save(buf, "JPEG", quality=70)
        lst = "\n".join("%d: %s" % (i, w[0]) for i, w in enumerate(words[:120]))
        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % self.cfg["gemini_model"]
        r = self.post(url, {"contents": [{"parts": [
            {"text": PROMPT.format(goal=goal) + "\n" + lst},
            {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode("ascii")}}]}]},
            {"x-goog-api-key": key.reveal()})
        return _num(r["candidates"][0]["content"]["parts"][0]["text"])

    def pick(self, goal, words, im=None, order=None, tried=None):
        """words: [(글자, x, y), ...]. 고른 항목 (글자, x, y, api) 또는 None.
        order: 시도할 api 순서(판단 담당표가 정함, 예 ['gemini']). 없으면 설정 order.
        원격(deepseek/gemini)은 최대 max_rounds 번, 딥시크는 한 번에 1회만. tried 목록에 실제 시도한 api 를 넣어 줌."""
        rounds = 0
        tried = tried if tried is not None else []
        seq = list(order) if order is not None else list(self.cfg.get("order", ["deepseek", "gemini"]))
        for api in dict.fromkeys(seq):
            if api not in ("local", "deepseek", "gemini"):
                self.log("ai_unknown_api", api=str(api)[:20])
                continue
            if api == "local" and not self.local_ok:
                continue
            if api != "local":
                if rounds >= int(self.cfg.get("max_rounds", 2)):
                    break
                rounds += 1
            self.calls += 1
            tried.append(api)
            try:
                if api == "local":
                    i = self._local(goal, words)
                elif api == "deepseek":
                    i = self._deepseek(goal, words)
                else:
                    i = self._gemini(goal, words, im)
            except Exception as e:  # noqa: BLE001
                self.log("ai_error", api=api, err=type(e).__name__)
                continue
            self.log("ai_pick", api=api, goal=goal, idx=i)
            if i is not None and 0 <= i < len(words):
                return tuple(words[i][:3]) + (api,)
        self.log("ai_gave_up", goal=goal, rounds=rounds)
        return None

    def read_digits(self, im_crop, ocr_text=""):
        """숫자 캡차: 제미나이(그림) 1번 → 없으면 딥시크(OCR 글자) 1번. 숫자 문자열 또는 ''. (키는 기록 안 함)"""
        import io as _io
        import re as _re
        q = "그림 속 숫자만 그대로 답해. 숫자 외 글자 금지."
        try:
            key = self.s.get("GEMINI_API_KEY", required=False)  # R7
            if key and im_crop is not None:
                buf = _io.BytesIO()
                im_crop.convert("RGB").save(buf, "PNG")
                url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % self.cfg["gemini_model"]
                r = self.post(url, {"contents": [{"parts": [{"text": q}, {"inline_data": {
                    "mime_type": "image/png", "data": base64.b64encode(buf.getvalue()).decode("ascii")}}]}]},
                    {"x-goog-api-key": key.reveal()})
                d = "".join(_re.findall(r"\d", r["candidates"][0]["content"]["parts"][0]["text"]))
                if 4 <= len(d) <= 8:
                    return d
        except Exception as e:  # noqa: BLE001
            self.log("ai_error", api="gemini", err=type(e).__name__)
        if ocr_text:
            d = "".join(_re.findall(r"\d", ocr_text))
            if 4 <= len(d) <= 8:
                return d
        return ""

    def stall_hint(self, summary):
        """멈춤 진단이 애매할 때(UNKNOWN) 기록 요약만 보고 딥시크에 1회 물어봄. 사이트 주소·비밀값은 안 보냄.
        돌려줌 코드 문자열(예 'ISP_DOWN') 또는 ''. 키 없으면 ''(안 부름)."""
        key = self.s.get("DEEPSEEK_API_KEY", required=False) if self.deepseek_ok else None
        if not key:
            return ""
        q = ("다음은 네트워크 자가진단 결과야. gw_ok(기본 게이트웨이 ping), dns, out(중립 사이트 연결). "
             "SITE_NO_RESPONSE / ISP_DOWN / LOCAL_NET_DOWN 중 하나만 대문자로 답해.\n" + str(summary))
        try:
            r = self.post(self.cfg["deepseek_url"], {
                "model": self.cfg["deepseek_model"], "temperature": 0,
                "messages": [{"role": "user", "content": q}]},
                {"Authorization": "Bearer " + key.reveal()})
            return (r["choices"][0]["message"]["content"] or "").strip().upper()
        except Exception as e:  # noqa: BLE001
            self.log("ai_error", api="deepseek_stall", err=type(e).__name__)
            return ""
