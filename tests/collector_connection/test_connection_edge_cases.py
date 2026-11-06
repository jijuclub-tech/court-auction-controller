import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("FAKE_TK_DIR", os.path.join(HERE, "fake_tk")))  # 화면 부품 대역 (tkinter 없는 서버용)
sys.path.insert(0, os.getcwd())  # 시험할 수집기 폴더에서 실행
CTRL = os.environ.get("TEST_CTRL_URL", "http://127.0.0.1:8099")
CTRL_DB = os.environ["TEST_CTRL_DB"]
SAVE_DIR = os.environ["TEST_SAVE_DIR"]
import json, time, types, sqlite3, requests
import tkinter as tk
import core.appraisal as appraisal
SLOW = {"on": False}
appraisal.time = types.SimpleNamespace(sleep=lambda s: (time.sleep(0.6) if SLOW["on"] else None), time=time.time)
import ui.app as app
from core.reporter import ControllerReporter
ok_all = True
def check(label, cond, detail=""):
    global ok_all; ok_all &= bool(cond); print(("PASS" if cond else "FAIL"), label, detail)
def wait(cond, sec=15):
    t = time.time()
    while time.time() - t < sec:
        if cond(): return True
        time.sleep(0.2)
    return False
web = requests.Session(); web.post(f"{CTRL}/login", data={"username": "admin", "password": "admin"})
def task_status(case):
    con = sqlite3.connect(CTRL_DB)
    for (d,) in con.execute("SELECT data FROM tasks"):
        d = json.loads(d)
        if d["case_no"] == case: return d["status"]
    return None
def make_ui(name, ctype):
    tk.WIDGETS.clear()
    json.dump({"name": name, "type": ctype}, open("config.json", "w"))
    if os.path.exists("settings.json"): os.remove("settings.json")
    u = app.CollectorUI(tk.Tk()); u.ctrl_entry.insert(0, CTRL); u.connect_controller()
    wait(lambda: u.settings_msg.get().startswith("Connected"))
    return u
def press_start(u):
    u.start(); [w for w in tk.WIDGETS if isinstance(w, tk.Button) and w.kw.get("text") == "저장 닫기"][-1].kw["command"]()

# 1) 보급형: 빈 입력칸 + 결과 보고
u = make_ui("probe-supply", "supply")
check("보급형 Collect 문구", any(w.text == "수집할 내용 입력하세요" for w in tk.WIDGETS if isinstance(w, tk.Label)))
check("보급형 입력칸 비어 있음 (고정 사건번호 없음)", u.case_box.get() == "")
check("미등록 이름 경고", "등록되어 있지 않습니다" in u.settings_msg.get(), u.settings_msg.get())
u.case_box.insert("end", "2026타경44444"); u.start_collection()
[w for w in tk.WIDGETS if isinstance(w, tk.Button) and w.kw.get("text") == "저장 닫기"][-1].kw["command"]()
wait(lambda: not u.running and task_status("2026타경44444"))
check("보급형 결과도 관제기 작업에 반영", task_status("2026타경44444") == "성공", task_status("2026타경44444"))

# 2) 사람 확인 필요 → 남은 사건은 '미수행'으로 보고, 지시 대기 일시정지
web.post(f"{CTRL}/instructions", data={"collector": "jiju0002", "task_type": "감정평가서", "cases": "2026타경55551\n2026타경55552"})
orig = appraisal.AppraisalDownloader.handle_view_dialog
appraisal.AppraisalDownloader.handle_view_dialog = lambda self: False
u = make_ui("jiju0002", "control"); u.settings["instruction_poll_seconds"] = 5; press_start(u)
wait(lambda: not u.running and task_status("2026타경55552"))
appraisal.AppraisalDownloader.handle_view_dialog = orig
check("사람 확인 필요 표시", u.human_var.get() == "User action required")
check("사람 확인 사건 재시도로 표시", task_status("2026타경55551") == "재시도", task_status("2026타경55551"))
check("남은 사건도 보고되어 재시도", task_status("2026타경55552") == "재시도", task_status("2026타경55552"))
check("사람 확인 뒤 지시 대기 멈춤", not u.running)

# 3) 수행 중 Stop → 남은 사건 보고
web.post(f"{CTRL}/instructions", data={"collector": "jiju0003", "task_type": "감정평가서", "cases": "2026타경66661\n2026타경66662\n2026타경66663"})
SLOW["on"] = True
u = make_ui("jiju0003", "control"); u.settings["instruction_poll_seconds"] = 5; press_start(u)
wait(lambda: "66661" in u.task_var.get(), 10); u.stop()
wait(lambda: not u.worker.is_alive() and task_status("2026타경66663"), 20); SLOW["on"] = False
check("Stop 중 남은 사건 보고됨", task_status("2026타경66663") == "재시도", task_status("2026타경66663"))
rep = web.get(f"{CTRL}/reports").text
check("관제기 보고 화면에 중지 사유", "사용자 중지로 수행하지 못함" in rep)

# 4) 관제기 꺼짐 → 보관 → 다시 켜면 재전송
r = ControllerReporter("jiju0004")
sent = r.send_to_controller("http://127.0.0.1:8098", "2026타경77771", "감정평가서", True)
check("관제기 꺼져 있으면 보관", (not sent) and len(list(r.unsent_dir.glob("*.json"))) == 1)
n = r.flush_unsent(CTRL)
check("다시 연결되면 재전송 후 보관함 비움", n == 1 and not list(r.unsent_dir.glob("*.json")) and task_status("2026타경77771") == "성공")
print("ALL PASS" if ok_all else "SOME FAILED")
