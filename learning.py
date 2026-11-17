"""학습서 (site guide book) for the court-auction controller — S-0.1.2-CONTROL

Python 3 standard library only.

Flow (관리인 지시 2026-11-17)
  1. Collectors send experience records (경험 기록): which screen, what was clicked,
     success/failure, time taken, popups, which AI rung solved it.
     Only "how to find the way" is kept. Typed text, passwords and images are dropped.
  2. The controller adds the records up per route step by itself (no AI, no cost):
     success rate, average time, frequent failures, which rung solved it.
  3. The writer AI (작성 AI, default: Grok bot) turns that into a versioned 학습서:
     per step a hint, wait time, obstacles with responses, and difficulty
     (easy -> start at rung 1 = DeepSeek etc., hard -> start at rung 2 = Gemini).
  4. The reviewer AI (검수 AI, default: ChatGPT) only points out problems; it never edits.
  5. The manager approves: trial on one collector first, then all collectors.
     Any older version can be put back.
  6. When a collector says "I am entering site X" (POST /api/collector/visit),
     the controller answers with the newest 학습서 for that site.

Five AIs can be chosen for writer and reviewer: 글록, 쳇GPT, 제미나이, 클로드, 딥시크.
Grok works through its bot (the bot fetches jobs with X-Bot-Token) or through the xAI API.
The others work through their API keys (environment variable or the manager page).
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from urllib.request import Request, urlopen

AIS = {
    "grok": {"label": "글록", "vendor": "xai", "modes": ["bot", "api"], "models": ["grok-4", "grok-3-mini"],
             "key_env": "XAI_API_KEY"},
    "chatgpt": {"label": "쳇GPT", "vendor": "openai", "modes": ["api"], "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
                "key_env": "OPENAI_API_KEY"},
    "gemini": {"label": "제미나이", "vendor": "gemini", "modes": ["api"],
               "models": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"], "key_env": "GEMINI_API_KEY"},
    "claude": {"label": "클로드", "vendor": "anthropic", "modes": ["api"],
               "models": ["claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"], "key_env": "ANTHROPIC_API_KEY"},
    "deepseek": {"label": "딥시크", "vendor": "deepseek", "modes": ["api"], "models": ["deepseek-chat", "deepseek-reasoner"],
                 "key_env": "DEEPSEEK_API_KEY"},
}
DEFAULT_SETTINGS = {
    "writer": "grok",          # 관리인 지시: 글록 봇이 기본값
    "reviewer": "chatgpt",
    "auto_after": 0,           # 0 = 관리인이 '학습서 만들기'를 누를 때만
    "ai": {k: {"mode": v["modes"][0], "model": v["models"][0]} for k, v in AIS.items()},
}
EXP_FIELDS = ("site_id", "route_id", "step", "screen", "action", "x", "y", "result", "seconds", "level", "ai",
              "popup", "error", "book_version")
RESULTS = ("ok", "fail", "skip")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
EXP_CAP = 20000
JOB_TEXT = {"write_wait": "작성 봇 기다림", "writing": "작성 중", "review_wait": "검수 봇 기다림", "reviewing": "검수 중",
            "ready": "관리인 승인 기다림", "failed": "실패"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS experiences (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, at TEXT NOT NULL, site_id TEXT NOT NULL,
  route_id TEXT NOT NULL DEFAULT '', step INTEGER NOT NULL DEFAULT 0, screen TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL DEFAULT '', x INTEGER, y INTEGER, result TEXT NOT NULL, seconds REAL,
  level INTEGER, ai TEXT NOT NULL DEFAULT '', popup TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  book_version INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS exp_site ON experiences(site_id, id);
CREATE TABLE IF NOT EXISTS learn_jobs (
  id TEXT PRIMARY KEY, site_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  status TEXT NOT NULL, writer TEXT NOT NULL, reviewer TEXT NOT NULL, input TEXT NOT NULL,
  draft TEXT NOT NULL DEFAULT '', review TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
  last_exp_id INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS learn_books (
  site_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, trial_collector TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL, review TEXT NOT NULL DEFAULT '', writer TEXT NOT NULL, reviewer TEXT NOT NULL,
  job_id TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT, PRIMARY KEY (site_id, version));
"""

# bound by controller.bind_learning()
_db = _kv_get = _kv_set = _audit = _now = None
_data_dir = None
_lock = threading.Lock()


def bind(db, kv_get, kv_set, audit, now, data_dir):
    global _db, _kv_get, _kv_set, _audit, _now, _data_dir
    _db, _kv_get, _kv_set, _audit, _now, _data_dir = db, kv_get, kv_set, audit, now, data_dir


def init(con):
    con.executescript(SCHEMA)
    # a job that was running when the controller stopped cannot finish
    con.execute("UPDATE learn_jobs SET status='failed', error='관제기 재시작으로 중단 — 다시 만들기' "
                "WHERE status IN ('writing','reviewing')")


# ---------------------------------------------------------------------------
# settings and keys
def settings(con):
    s = _kv_get(con, "learning")
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    if isinstance(s, dict):
        for k in ("writer", "reviewer"):
            if s.get(k) in AIS:
                out[k] = s[k]
        if isinstance(s.get("auto_after"), int) and 0 <= s["auto_after"] <= 100000:
            out["auto_after"] = s["auto_after"]
        for k, v in (s.get("ai") or {}).items():
            if k in AIS and isinstance(v, dict):
                if v.get("mode") in AIS[k]["modes"]:
                    out["ai"][k]["mode"] = v["mode"]
                if isinstance(v.get("model"), str) and 0 < len(v["model"]) <= 80:
                    out["ai"][k]["model"] = v["model"]
    return out


def _key_file():
    return _data_dir / "ai_keys.json"


def _keys():
    try:
        return json.loads(_key_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def api_key(ai):
    return _keys().get(ai) or os.environ.get(AIS[ai]["key_env"], "")


def set_key(ai, key):
    with _lock:
        keys = _keys()
        if key:
            keys[ai] = key
        else:
            keys.pop(ai, None)
        tmp = _key_file().with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(keys, f)
        os.replace(tmp, _key_file())


def ai_info():
    return {k: {"label": v["label"], "modes": v["modes"], "models": v["models"], "has_key": bool(api_key(k)),
                "key_env": v["key_env"]} for k, v in AIS.items()}


# ---------------------------------------------------------------------------
# experience records
def _clean(rec):
    """Keep only the way-finding fields. Typed text, passwords and images never get in."""
    if not isinstance(rec, dict):
        raise ValueError("record must be an object")
    site = str(rec.get("site_id") or "")
    if not SAFE_ID.match(site):
        raise ValueError("site_id (letters, digits, _ -) required")
    result = str(rec.get("result") or "")
    if result not in RESULTS:
        raise ValueError("result must be ok, fail or skip")

    def num(k, lo, hi, kind=int):
        try:
            v = kind(rec.get(k))
        except (TypeError, ValueError):
            return None
        return v if lo <= v <= hi else None

    action = str(rec.get("action") or "").upper()[:20]
    return {
        "site_id": site, "route_id": str(rec.get("route_id") or "")[:80], "step": num("step", 0, 999) or 0,
        "screen": str(rec.get("screen") or "")[:120], "action": action,
        "x": num("x", 0, 100000), "y": num("y", 0, 100000), "result": result,
        "seconds": num("seconds", 0, 86400, float), "level": num("level", 0, 4), "ai": str(rec.get("ai") or "")[:40],
        "popup": str(rec.get("popup") or "")[:200], "error": str(rec.get("error") or "")[:200],
        "book_version": num("book_version", 0, 10 ** 6) or 0,
    }


def add_experience(con, name, records):
    if not isinstance(records, list) or not 1 <= len(records) <= 200:
        raise ValueError("records: list of 1..200")
    rows = [_clean(r) for r in records]
    at = _now()
    for r in rows:
        con.execute("INSERT INTO experiences(name,at,site_id,route_id,step,screen,action,x,y,result,seconds,level,ai,"
                    "popup,error,book_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, at, r["site_id"], r["route_id"], r["step"], r["screen"], r["action"], r["x"], r["y"],
                     r["result"], r["seconds"], r["level"], r["ai"], r["popup"], r["error"], r["book_version"]))
    con.execute("DELETE FROM experiences WHERE id NOT IN (SELECT id FROM experiences ORDER BY id DESC LIMIT ?)", (EXP_CAP,))
    return sorted({r["site_id"] for r in rows})


def stats(con, site_id, since_id=0):
    """Plain counting per route step — done by the controller itself, no AI."""
    out = {}
    for r in con.execute("SELECT * FROM experiences WHERE site_id=? AND id>? ORDER BY id", (site_id, since_id)):
        k = "%s#%s" % (r["route_id"], r["step"])
        s = out.setdefault(k, {"route_id": r["route_id"], "step": r["step"], "n": 0, "ok": 0, "fail": 0, "seconds": [],
                               "screens": {}, "levels_ok": {}, "problems": {}, "actions_ok": {}})
        s["n"] += 1
        if r["result"] == "ok":
            s["ok"] += 1
            if r["level"] is not None:
                s["levels_ok"][str(r["level"])] = s["levels_ok"].get(str(r["level"]), 0) + 1
            if r["action"]:
                a = "%s %s,%s" % (r["action"], r["x"], r["y"]) if r["x"] is not None else r["action"]
                s["actions_ok"][a] = s["actions_ok"].get(a, 0) + 1
        elif r["result"] == "fail":
            s["fail"] += 1
            p = (r["error"] or r["popup"] or "원인 기록 없음")[:120]
            s["problems"][p] = s["problems"].get(p, 0) + 1
        if r["seconds"] is not None:
            s["seconds"].append(r["seconds"])
        if r["screen"]:
            s["screens"][r["screen"]] = s["screens"].get(r["screen"], 0) + 1
    res = []
    for s in out.values():
        secs = s.pop("seconds")
        s["avg_seconds"] = round(sum(secs) / len(secs), 1) if secs else None
        s["success_rate"] = round(s["ok"] / s["n"], 3) if s["n"] else None
        for k in ("screens", "levels_ok", "problems", "actions_ok"):
            s[k] = dict(sorted(s[k].items(), key=lambda kv: -kv[1])[:8])
        lv1 = s["levels_ok"].get("1", 0)
        if s["n"] >= 5:
            s["suggested"] = "easy" if s["ok"] and lv1 / s["n"] >= 0.8 else "hard"
        else:
            s["suggested"] = "unknown"
        res.append(s)
    return sorted(res, key=lambda s: (s["route_id"], s["step"]))


def book_results(con, site_id):
    """Success rate of records that were made while each 학습서 version was in use."""
    return {str(r["book_version"]): {"n": r["n"], "ok": r["ok"]} for r in con.execute(
        "SELECT book_version, COUNT(*) n, SUM(result='ok') ok FROM experiences WHERE site_id=? GROUP BY book_version",
        (site_id,))}


# ---------------------------------------------------------------------------
# prompts
WRITE_PROMPT = """너는 법원경매 수집기들의 '학습서'를 쓰는 작성 AI다.
아래 자료는 여러 수집기가 실제로 다녀온 기록을 관제기가 단계별로 센 것이다.
이것을 다음 수집 때 수집기가 그대로 따를 작업 지침으로 정리하라.

지킬 것
- 기록에 있는 사실만 쓴다. 기록에 없는 화면·단추를 지어내지 않는다.
- 단계마다 difficulty를 정한다: 기록상 1단계 AI로 잘 풀리면 "easy"(딥시크 등 1단계부터), 단추 고르기·화면 판단이 필요하면 "hard"(제미나이 등 2단계부터).
- 실패가 많은 곳은 obstacles에 '알아보는 표시(sign)'와 '대처(response)'를 순서대로 적는다.
- 아이디·암호 같은 값은 쓰지 않는다. 보안문자·로그인 화면 처리는 기존 단계 규칙(관리인 단계)을 따른다고 적는다.
- previous_book이 있으면 그것을 고쳐 쓰고, 바뀐 점을 changes에 적는다. previous_review가 있으면 지적된 문제를 반영한다.

JSON 하나로만 답한다:
{"summary":"한두 문장","changes":["바뀐 점"],"steps":[{"route_id":"","step":1,"screen":"화면 이름","difficulty":"easy|hard",
"hint":"이 단계에서 할 일과 요령","wait_seconds":0,"obstacles":[{"sign":"알아보는 표시","response":"대처"}]}]}
"""

REVIEW_PROMPT = """너는 학습서 검수 AI다. 아래 '자료'와 작성 AI가 쓴 '초안'을 비교하라.
초안을 고쳐 쓰지 말고, 문제만 지적하라.
- 기록에 없는 내용을 지어냈는지
- 실패가 많은 단계의 대처가 빠졌는지
- difficulty(easy/hard)가 기록의 성공률과 맞는지
- 대기 시간이 기록보다 지나치게 짧거나 긴지
- 아이디·암호 같은 값이 들어갔는지

JSON 하나로만 답한다:
{"verdict":"pass|fix","problems":[{"route_id":"","step":1,"problem":"무엇이 문제인지"}],"note":"한두 문장"}
"""


def _extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        return json.loads(text[a:b + 1])
    raise ValueError("AI 답이 JSON이 아님")


def _post(url, body, headers, timeout=180):
    req = Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers},
                  method="POST")
    return json.loads(urlopen(req, timeout=timeout).read())


def call_ai(ai, model, system, user):
    """One request to the chosen AI's API. Returns a dict parsed from the AI's JSON answer."""
    key = api_key(ai)
    if not key:
        raise RuntimeError("%s API 키 없음" % AIS[ai]["label"])
    vendor = AIS[ai]["vendor"]
    if vendor in ("openai", "deepseek", "xai"):
        url = {"openai": "https://api.openai.com/v1/chat/completions",
               "deepseek": "https://api.deepseek.com/chat/completions",
               "xai": "https://api.x.ai/v1/chat/completions"}[vendor]
        body = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if model == "deepseek-reasoner":
            body.pop("temperature")
            body.pop("response_format")
        d = _post(url, body, {"Authorization": "Bearer " + key})
        return _extract_json(d["choices"][0]["message"]["content"])
    if vendor == "anthropic":
        d = _post("https://api.anthropic.com/v1/messages",
                  {"model": model, "max_tokens": 8000, "temperature": 0, "system": system,
                   "messages": [{"role": "user", "content": user}]},
                  {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return _extract_json("".join(c.get("text", "") for c in d.get("content", [])))
    if vendor == "gemini":
        d = _post("https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model,
                  {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"parts": [{"text": user}]}],
                   "generationConfig": {"responseMimeType": "application/json", "temperature": 0}},
                  {"x-goog-api-key": key})
        parts = ((d.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        return _extract_json("".join(p.get("text", "") for p in parts))
    raise RuntimeError("unknown vendor")


# ---------------------------------------------------------------------------
# draft / review checks
def clean_draft(d):
    if not isinstance(d, dict) or not isinstance(d.get("steps"), list):
        raise ValueError("초안에 steps 목록이 없음")
    steps = []
    for s in d["steps"][:200]:
        if not isinstance(s, dict):
            continue
        diff = s.get("difficulty") if s.get("difficulty") in ("easy", "hard") else "hard"
        try:
            wait = max(0, min(600, int(float(s.get("wait_seconds") or 0))))
        except (TypeError, ValueError):
            wait = 0
        try:
            step_no = int(s.get("step") or 0)
        except (TypeError, ValueError):
            step_no = 0
        obs = [{"sign": str(o.get("sign") or "")[:200], "response": str(o.get("response") or "")[:300]}
               for o in (s.get("obstacles") or [])[:10] if isinstance(o, dict)]
        steps.append({"route_id": str(s.get("route_id") or "")[:80], "step": step_no,
                      "screen": str(s.get("screen") or "")[:120], "difficulty": diff,
                      "start_level": 1 if diff == "easy" else 2,
                      "hint": str(s.get("hint") or "")[:1000], "wait_seconds": wait, "obstacles": obs})
    if not steps:
        raise ValueError("초안 단계가 비어 있음")
    return {"summary": str(d.get("summary") or "")[:500],
            "changes": [str(c)[:300] for c in (d.get("changes") or [])[:30]], "steps": steps}


def clean_review(r):
    if not isinstance(r, dict):
        raise ValueError("검수 답이 객체가 아님")
    return {"verdict": "pass" if r.get("verdict") == "pass" else "fix",
            "problems": [{"route_id": str(p.get("route_id") or "")[:80], "step": p.get("step"),
                          "problem": str(p.get("problem") or "")[:400]}
                         for p in (r.get("problems") or [])[:40] if isinstance(p, dict)],
            "note": str(r.get("note") or "")[:500]}


# ---------------------------------------------------------------------------
# jobs
def build_input(con, site_id):
    sites = _kv_get(con, "sites") or []
    routes = [r for r in (_kv_get(con, "routes") or []) if r.get("site_id") == site_id]
    live = con.execute("SELECT body FROM learn_books WHERE site_id=? AND status='live'", (site_id,)).fetchone()
    last = con.execute("SELECT review FROM learn_books WHERE site_id=? ORDER BY version DESC LIMIT 1", (site_id,)).fetchone()
    fails = [dict(r) for r in con.execute(
        "SELECT route_id, step, screen, action, level, ai, popup, error, seconds FROM experiences "
        "WHERE site_id=? AND result='fail' ORDER BY id DESC LIMIT 40", (site_id,))]
    return {"site": next((s for s in sites if s.get("site_id") == site_id), {"site_id": site_id}),
            "routes": routes, "step_stats": stats(con, site_id), "recent_failures": fails,
            "previous_book": json.loads(live["body"]) if live else None,
            "previous_review": json.loads(last["review"]) if last and last["review"] else None}


def _stage_status(st, role):
    """Which status a job gets when it reaches the writer or reviewer."""
    ai = st[role]
    if st["ai"][ai]["mode"] == "bot":
        return "write_wait" if role == "writer" else "review_wait"
    return "writing" if role == "writer" else "reviewing"


def start_job(con, site_id, who="manager"):
    if not SAFE_ID.match(site_id or ""):
        raise ValueError("site_id required")
    if con.execute("SELECT 1 FROM learn_jobs WHERE site_id=? AND status IN ('write_wait','writing','review_wait','reviewing')",
                   (site_id,)).fetchone():
        raise ValueError("이 사이트 학습서를 이미 만드는 중")
    if not con.execute("SELECT 1 FROM experiences WHERE site_id=? LIMIT 1", (site_id,)).fetchone():
        raise ValueError("이 사이트 경험 기록이 아직 없음")
    st = settings(con)
    jid = uuid.uuid4().hex[:16]
    last_id = con.execute("SELECT COALESCE(MAX(id),0) FROM experiences WHERE site_id=?", (site_id,)).fetchone()[0]
    status = _stage_status(st, "writer")
    con.execute("INSERT INTO learn_jobs(id,site_id,created_at,updated_at,status,writer,reviewer,input,last_exp_id) "
                "VALUES(?,?,?,?,?,?,?,?,?)", (jid, site_id, _now(), _now(), status, st["writer"], st["reviewer"],
                                              json.dumps(build_input(con, site_id), ensure_ascii=False), last_id))
    _audit(con, who, "학습서 만들기 %s %s (작성 %s, 검수 %s)" % (site_id, jid, st["writer"], st["reviewer"]))
    return jid


def run_if_api(jid):
    """Call after the transaction that created the job has been committed."""
    with _db() as con:
        j = _job(con, jid)
    if j is not None and j["status"] in ("writing", "reviewing"):
        _spawn(jid)


def maybe_auto(con, site_ids):
    st = settings(con)
    if not st["auto_after"]:
        return []
    started = []
    for site in site_ids:
        last = con.execute("SELECT COALESCE(MAX(last_exp_id),0) FROM learn_jobs WHERE site_id=?", (site,)).fetchone()[0]
        n = con.execute("SELECT COUNT(*) FROM experiences WHERE site_id=? AND id>?", (site, last)).fetchone()[0]
        if n >= st["auto_after"]:
            try:
                started.append(start_job(con, site, who="auto"))
            except ValueError:
                pass
    return started


def _spawn(jid):
    threading.Thread(target=_run, args=(jid,), daemon=True, name="learn-" + jid).start()


def _job(con, jid):
    return con.execute("SELECT * FROM learn_jobs WHERE id=?", (jid,)).fetchone()


def _run(jid):
    """Runs the API stage that the job is waiting for (writing or reviewing)."""
    try:
        with _db() as con:
            j = _job(con, jid)
            st = settings(con)
        if j is None or j["status"] not in ("writing", "reviewing"):
            return
        data = j["input"]
        if j["status"] == "writing":
            ai = j["writer"]
            draft = clean_draft(CALL_AI(ai, st["ai"][ai]["model"], WRITE_PROMPT, data))
            advance(jid, "write", draft, by=ai)
        else:
            ai = j["reviewer"]
            user = json.dumps({"자료": json.loads(data), "초안": json.loads(j["draft"])}, ensure_ascii=False)
            advance(jid, "review", clean_review(CALL_AI(ai, st["ai"][ai]["model"], REVIEW_PROMPT, user)), by=ai)
    except Exception as exc:  # the job shows the reason; the manager can build again
        with _db() as con:
            con.execute("UPDATE learn_jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                        (("%s: %s" % (type(exc).__name__, exc))[:300], _now(), jid))


def advance(jid, stage, result, by):
    """Stores a writer draft or a reviewer verdict and moves the job on."""
    nxt = None
    with _db() as con:
        j = _job(con, jid)
        if j is None:
            raise ValueError("unknown job")
        if stage == "write":
            if j["status"] not in ("write_wait", "writing"):
                raise ValueError("이 작업은 지금 작성 단계가 아님")
            draft = clean_draft(result)
            st = settings(con)
            status = _stage_status(dict(st, writer=j["writer"], reviewer=j["reviewer"]), "reviewer")
            con.execute("UPDATE learn_jobs SET draft=?, status=?, updated_at=? WHERE id=?",
                        (json.dumps(draft, ensure_ascii=False), status, _now(), jid))
            _audit(con, by, "학습서 초안 %s" % jid)
            nxt = status
        elif stage == "review":
            if j["status"] not in ("review_wait", "reviewing"):
                raise ValueError("이 작업은 지금 검수 단계가 아님")
            review = clean_review(result)
            ver = con.execute("SELECT COALESCE(MAX(version),0)+1 FROM learn_books WHERE site_id=?",
                              (j["site_id"],)).fetchone()[0]
            con.execute("INSERT INTO learn_books(site_id,version,status,body,review,writer,reviewer,job_id,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)", (j["site_id"], ver, "draft", j["draft"],
                                                      json.dumps(review, ensure_ascii=False), j["writer"],
                                                      j["reviewer"], jid, _now()))
            con.execute("UPDATE learn_jobs SET review=?, status='ready', updated_at=? WHERE id=?",
                        (json.dumps(review, ensure_ascii=False), _now(), jid))
            _audit(con, by, "학습서 검수 %s → %s 판 %s (%s)" % (jid, j["site_id"], ver, review["verdict"]))
        else:
            raise ValueError("stage must be write or review")
    if nxt == "reviewing":
        _spawn(jid)


CALL_AI = call_ai     # tests replace this


# ---------------------------------------------------------------------------
# books
def decide(con, site_id, version, action, collector=""):
    b = con.execute("SELECT * FROM learn_books WHERE site_id=? AND version=?", (site_id, version)).fetchone()
    if b is None:
        raise ValueError("unknown book")
    if action == "trial":
        if not collector:
            raise ValueError("시험할 수집기를 고르십시오")
        con.execute("UPDATE learn_books SET status='retired' WHERE site_id=? AND status='trial'", (site_id,))
        con.execute("UPDATE learn_books SET status='trial', trial_collector=?, decided_at=? WHERE site_id=? AND version=?",
                    (collector[:60], _now(), site_id, version))
    elif action == "live":
        con.execute("UPDATE learn_books SET status='retired' WHERE site_id=? AND status IN ('live','trial') AND version<>?",
                    (site_id, version))
        con.execute("UPDATE learn_books SET status='live', trial_collector='', decided_at=? WHERE site_id=? AND version=?",
                    (_now(), site_id, version))
    elif action in ("reject", "retire"):
        con.execute("UPDATE learn_books SET status=?, decided_at=? WHERE site_id=? AND version=?",
                    ("rejected" if action == "reject" else "retired", _now(), site_id, version))
    else:
        raise ValueError("action: trial, live, reject, retire")
    _audit(con, "manager", "학습서 %s 판 %s → %s %s" % (site_id, version, action, collector))


def for_visit(con, name, site_id, have_version=None):
    """The 학습서 a collector gets when it says it is entering a site."""
    if not SAFE_ID.match(site_id or ""):
        raise ValueError("site_id required")
    b = con.execute("SELECT * FROM learn_books WHERE site_id=? AND status='trial' AND trial_collector=?",
                    (site_id, name)).fetchone()
    trial = b is not None
    if b is None:
        b = con.execute("SELECT * FROM learn_books WHERE site_id=? AND status='live'", (site_id,)).fetchone()
    if b is None:
        return {"ok": True, "site_id": site_id, "version": 0, "book": None}
    out = {"ok": True, "site_id": site_id, "version": b["version"], "trial": trial}
    if str(have_version) == str(b["version"]):
        out["unchanged"] = True
    else:
        out["book"] = json.loads(b["body"])
    return out


def admin_state(con):
    jobs = [{k: r[k] for k in ("id", "site_id", "created_at", "updated_at", "status", "writer", "reviewer", "error")}
            | {"status_text": JOB_TEXT.get(r["status"], r["status"])}
            for r in con.execute("SELECT * FROM learn_jobs ORDER BY created_at DESC LIMIT 20")]
    books = []
    for r in con.execute("SELECT * FROM learn_books ORDER BY site_id, version DESC"):
        d = {k: r[k] for k in ("site_id", "version", "status", "trial_collector", "writer", "reviewer", "created_at",
                               "decided_at")}
        d["body"] = json.loads(r["body"])
        d["review"] = json.loads(r["review"]) if r["review"] else None
        books.append(d)
    sites = {}
    for r in con.execute("SELECT site_id, COUNT(*) n, SUM(result='ok') ok, MAX(at) last FROM experiences GROUP BY site_id"):
        sites[r["site_id"]] = {"n": r["n"], "ok": r["ok"], "last": r["last"], "by_version": book_results(con, r["site_id"])}
    return {"settings": settings(con), "ais": ai_info(), "jobs": jobs, "books": books, "sites": sites}


# ---------------------------------------------------------------------------
# HTTP (called from controller.Handler)
def route(h, method, p, q):
    """Collector and bot addresses. Returns True when handled."""
    if p == "/api/collector/experience" and method == "POST":
        b = h._body()
        name = str(b.get("name") or "")
        with _db() as con:
            if h._collector_ok(con, name) is None:
                h._json(403, {"ok": False})
                return True
            sites = add_experience(con, name, b.get("records"))
            started = maybe_auto(con, sites)
        for jid in started:
            run_if_api(jid)
        h._json(200, {"ok": True, "stored": len(b["records"]), "jobs_started": started})
        return True
    if p == "/api/collector/visit" and method == "POST":
        b = h._body()
        name = str(b.get("name") or "")
        with _db() as con:
            if h._collector_ok(con, name) is None:
                h._json(403, {"ok": False})
                return True
            out = for_visit(con, name, str(b.get("site_id") or ""), b.get("version"))
        h._json(200, out)
        return True
    if p == "/api/bot/learning" and method == "GET":
        if not h._bot():
            h._json(403, {"ok": False})
            return True
        out = []
        with _db() as con:
            st = settings(con)
            for r in con.execute("SELECT * FROM learn_jobs WHERE status IN ('write_wait','review_wait') ORDER BY created_at"):
                role = "writer" if r["status"] == "write_wait" else "reviewer"
                if r[role] != "grok" or st["ai"]["grok"]["mode"] != "bot":
                    continue
                if role == "writer":
                    out.append({"id": r["id"], "stage": "write", "site_id": r["site_id"], "prompt": WRITE_PROMPT,
                                "input": json.loads(r["input"])})
                else:
                    out.append({"id": r["id"], "stage": "review", "site_id": r["site_id"], "prompt": REVIEW_PROMPT,
                                "input": {"자료": json.loads(r["input"]), "초안": json.loads(r["draft"])}})
        h._json(200, {"ok": True, "jobs": out})
        return True
    m = re.fullmatch(r"/api/bot/learning/([A-Za-z0-9]+)", p)
    if m and method == "POST":
        if not h._bot():
            h._json(403, {"ok": False})
            return True
        b = h._body()
        with _db() as con:
            j = _job(con, m.group(1))
        if j is None:
            h._json(404, {"ok": False})
            return True
        stage = b.get("stage")
        role = "writer" if stage == "write" else "reviewer"
        if j[role] != "grok":
            h._json(403, {"ok": False, "error": "이 단계는 글록 봇 담당이 아님"})
            return True
        advance(j["id"], stage, b.get("result"), by="grok_bot")
        h._json(200, {"ok": True})
        return True
    return False


def admin_route(h, method, p, q, b):
    """Manager addresses under /api/admin/learning/. Returns True when handled."""
    if not p.startswith("/api/admin/learning/") or method != "POST":
        return False
    with _db() as con:
        if p == "/api/admin/learning/settings":
            cur = settings(con)
            for k in ("writer", "reviewer"):
                if k in b:
                    if b[k] not in AIS:
                        raise ValueError("AI: " + ", ".join(AIS))
                    cur[k] = b[k]
            if "auto_after" in b:
                v = int(b["auto_after"])
                if not 0 <= v <= 100000:
                    raise ValueError("auto_after 0..100000")
                cur["auto_after"] = v
            for k, v in (b.get("ai") or {}).items():
                if k not in AIS or not isinstance(v, dict):
                    raise ValueError("unknown AI " + str(k))
                if "mode" in v:
                    if v["mode"] not in AIS[k]["modes"]:
                        raise ValueError("%s mode: %s" % (k, AIS[k]["modes"]))
                    cur["ai"][k]["mode"] = v["mode"]
                if "model" in v:
                    if not isinstance(v["model"], str) or not 0 < len(v["model"]) <= 80:
                        raise ValueError("model")
                    cur["ai"][k]["model"] = v["model"]
            _kv_set(con, "learning", cur)
            _audit(con, "manager", "학습서 AI: 작성 %s, 검수 %s" % (cur["writer"], cur["reviewer"]))
            h._json(200, {"ok": True, "settings": cur})
        elif p == "/api/admin/learning/key":
            ai = b.get("ai")
            if ai not in AIS:
                raise ValueError("AI: " + ", ".join(AIS))
            key = str(b.get("key") or "").strip()
            if len(key) > 300:
                raise ValueError("key too long")
            set_key(ai, key)
            _audit(con, "manager", "%s API 키 %s" % (ai, "저장" if key else "삭제"))   # never the key itself
            h._json(200, {"ok": True, "has_key": bool(api_key(ai))})
        elif p == "/api/admin/learning/build":
            jid = start_job(con, str(b.get("site_id") or ""))
            con.commit()
            run_if_api(jid)
            h._json(200, {"ok": True, "job_id": jid})
        elif p == "/api/admin/learning/book":
            decide(con, str(b.get("site_id") or ""), int(b.get("version") or 0), str(b.get("action") or ""),
                   str(b.get("collector") or ""))
            h._json(200, {"ok": True})
        else:
            h._json(404, {"ok": False})
    return True
