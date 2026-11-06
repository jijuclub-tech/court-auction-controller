"""글록 판에서 새로 들어온 부분 시험: human_input(HumanHID), captcha, login, main.py"""
import sys, os, time, json, random, statistics, logging
sys.path.insert(0, os.environ.get("FAKE_TK_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fake_tk"))); sys.path.insert(0, os.getcwd())
logging.disable(logging.CRITICAL)
from core.human_input import HumanHID
from core.captcha import CaptchaHandler
from core.login import ControlTypeLogin
ok_all = True
def check(label, cond, detail=""):
    global ok_all; ok_all &= bool(cond); print(("PASS" if cond else "FAIL"), label, detail)

# 1) 타이핑: 오타 후 지우기까지 반영하면 최종 글자가 정확한가 (200회)
def typed(text, rate):
    cmds = []
    h = HumanHID(pico2_send=lambda c, **k: cmds.append((c, k)))
    import core.human_input as hi; real = hi.time.sleep; hi.time.sleep = lambda s: None
    try: h.human_type(text, error_rate=rate)
    finally: hi.time.sleep = real
    out = []
    for c, k in cmds:
        if c == "KEY":
            out.pop() if k["key"] == "BACKSPACE" else out.append(k["key"])
    return "".join(out), cmds
bad = [t for t in range(200) if typed("2026타경12345 admin!", 0.3)[0] != "2026타경12345 admin!"]
check("오타 30%로 200회 입력해도 최종 글자 정확", not bad, f"틀린 횟수 {len(bad)}")
_, cmds = typed("x" * 2000, 0.03)
typos = sum(1 for c, k in cmds if k.get("key") == "BACKSPACE")
check("기본 오타 비율 약 3%", 30 <= typos <= 95, f"2000자 중 {typos}회")

# 2) 실제 시간: admin 타이핑, 이동, 클릭
h = HumanHID(pico2_send=lambda c, **k: None)
ts = []
for _ in range(5):
    t = time.time(); h.human_type("admin"); ts.append(time.time() - t)
print(f"   'admin' 입력 시간: 최소 {min(ts):.2f}s 최대 {max(ts):.2f}s")
moves = []
h = HumanHID(pico2_send=lambda c, **k: moves.append(k) if c == "MOVE" else None)
t = time.time(); h.move_to(800, 600); dt = time.time() - t
check("마우스 이동이 목표에 도착", moves[-1] == {"x": 800, "y": 600}, f"{len(moves)}점, {dt:.2f}s")
straight = all(abs(p["y"] - p["x"] * 600 / 800) < 2 for p in moves[1:-1])
check("직선이 아닌 곡선 경로", not straight)
clicks = []
h = HumanHID(pico2_send=lambda c, **k: clicks.append(k) if c == "CLICK" else None)
for _ in range(50): h.current_pos = (0, 0); h.click(500, 300)
off = [(c["x"] - 500, c["y"] - 300) for c in clicks]
check("클릭 위치 흔들림 -3~+4 범위", all(-3 <= x <= 4 and -3 <= y <= 4 for x, y in off))

# 3) 로그인(관제형) 소요 시간과 결과
lg = ControlTypeLogin("jiju0001", "control")
t = time.time(); r = lg.login("courtauction.go.kr", "admin", "admin", permission_from_controller=True); dt = time.time() - t
check("관제형 로그인 직접 입력 성공", r.get("success"), f"{dt:.1f}초 걸림")
LOGIN_SECONDS = dt
try:
    ControlTypeLogin("u1", "supply"); check("보급형 로그인 거부", False)
except PermissionError:
    check("보급형 로그인 거부", True)
r = lg.login("courtauction.go.kr", None, None, use_browser_saved=True, permission_from_controller=True)
check("자격 증명 없으면 실패 반환(멈추지 않음)", r == {"success": False, "reason": "no_credentials"}, str(r))

# 4) 보안문자 처리기: 시도 후 사용자 호출
cap_inputs = []
c = CaptchaHandler("jiju0001", pico2_input=lambda t: cap_inputs.append(t))
t = time.time(); r1 = c.handle_view_dialog(already_checked=True); d1 = time.time() - t
check("열람하기: 이미 체크면 클릭 후 성공", r1["success"] and cap_inputs == ["CLICK_VIEW"], f"{d1:.1f}초")
t = time.time(); r2 = c.handle_view_dialog(already_checked=False); d2 = time.time() - t
check("열람하기: 체크 필요하면 사용자 호출", r2["needs_human"] and not r2["success"], f"{d2:.1f}초")
r3 = c.handle_security_code("img.png", ai_recognize=lambda p: "A1B2")
check("보안문자: AI 인식 결과를 피코2로 입력", r3["success"] and cap_inputs[-1] == "A1B2")
r4 = c.handle_security_code("img.png", ai_recognize=lambda p: (_ for _ in ()).throw(RuntimeError("x")))
check("보안문자: AI 실패하면 사용자 호출 (자동 포기 아님)", r4["needs_human"])

# 5) main.py: 고정 사건번호 없이 화면 실행
json.dump({"name": "jiju0009", "type": "control"}, open("config.json", "w"))
import tkinter as tk
import main as m
m.main()
titles = [w for w in tk.WIDGETS if isinstance(w, tk.Tk)]
check("main.py 실행 → 수집기 화면 생성", bool(titles))
src = open("main.py", encoding="utf-8").read() + open("ui/app.py", encoding="utf-8").read()
check("고정 사건번호 2025타경56881 없음", "56881" not in src)
check("창 제목 '그록 버전 수집기'", 'self.root.title("그록 버전 수집기")' in src)
# 6) 로그에 비밀번호 글자가 남는지 (기록용: 결과와 무관하게 사실만 출력)
import io
logging.disable(logging.NOTSET)
buf = io.StringIO(); hd = logging.StreamHandler(buf); logging.getLogger().addHandler(hd); logging.getLogger().setLevel(logging.INFO)
ControlTypeLogin("jiju0001", "control").login("courtauction.go.kr", "user1", "Pw#9z", permission_from_controller=True)
leaked = "".join(l.split("'key': '")[1][0] for l in buf.getvalue().splitlines() if "KEY {'key': '" in l)
print("   [기록] 로그인 때 로그에 찍힌 키 순서:", leaked, "(비밀번호 Pw#9z 포함 여부:", "Pw#9z" in leaked, ")")
logging.getLogger().removeHandler(hd)
print("ALL PASS" if ok_all else "SOME FAILED")
