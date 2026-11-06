"""관제기 ↔ 관제형 수집기 연결·지시 수신·수행·보고 시험"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.environ.get("FAKE_TK_DIR", os.path.join(HERE, "fake_tk")))  # 화면 부품 대역 (tkinter 없는 서버용)
sys.path.insert(0, os.getcwd())  # 시험할 수집기 폴더에서 실행
CTRL = os.environ.get("TEST_CTRL_URL", "http://127.0.0.1:8099")
CTRL_DB = os.environ["TEST_CTRL_DB"]
SAVE_DIR = os.environ["TEST_SAVE_DIR"]
import sys, os, json, time, sqlite3, requests
import tkinter as tk
from tkinter import messagebox
import core.appraisal as appraisal
import types
appraisal.time = types.SimpleNamespace(sleep=lambda s: None, time=time.time)  # 감정평가서 모듈 안의 대기만 시험에서 생략
import ui.app as app

ok_all = True
def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS" if cond else "FAIL"), label, detail)

# 관제기에서 지시서 발행 (관리자 로그인 후)
web = requests.Session()
web.post(f"{CTRL}/login", data={"username": "admin", "password": "admin"})
for task, cases in [("감정평가서", "2026타경11111\n2026타경11112"), ("등기부등본", "2026타경22222"), ("기본정보", "2026타경33333")]:
    web.post(f"{CTRL}/instructions", data={"collector": "jiju0001", "task_type": task, "cases": cases})
page = web.get(f"{CTRL}/instructions").text
check("관제기 대기 지시서 목록 3건", "대기 중인 지시서 <span class=\"count\">3건" in page)

# 수집기 화면 (관제형 jiju0001)
json.dump({"name": "jiju0001", "type": "control"}, open("config.json", "w"))
root = tk.Tk()
ui = app.CollectorUI(root)
texts = [w.text for w in tk.WIDGETS if isinstance(w, tk.Label)]
check("관제형 Collect 문구", "관제형 수집기는 관제기의 지시를 수행합니다." in texts)
collect_buttons = [w for w in tk.WIDGETS if isinstance(w, tk.Button) and w.kw.get("text") == "Start collection"]
check("관제형 Collect 탭에 사건 입력·시작 버튼 없음", not collect_buttons and not hasattr(ui, "case_box"))
check("로그인 기본값 admin/admin", ui.user_entry.get() == "admin" and ui.pw_entry.get() == "admin")
check("배율 기능 없음", not any(hasattr(ui, m) for m in ("_check_display_scale", "_ask_scale_action")))
check("미연결 안내", "관제기 미연결" in ui.instr_var.get(), ui.instr_var.get())

# 연결 (http:// 없이 입력해도 됨, 저장 버튼 안 눌러도 저장)
ui.ctrl_entry.insert(0, "127.0.0.1:8099")
ui.connect_controller()
for _ in range(40):
    if ui.ctrl_connected and ui.ctrl_var.get() == "Controller: connected": break
    time.sleep(0.25)
check("Status: Controller: connected", ui.ctrl_var.get() == "Controller: connected", ui.ctrl_var.get())
check("연결 메시지에 대기 지시서 3건", "대기 지시서 3건" in ui.settings_msg.get(), ui.settings_msg.get())
check("관제기 주소 저장됨", json.load(open("settings.json"))["controller_url"] == CTRL)
check("연결만으로 지시서를 가져가지 않음", "3건" in web.get(f"{CTRL}/instructions").text)

# Start → 시작 옵션 창 '저장 닫기' → 지시 대기 루프
ui.settings["instruction_poll_seconds"] = 5
ui.start()
btn = [w for w in tk.WIDGETS if isinstance(w, tk.Button) and w.kw.get("text") == "저장 닫기"]
check("옵션 창 버튼 '저장 닫기'", len(btn) == 1)
btn[0].kw["command"]()
check("시작됨", ui.running)
deadline = time.time() + 60
while time.time() < deadline:
    if "대기 중인 지시서 <span class=\"count\">0건" in web.get(f"{CTRL}/instructions").text and "지시 대기 중" in ui.instr_var.get():
        break
    time.sleep(0.5)
check("수집기가 지시서 3건 모두 수령", "0건" in web.get(f"{CTRL}/instructions").text)
check("수행 후 다시 지시 대기", "지시 대기 중" in ui.instr_var.get(), ui.instr_var.get())

# 수행 결과: 파일, 관제기 작업 상태
files = sorted(os.listdir(SAVE_DIR))
check("지시서 저장 경로에 감정평가서 2건 저장", len([f for f in files if "2026타경1111" in f]) == 2, files)
con = sqlite3.connect(CTRL_DB)
rows = {json.loads(d)["case_no"]: json.loads(d) for (d,) in con.execute("SELECT data FROM tasks")}
status = {k: (v["task_type"], v["status"], v["assigned_to"]) for k, v in rows.items()}
print("   관제기 작업 상태:", status)
check("감정평가서 2건 성공", status.get("2026타경11111", ("", ""))[1] == "성공" and status.get("2026타경11112", ("", ""))[1] == "성공")
check("등기부등본 유형 유지", status.get("2026타경22222", ("",))[0] == "등기부등본")
check("미지원 기본정보는 성공으로 꾸미지 않음", status.get("2026타경33333", ("", ""))[1] in ("재시도", "실패"))
check("배정 수집기 jiju0001 기록", status.get("2026타경11111", ("", "", ""))[2] == "jiju0001")
rep = web.get(f"{CTRL}/reports").text
check("관제기 보고 화면 4건", "수집기 결과 보고 <span class=\"count\">4건" in rep)
col = web.get(f"{CTRL}/collectors").text
check("관제기 수집기 목록에 마지막 접속 표시", "접속 기록 없음" in col and col.count("2026-") >= 10)

# Stop
ui.stop()
t0 = time.time()
while time.time() - t0 < 8 and (ui.running or ui.worker.is_alive()):
    time.sleep(0.2)
print("   Stop 후 작업 종료까지", round(time.time() - t0, 1), "초 / worker alive:", ui.worker.is_alive())
check("Stop 후 대기 해제", not ui.running and "Start를 누르면" in ui.instr_var.get(), ui.instr_var.get())
print("ALL PASS" if ok_all else "SOME FAILED")
