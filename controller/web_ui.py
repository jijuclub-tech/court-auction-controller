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

# jiju0001~jiju0009 관제형 미리 등록 (이미 있으면 그대로 둠 → 재시작해도 메모·실적 유지)
for i in range(1, 10):
    try:
        if f"jiju{i:04d}" not in ctrl.collectors:
            ctrl.register_collector(f"jiju{i:04d}", "control")
    except Exception:
        pass


def done(msg: str, ok: bool = True, endpoint: str = "index"):
    """저장 후 같은 화면으로 다시 이동 + 알림 (새로고침해도 두 번 저장되지 않음)"""
    flash(msg, "ok" if ok else "error")
    return redirect(url_for(endpoint))


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
        flash("아이디 또는 비밀번호가 맞지 않습니다", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    lang = session.get("lang")
    session.clear()
    if lang:
        session["lang"] = lang
    flash("로그아웃되었습니다", "ok")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    summary = ctrl.get_status_summary()
    stats = ctrl.get_statistics()
    counts = {
        "collectors": len(ctrl.collectors),
        "natural_tasks": len(getattr(ctrl, "natural_tasks", [])),
        "instructions": len(getattr(ctrl, "pending_instructions", [])),
        "api_keys": len(ctrl.list_api_keys()),
    }
    return render_template("index.html", summary=summary, stats=stats, counts=counts, active="home")


@app.route("/collectors", methods=["GET", "POST"])
@login_required
def collectors():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        ctype = request.form.get("type", "supply")
        memo = request.form.get("memo", "").strip()
        if not name:
            return done("이름을 입력하세요", False, "collectors")
        label = "관제형" if ctype == "control" else "보급형"
        try:
            if name in ctrl.collectors:
                ctrl.update_collector(name, ctype, memo)
                return done(f"이미 있는 수집기 '{name}'의 유형·메모를 갱신했습니다 ({label})", True, "collectors")
            ctrl.register_collector(name, ctype, memo=memo)
            return done(f"수집기 '{name}' 등록 완료 ({label})", True, "collectors")
        except Exception as e:
            return done(f"수집기 등록 실패: {e}", False, "collectors")
    items = []
    for name, c in ctrl.collectors.items():
        items.append({
            "name": name,
            "type": c.get("type", "supply"),
            "speed": c.get("speed_score", 1),
            "success": round(c.get("success_rate", 0) * 100, 1),
            "memo": c.get("memo", ""),
            "registered": (c.get("registered_at") or "")[:16].replace("T", " "),
            "last_connected": (c.get("last_connected_at") or "")[:16].replace("T", " "),
            "busy": c.get("is_busy", False),
        })
    return render_template("collectors.html", collectors=items, active="collectors")


@app.route("/tasks", methods=["GET", "POST"])
@login_required
def tasks():
    if request.method == "POST":
        form_type = request.form.get("form_type", "natural")
        try:
            if form_type == "natural":
                text = request.form.get("task_text", "").strip()
                memo = request.form.get("memo", "").strip()
                if not text:
                    return done("작업 내용을 입력하세요", False, "tasks")
                nt = ctrl.add_natural_task(text, memo)
                return done(f"작업 추가 완료 (번호 {nt['id']})", True, "tasks")
            case_no = request.form.get("case_no", "").strip()
            task_name = request.form.get("task_type", "감정평가서")
            court = request.form.get("court", "")
            if not case_no:
                return done("사건번호를 입력하세요", False, "tasks")
            ctrl.ensure_case(case_no, court or "미지정")
            try:
                tt = TaskType(task_name)
            except Exception:
                tt = TaskType.APPRAISAL
            ctrl.create_task(case_no, tt)
            return done(f"상세 작업 추가 완료: {case_no} ({tt.value})", True, "tasks")
        except Exception as e:
            return done(f"작업 추가 실패: {e}", False, "tasks")
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
                "created": (d.get("created_at") or "")[:16].replace("T", " "),
            })
        items.sort(key=lambda x: x["created"], reverse=True)
    except Exception:
        pass
    return render_template("tasks.html", tasks=items, natural_tasks=list(reversed(natural)), active="tasks")


@app.route("/api-keys", methods=["GET", "POST"])
@login_required
def api_keys():
    if request.method == "POST":
        form_type = request.form.get("form_type", "api")
        try:
            if form_type == "api":
                name = request.form.get("name", "gemini").strip()
                key = request.form.get("api_key", "").strip()
                if not (name and key):
                    return done("이름과 키를 모두 입력하세요", False, "api_keys")
                ctrl.store_api_key(name, key)
                return done(f"API 키 '{name}' 저장 완료 (암호화됨)", True, "api_keys")
            if form_type == "cred":
                name = request.form.get("cred_name", "").strip()
                user = request.form.get("cred_user", "").strip()
                pw = request.form.get("cred_pw", "").strip()
                if not (name and user and pw):
                    return done("용도 이름·아이디·비밀번호를 모두 입력하세요", False, "api_keys")
                ctrl.store_credential(name, user, pw)
                return done(f"자격 증명 '{name}' 저장 완료 (암호화됨)", True, "api_keys")
        except Exception as e:
            # 오류 내용에 키 값이 섞이지 않도록 종류만 표시
            return done(f"저장 실패 ({type(e).__name__})", False, "api_keys")
    creds = ctrl.list_credentials()
    api_key_names = ctrl.list_api_keys()
    return render_template("api_keys.html", credentials=creds, api_keys=api_key_names, active="api_keys")


@app.route("/instructions", methods=["GET", "POST"])
@login_required
def instructions():
    if request.method == "POST":
        collector = request.form.get("collector", "").strip()
        cases_raw = request.form.get("cases", "").strip()
        task_name = request.form.get("task_type", "감정평가서")
        memo = request.form.get("memo", "").strip()
        cases = [c.strip() for c in cases_raw.splitlines() if c.strip()]
        if not (collector and cases):
            return done("수집기와 사건번호를 입력하세요", False, "instructions")
        try:
            try:
                tt = TaskType(task_name)
            except Exception:
                tt = TaskType.APPRAISAL
            instr = ctrl.generate_instruction(collector, tt, cases)
            if memo:
                instr.notes = f"{instr.notes} / 메모: {memo}"
                ctrl._save_state()
            warn = "" if collector in ctrl.collectors else " — 주의: 등록되지 않은 수집기 이름입니다"
            return done(f"지시서 발행 완료: {instr.instruction_id[:8]} → {collector} ({len(cases)}건){warn}",
                        not warn, "instructions")
        except Exception as e:
            return done(f"지시서 발행 실패: {e}", False, "instructions")
    # 대기 지시서 목록
    pending = []
    for i in reversed(getattr(ctrl, "pending_instructions", [])):
        pending.append({
            "id": i.instruction_id[:8],
            "count": len(i.cases),
            "notes": i.notes,
            "collector": i.collector_name,
            "task_type": i.task_type,
            "cases": ", ".join(i.cases[:3]) + ("..." if len(i.cases) > 3 else ""),
            "created": i.created_at,
        })
    return render_template("instructions.html", instructions=pending,
                           collector_names=sorted(ctrl.collectors.keys()), active="instructions")


@app.route("/reports")
@login_required
def reports():
    return render_template("reports.html", reports=list(reversed(getattr(ctrl, "collector_reports", []))),
                           active="reports")


@app.route("/api/collector/connect", methods=["POST"])
def api_connect():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    site = data.get("site", "courtauction.go.kr")
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    result = ctrl.on_collector_connect(name, site)
    return jsonify({"ok": True, "result": result})


@app.route("/api/collector/report", methods=["POST"])
def api_report():
    """수집기 → 관제기 결과 보고 (사건 1건 단위)"""
    data = request.get_json(force=True, silent=True) or {}
    name = str(data.get("name", "")).strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    try:
        result = ctrl.receive_collector_report(name, data)
    except ValueError as e:
        return jsonify({"ok": False, "error": f"invalid report: {e}"}), 400
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
            return done("업무 정책 저장 완료 — 수집기가 다음 접속 때 반영합니다", True, "policy")
        except Exception as e:
            # 잘못된 JSON이면 입력한 내용을 그대로 남겨 두고 고칠 수 있게 함
            flash(f"저장 실패: JSON 형식 오류 ({e})", "error")
            return render_template("policy.html", policy_json=raw, active="policy")
    return render_template("policy.html", policy_json=json.dumps(data, ensure_ascii=False, indent=2), active="policy")

@app.route("/api/policy")
def api_policy():
    return jsonify(load_policy())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
