"""
관제기 웹 UI - 전체 기능
- 관리자 로그인
- Gemini API 키 관리
- 수집기 등록/상태
- 업무 지시
- 보고
"""
from __future__ import annotations

import json
from functools import wraps
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash

from controller import AuctionController, TaskType

app = Flask(__name__)
app.secret_key = "bidguard-controller-secret-change-me"

TRANSLATIONS = {
    "en": {
        "home": "Home", "collectors": "Collectors", "tasks": "Tasks",
        "instructions": "Instructions", "api_keys": "API Keys", "reports": "Reports",
        "logout": "Logout", "login": "Login", "status": "Status",
        "name": "Name", "type": "Type", "add": "Add", "save": "Save",
        "case": "Case", "court": "Court", "collector": "Collector",
        "create": "Create", "username": "Username", "password": "Password",
    },
    "ko": {
        "home": "홈", "collectors": "수집기", "tasks": "작업",
        "instructions": "업무지시", "api_keys": "API 키", "reports": "보고",
        "logout": "로그아웃", "login": "로그인", "status": "현황",
        "name": "이름", "type": "유형", "add": "추가", "save": "저장",
        "case": "사건번호", "court": "법원", "collector": "수집기",
        "create": "생성", "username": "아이디", "password": "비밀번호",
    },
}

def get_lang():
    return session.get("lang", "ko")

def t():
    return TRANSLATIONS.get(get_lang(), TRANSLATIONS["ko"])

@app.context_processor
def inject_t():
    return {"t": t(), "lang": get_lang()}

@app.route("/lang/<code>")
def set_lang(code):
    if code in TRANSLATIONS:
        session["lang"] = code
    return redirect(request.referrer or url_for("index"))


BASE = Path(__file__).parent
ctrl = AuctionController(db_path=str(BASE / "controller.db"))

# 최초 관리자 (없으면 기본값)
if not ctrl.verify_admin("admin", "admin"):
    try:
        ctrl.set_admin("admin", "admin")
    except Exception:
        pass

# jiju0001~jiju0009 관제형 미리 등록
for i in range(1, 10):
    try:
        ctrl.register_collector(f"jiju{i:04d}", "control")
    except Exception:
        pass


def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if ctrl.verify_admin(user, pw):
            session["admin"] = user
            return redirect(url_for("index"))
        flash("Login failed")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    summary = ctrl.get_status_summary()
    stats = ctrl.get_statistics()
    return render_template("index.html", summary=summary, stats=stats)


@app.route("/collectors", methods=["GET", "POST"])
@login_required
def collectors():
    msg = ""
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ctype = request.form.get("type", "supply")
        if name:
            memo = request.form.get("memo", "").strip()
            ctrl.register_collector(name, ctype)
            if memo and name in ctrl.collectors:
                ctrl.collectors[name]["memo"] = memo
            msg = f"수집기 '{name}' 등록 완료 ({ctype})"
        else:
            msg = "이름을 입력하세요"
    items = []
    for name, c in ctrl.collectors.items():
        items.append({
            "name": name,
            "type": c.get("type", "supply"),
            "speed": c.get("speed_score", 1),
            "success": round(c.get("success_rate", 0) * 100, 1),
            "memo": c.get("memo", ""),
        })
    return render_template("collectors.html", collectors=items, msg=msg)


@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    msg = ""
    if request.method == "POST":
        form_type = request.form.get("form_type", "natural")
        if form_type == "natural":
            text = request.form.get("task_text", "").strip()
            memo = request.form.get("memo", "").strip()
            if text:
                t = ctrl.add_natural_task(text, memo)
                msg = f"작업 추가됨: {t['id']}"
            else:
                msg = "작업 내용을 입력하세요"
        else:
            case_no = request.form.get("case_no", "").strip()
            task_name = request.form.get("task_type", "감정평가서")
            court = request.form.get("court", "")
            if case_no:
                ctrl.ensure_case(case_no, court or "미지정")
                try:
                    tt = TaskType(task_name)
                except Exception:
                    tt = TaskType.APPRAISAL
                ctrl.create_task(case_no, tt)
                msg = f"상세 작업 추가: {case_no}"
    natural = getattr(ctrl, "natural_tasks", [])
    items = []
    try:
        import sqlite3, json as _json
        con = sqlite3.connect(str(BASE / "controller.db"))
        cur = con.execute("SELECT id, data FROM tasks")
        rows = cur.fetchall()
        con.close()
        for _id, data in rows:
            d = _json.loads(data) if data else {}
            items.append({
                "case_no": d.get("case_no",""),
                "task_type": d.get("task_type",""),
                "status": d.get("status",""),
                "assigned_to": d.get("assigned_to") or "",
            })
    except Exception:
        pass
    return render_template("tasks.html", tasks=items, natural_tasks=natural, msg=msg)


@app.route("/api-keys", methods=["GET", "POST"])
@login_required
def api_keys():
    msg = ""
    if request.method == "POST":
        form_type = request.form.get("form_type", "api")
        if form_type == "api":
            name = request.form.get("name", "gemini").strip()
            key = request.form.get("api_key", "").strip()
            if name and key:
                ctrl.store_api_key(name, key)
                msg = f"API 키 저장: {name}"
        elif form_type == "cred":
            name = request.form.get("cred_name", "").strip()
            user = request.form.get("cred_user", "").strip()
            pw = request.form.get("cred_pw", "").strip()
            if name and user and pw:
                ctrl.store_credential(name, user, pw)
                msg = f"자격 증명 저장: {name}"
    creds = ctrl.list_credentials()
    api_key_names = ctrl.list_api_keys()
    return render_template("api_keys.html", msg=msg, credentials=creds, api_keys=api_key_names)


@app.route("/instructions", methods=["GET", "POST"])
@login_required
def instructions():
    msg = ""
    if request.method == "POST":
        collector = request.form.get("collector", "").strip()
        cases_raw = request.form.get("cases", "").strip()
        task_name = request.form.get("task_type", "감정평가서")
        cases = [c.strip() for c in cases_raw.splitlines() if c.strip()]
        if collector and cases:
            try:
                tt = TaskType(task_name)
            except Exception:
                tt = TaskType.APPRAISAL
            instr = ctrl.generate_instruction(collector, tt, cases)
            msg = f"지시서 발행: {instr.instruction_id[:8]} → {collector}"
        else:
            msg = "수집기와 사건번호를 입력하세요"
    # 대기 지시서 목록
    pending = []
    for i in getattr(ctrl, "pending_instructions", []):
        pending.append({
            "collector": i.collector_name,
            "task_type": i.task_type,
            "cases": ", ".join(i.cases[:3]) + ("..." if len(i.cases) > 3 else ""),
            "created": i.created_at,
        })
    return render_template("instructions.html", instructions=pending, msg=msg)


@app.route("/reports")
@login_required
def reports():
    return render_template("reports.html")


@app.route("/api/collector/connect", methods=["POST"])
def api_connect():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    site = data.get("site", "courtauction.go.kr")
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    result = ctrl.on_collector_connect(name, site)
    return jsonify({"ok": True, "result": result})


@app.route("/api/collector/instruction/<name>")
def api_instruction(name):
    inst = ctrl.get_next_instruction(name)
    if not inst:
        return jsonify({"ok": True, "instruction": None})
    return jsonify({"ok": True, "instruction": inst.__dict__ if hasattr(inst, "__dict__") else str(inst)})



POLICY_PATH = BASE / "defaults" / "work_policy.json"

def load_policy():
    if POLICY_PATH.exists():
        return json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    return {}

def save_policy(data):
    POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    POLICY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

@app.route("/policy", methods=["GET", "POST"])
@login_required
def policy():
    data = load_policy()
    msg = ""
    if request.method == "POST":
        raw = request.form.get("policy_json", "")
        try:
            data = json.loads(raw)
            save_policy(data)
            msg = "Saved"
        except Exception as e:
            msg = f"Error: {e}"
    return render_template("policy.html", policy_json=json.dumps(data, ensure_ascii=False, indent=2), msg=msg)

@app.route("/api/policy")
def api_policy():
    return jsonify(load_policy())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
