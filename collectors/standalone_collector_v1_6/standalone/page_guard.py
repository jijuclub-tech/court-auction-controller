# -*- coding: utf-8 -*-
"""page_guard (단독 수집기 사본: 표 위치만 collector_kb/errors 로 바꿈) — 화면 오류 코드 확인기 (2026-11-19, 수집기 1호, 한영님 t808u~t810u)

페이지가 바뀔 때마다·클릭 전마다 화면 사진 OCR 글자와 창 제목으로 error_codes.json 을 먼저 확인한다.
문제가 없을 때(None)만 마우스를 움직인다. AI·Playwright·CDP·JS·직접 HTTP 호출 없음. 피코2 동작은 넘겨받은 함수로만 한다.

    from page_guard import check_page, guarded, RateGate, PageError
    hit = check_page(im, ocr_text_fn, window_title=_fg_title(), site="court")
    if hit: code, action = hit   # 예: ("HTTP_429", "RATE_LIMIT")

ocr_text_fn(im, box) -> str  (box=(x1,y1,x2,y2) 화면 좌표. 1호: lambda im, b: ocr(im, b, 1))
"""
import json, os, re, time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(HERE, "error_codes.json")
if not os.path.exists(DEFAULT_JSON):  # 단독 수집기: 표는 DB(collector_kb/errors) 안에 있어 서버에서 새로 받을 수 있다
    DEFAULT_JSON = os.path.join(os.path.dirname(HERE), "collector_kb", "errors", "error_codes.json")

_WS = re.compile(r"\s+")
def _ns(s):
    return _WS.sub("", s or "").lower()

# 사건번호·금액·날짜·전화 속 숫자는 HTTP 코드로 보지 않도록 미리 지운다.
_MASKS = [re.compile(p) for p in (
    r"\d{2,4}\s*타\s*경\s*\d+(?:\s*\(\s*\d+\s*\))?",   # 2025타경52429(1)
    r"\d[\d,\.]*\s*(?:원|만원|억|㎡|m2|평|%|번지|호|층|동)",  # 금액·면적·주소
    r"\d{4}[.\-/년]\s*\d{1,2}[.\-/월]\s*\d{1,2}",         # 날짜
    r"\d{2,4}-\d{3,4}-\d{4}",                               # 전화
)]
_G_HTTP = re.compile(r"HTTP\s*(?:ERROR|STATUS|오류|상태)?\s*[:#]?\s*(\d{3})(?![\d,\.])", re.I)
_G_ERR = re.compile(r"(?:ERROR|오류|에러)\s*(?:CODE|코드)?\s*[:#]?\s*(\d{3})(?![\d,\.])", re.I)
_G_TITLE = re.compile(r"^\s*(\d{3})(?![\d,\.])\s*(?:[-:]\s*)?[A-Za-z]")
_G_CHROME = re.compile(r"(?:net::)?(ERR_[A-Z0-9_]{2,})")


class PageError(Exception):
    def __init__(self, code, action, why=""):
        super().__init__(f"{code}:{action}")
        self.code, self.action, self.why = code, action, why


class PageGuard:
    def __init__(self, path=DEFAULT_JSON):
        self.doc = json.load(open(path, encoding="utf-8"))
        self.actions = self.doc["actions"]
        self.entries = [e for e in self.doc["entries"] if e.get("guard")]
        self.by_code = {e["code"]: e for e in self.doc["entries"]}
        self.http = {}       # 숫자 -> 항목
        self.chrome = {}     # ERR_xxx -> 항목
        self.reason_re = []  # (항목, 숫자+문구 정규식, 문구 단독 정규식|None)
        for e in self.entries:
            d = e["detect"]
            if d.get("http"):
                self.http[int(d["http"])] = e
                flex = r"\s*".join(re.escape(w) for w in d["reason"].split())
                self.reason_re.append((e, re.compile(rf"(?<![\d,\.]){d['http']}\s*[-:]?\s*{flex}", re.I),
                                       re.compile(flex, re.I) if d.get("phrase_alone") else None))
            if d.get("chrome_err"):
                self.chrome[d["chrome_err"]] = e
            d["_any"] = [_ns(w) for w in d.get("ocr_any", [])]
            d["_need"] = [_ns(w) for w in d.get("need_any", [])]
            d["_re"] = [re.compile(r, re.I) for r in d.get("ocr_regex", [])]
            d["_title"] = [w.lower() for w in d.get("title_any", [])]
        self.last = None

    def sev(self, action):
        return self.actions.get(action, {}).get("severity", 0)

    def _ok_site(self, e, site):
        s = e.get("sites") or ["all"]
        return "all" in s or site in s or site is None

    def scan_text(self, text, window_title=None, site="court", ignore=()):
        """글자만으로 검사. 맞은 것 모두 [(code, action, why)] (심각한 순)."""
        raw = text or ""
        masked = raw
        for m in _MASKS:
            masked = m.sub(" ", masked)
        ns = _ns(raw)
        title = (window_title or "").strip()
        hits = []
        def add(e, why):
            if e["code"] in ignore or e["action"] in ignore or not self._ok_site(e, site):
                return
            hits.append((e["code"], e["action"], why))
        # 1) HTTP 숫자 (앞뒤 말과 함께일 때만)
        for g in (_G_HTTP, _G_ERR):
            for m in g.finditer(masked):
                e = self.http.get(int(m.group(1)))
                if e: add(e, "ocr:" + m.group(0)[:40])
        for e, r_num, r_alone in self.reason_re:
            m = r_num.search(masked)
            if m: add(e, "ocr:" + m.group(0)[:40])
            elif r_alone is not None and r_alone.search(raw): add(e, "ocr_phrase:" + e["detect"]["reason"])
            if title and (r_num.search(title) or (r_alone is not None and r_alone.search(title))):
                add(e, "title:" + title[:60])
        if title:
            m = _G_TITLE.match(title) or _G_HTTP.search(title)
            if m and int(m.group(1)) in self.http:
                add(self.http[int(m.group(1))], "title:" + title[:60])
        # 2) 크롬 ERR_*
        up = re.sub(r"\s*_\s*", "_", raw.upper())
        for m in _G_CHROME.finditer(up):
            name = m.group(1).rstrip("_")
            e = self.chrome.get(name)
            if e: add(e, "ocr:" + name)
            elif "CHROME_ERR_UNKNOWN" not in ignore:
                hits.append(("CHROME_ERR_UNKNOWN", "NETWORK_WAIT", "ocr:" + name))
        # 3) 문구·정규식·창 제목
        tl = title.lower()
        for e in self.entries:
            d = e["detect"]
            if d.get("http") and not (d["_any"] or d["_re"] or d["_title"]):
                continue
            if d["_need"] and not any(w in ns for w in d["_need"]):
                continue
            w = next((p for p in d["_any"] if p and p in ns), None)
            if w: add(e, "ocr:" + w); continue
            r = next((x for x in d["_re"] if x.search(raw)), None)
            if r: add(e, "re:" + r.pattern[:40]); continue
            t = next((x for x in d["_title"] if x and x in tl), None)
            if t: add(e, "title:" + title[:60])
        # 겹친 것 정리: 같은 코드 하나만, 심각한 순
        seen, out = set(), []
        for h in sorted(hits, key=lambda h: (-self.sev(h[1]), 0 if h[0].startswith(("CF_", "AKAMAI", "WAF", "NETFUNNEL")) else 1)):  # 같은 심각도면 업체 화면이 먼저
            if h[0] not in seen:
                seen.add(h[0]); out.append(h)
        return out

    def check(self, im, ocr_text_fn, window_title=None, site="court", ignore=(), boxes=None):
        W, H = getattr(im, "size", (1920, 1080))
        if boxes is None:
            boxes = [(0, 0, W, int(H * 0.22)),                                  # 탭 제목·주소줄·페이지 머리
                     (int(W * 0.08), int(H * 0.15), int(W * 0.92), int(H * 0.92))]  # 가운데(알림창·오류 본문)
        text = "\n".join(ocr_text_fn(im, b) or "" for b in boxes)
        hits = self.scan_text(text, window_title, site, ignore)
        self.last = dict(hits=hits, text=text[:2000], title=window_title)
        return (hits[0][0], hits[0][1]) if hits else None

    def plan(self, action):
        """대응 순서(사람이 읽는 단계 + 숫자 값)."""
        return self.actions.get(action, {})


_DEFAULT = None
def _g():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PageGuard()
    return _DEFAULT

def check_page(im, ocr_text_fn, window_title=None, site="court", ignore=(), boxes=None, guard=None):
    """(code, action) 또는 None. None 일 때만 마우스를 움직인다."""
    return (guard or _g()).check(im, ocr_text_fn, window_title, site, ignore, boxes)


def guarded(action_fn, *args, grab_fn, ocr_text_fn, log_fn=None, title_fn=None, site="court", where="",
            on_error=None, ignore=(), guard=None, **kwargs):
    """모든 마우스 동작 전에 부르는 감싸개.
    화면 확인 → 문제가 있으면 action_fn 을 부르지 않고 log {ev:'page_error', code, action} 후
    on_error(code, action) 결과를 돌려주거나(on_error 있음) PageError 를 던진다.
        guarded(clk, 905, y, "item link", grab_fn=grab, ocr_text_fn=lambda im,b: ocr(im,b,1), log_fn=log, title_fn=_fg_title, where="list")
    """
    g = guard or _g()
    im = grab_fn()
    title = None
    if title_fn:
        try: title = title_fn()
        except Exception: title = None
    hit = g.check(im, ocr_text_fn, title, site, ignore)
    if hit:
        code, action = hit
        why = g.last["hits"][0][2] if g.last and g.last["hits"] else ""
        if log_fn:
            log_fn(ev="page_error", code=code, action=action, where=where, why=why, title=(title or "")[:80])
        if on_error:
            return on_error(code, action)
        raise PageError(code, action, why)
    return action_fn(*args, **kwargs)


class RateGate:
    """요청 간격 지키기(물건 사이 최소 초 + 시간당 상한). 429 뒤에는 간격을 배수로 늘린다.
    시계·잠자기는 넘겨받아 시험할 수 있게 함."""
    def __init__(self, gap_sec=(60, 120), per_hour=20, per_run=40, mult=1.0, now_fn=time.time, sleep_fn=time.sleep, rand_fn=None):
        import random
        self.gap, self.per_hour, self.per_run, self.mult = tuple(gap_sec), per_hour, per_run, mult
        self.now, self.sleep, self.rand = now_fn, sleep_fn, rand_fn or random.uniform
        self.stamps, self.count, self.last = [], 0, None

    def on_rate_limit(self, factor=2.0):
        self.mult *= factor
        return self.mult

    def limits(self):
        return (self.gap[0] * self.mult, self.gap[1] * self.mult), max(1, int(self.per_hour / self.mult))

    def wait_before_item(self):
        """다음 물건 클릭 전에 부름. 기다린 초를 돌려줌. 회차 상한이면 None."""
        if self.per_run and self.count >= self.per_run:
            return None
        (lo, hi), ph = self.limits()
        waited = 0.0
        t = self.now()
        if self.last is not None:
            need = self.rand(lo, hi) - (t - self.last)
            if need > 0:
                self.sleep(need); waited += need; t = self.now()
        self.stamps = [s for s in self.stamps if t - s < 3600]
        if len(self.stamps) >= ph:
            need = 3600 - (t - self.stamps[0]) + 1
            if need > 0:
                self.sleep(need); waited += need; t = self.now()
            self.stamps = [s for s in self.stamps if t - s < 3600]
        self.stamps.append(t); self.last = t; self.count += 1
        return waited
