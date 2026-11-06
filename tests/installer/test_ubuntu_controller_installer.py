"""우분투 관제기 설치기 실제 설치 시험 — 격리된 시험 상자(BIDGUARD_ROOT_PREFIX) 안에서 진짜로 설치·실행·업그레이드·되돌리기·지우기
사용: python test_ubuntu_controller_installer.py <Court-Auction-Controller-Ubuntu-Setup-*.zip>
(systemd 가 없는 환경에서는 설치기가 관제기를 직접 켜고, 시험은 그 상태로 확인한다)
"""
import hashlib, http.cookiejar, json, os, shutil, socket, subprocess, sys, tempfile, time, urllib.parse, urllib.request, zipfile
from pathlib import Path

ZIP = Path(sys.argv[1]).resolve()
WORK = Path(tempfile.mkdtemp(prefix="bg_ubuntu_test_"))
PORT = 8199
ok_all = True
def check(label, cond, detail=""):
    global ok_all; ok_all &= bool(cond); print(("PASS" if cond else "FAIL"), label, detail)

def extract(dst):
    with zipfile.ZipFile(ZIP) as z: z.extractall(dst)
    root = next(Path(dst).iterdir())
    for p in root.rglob("*.sh"): p.chmod(0o755)
    return root

def run(script, box, extra=None, label=""):
    env = dict(os.environ, BIDGUARD_NONINTERACTIVE="1", BIDGUARD_ROOT_PREFIX=str(box), BIDGUARD_PORT=str(PORT), BIDGUARD_RUN_USER="root")
    env.update(extra or {})
    r = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL)
    (WORK / f"out_{label or script.name}.txt").write_text(r.stdout + r.stderr)
    return r

class Web:
    def __init__(self):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def get(self, path):
        return self.op.open(f"http://127.0.0.1:{PORT}{path}", timeout=5).read().decode()
    def post(self, path, data):
        return self.op.open(f"http://127.0.0.1:{PORT}{path}", urllib.parse.urlencode(data).encode(), timeout=5).read().decode()
    def login(self):
        self.post("/login", {"username": "admin", "password": "admin"}); return self

def up():
    try: return urllib.request.urlopen(f"http://127.0.0.1:{PORT}/login", timeout=2).status == 200
    except Exception: return False

def stop_box(box):
    pidf = box / "opt/bidguard/controller/.bidguard_test_pid"
    if pidf.exists():
        try: os.kill(int(pidf.read_text()), 15)
        except Exception: pass
    subprocess.run(["pkill", "-f", f"port={PORT}"], capture_output=True); time.sleep(1)

bundle = extract(WORK / "bundle")
inst = bundle / "install_court_auction_controller.sh"
box = WORK / "box"; box.mkdir()
cdir = box / "opt/bidguard/controller"
unit = box / "etc/systemd/system/bidguard-court-auction-controller.service"

# S1 새로 설치
r = run(inst, box, label="s1")
check("S1 새로 설치 성공", r.returncode == 0, r.stdout[-300:] if r.returncode else "")
check("S1 관제기 화면이 실제로 열림", up())
rec = json.loads((cdir / "install_record.json").read_text())
check("S1 설치 기록 (판·포트)", rec["version"] == "S-0.1.0" and rec["port"] == str(PORT))
u = unit.read_text()
check("S1 자동 실행 서비스: 실제 계정·포트·작업 폴더·파일 권한", "User=root" in u and f"port={PORT}" in u and f"WorkingDirectory={cdir}" in u and "UMask=0077" in u)
check("S1 직접 실행 파일·지우기 파일 생성", (cdir / "Run_Court_Auction_Controller.sh").exists() and (cdir / "uninstall_court_auction_controller.sh").exists())
check("S1 완료 화면에 수집기용 주소·admin 주의", f":{PORT}" in r.stdout and "admin / admin" in r.stdout)
check("S1 옛 판·임시 폴더 안 남음", not list((box / "opt/bidguard").glob("controller.*")))

# S2 관제기 사용 (자료 생김)
w = Web().login()
w.post("/collectors", {"name": "keep01", "type": "control", "memo": "보존확인"})
w.post("/api-keys", {"form_type": "api", "name": "gemini-keep", "api_key": "secret-x"})
check("S2 관제기에 자료 저장됨", "보존확인" in w.get("/collectors") and "gemini-keep" in w.get("/api-keys"))
mode = oct((cdir / "apikeys.enc").stat().st_mode & 0o777)
check("S2 관제기가 새로 만든 API 키 파일도 이 계정만 읽기 (600)", mode == "0o600", mode)

# S3 같은 묶음으로 다시 설치 (업그레이드)
r = run(inst, box, label="s3")
check("S3 업그레이드 성공", r.returncode == 0, r.stdout[-300:] if r.returncode else "")
w = Web().login()
check("S3 수집기·메모 그대로", "보존확인" in w.get("/collectors"))
check("S3 API 키 그대로 (암호 열쇠도 보존)", "gemini-keep" in w.get("/api-keys"))
check("S3 비밀 파일 권한 600", oct((cdir / "apikeys.enc").stat().st_mode & 0o777) == "0o600", mode)
check("S3 옛 판 폴더 정리됨", not list((box / "opt/bidguard").glob("controller.old_*")))

# S4 묶음 파일이 바뀐 경우 → 아무것도 안 바꾸고 멈춤
t4 = WORK / "tampered"; shutil.copytree(bundle, t4)
(t4 / "payload/controller/web_ui.py").write_text("# tampered\n")
r = run(t4 / "install_court_auction_controller.sh", box, label="s4")
check("S4 바뀐 묶음은 점검에서 멈춤", r.returncode == 1 and "바뀌었거나 망가진" in r.stdout)
check("S4 돌던 관제기·자료 그대로", up() and "보존확인" in Web().login().get("/collectors"))

# S5 잘못 만든 묶음(지문은 맞지만 켜지지 않음) → 설치 전 상태로 되돌림
t5 = WORK / "broken"; shutil.copytree(bundle, t5)
wp = t5 / "payload/controller/web_ui.py"; wp.write_text(wp.read_text() + "\nraise SystemExit('broken build')\n")
m = json.loads((t5 / "BUILD-MANIFEST.json").read_text()); m["files"]["payload/controller/web_ui.py"] = hashlib.sha256(wp.read_bytes()).hexdigest()
(t5 / "BUILD-MANIFEST.json").write_text(json.dumps(m))
r = run(t5 / "install_court_auction_controller.sh", box, label="s5")
check("S5 켜지지 않으면 실패로 멈춤", r.returncode == 1 and "되돌리기 끝" in r.stdout, r.stdout[-200:])
check("S5 이전 판 파일로 되돌림", "broken build" not in (cdir / "web_ui.py").read_text())
check("S5 자료 그대로", "keep01" in (cdir / "controller_state.json").read_text())
check("S5 임시 폴더 안 남음", not [p for p in (box / "opt/bidguard").iterdir() if p.name != "controller"])
subprocess.Popen(["bash", str(cdir / "Run_Court_Auction_Controller.sh")], cwd=cdir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
for _ in range(20):
    if up(): break
    time.sleep(0.5)
check("S5 되돌린 관제기가 다시 켜지고 자료 보임", up() and "보존확인" in Web().login().get("/collectors"))
stop_box(box)

# S6 포트를 다른 프로그램이 쓰는 중 → 멈춤
box6 = WORK / "box6"; box6.mkdir()
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("0.0.0.0", PORT)); s.listen(1)
r = run(inst, box6, label="s6")
s.close()
check("S6 포트 사용 중이면 설치 안 함", r.returncode == 1 and not (box6 / "opt/bidguard/controller").exists())

# S7 글록 판(예전 install.sh) 설치가 있던 컴퓨터
box7 = WORK / "box7"; legacy = box7 / "opt/bidguard/controller"
shutil.copytree(bundle / "payload/controller", legacy)
(legacy / "venv").mkdir()
(legacy / "controller_state.json").write_text(json.dumps({"version": 1, "collectors": {"legacy07": {"name": "legacy07", "type": "supply", "memo": "글록판자료"}}, "natural_tasks": [], "pending_instructions": [], "collector_reports": []}))
(box7 / "etc/systemd/system").mkdir(parents=True)
(box7 / "etc/systemd/system/bidguard-controller.service").write_text("[Service]\nUser=ubuntu\nExecStart=/opt/bidguard/controller/venv/bin/python web_ui.py\n")
r = run(inst, box7, label="s7")
check("S7 글록 판 위에 설치 성공", r.returncode == 0, r.stdout[-300:] if r.returncode else "")
check("S7 글록 판 서비스 정리", not (box7 / "etc/systemd/system/bidguard-controller.service").exists() and (box7 / "etc/systemd/system/bidguard-court-auction-controller.service").exists())
check("S7 글록 판 자료 그대로", "글록판자료" in Web().login().get("/collectors"))
stop_box(box7)

# S8 지우기 (자료는 옮겨 둠)
r = run(cdir / "uninstall_court_auction_controller.sh", box, label="s8")
kept = list((box / "opt/bidguard").glob("controller_data_*"))
check("S8 지우기 성공·서비스 파일 지움", r.returncode == 0 and not cdir.exists() and not unit.exists())
check("S8 자료는 따로 옮겨 둠", kept and "keep01" in (kept[0] / "controller_state.json").read_text() and (kept[0] / "apikeys.enc").exists())

# S9 다시 설치 → 옮겨 둔 자료 되살림
r = run(inst, box, label="s9")
w = Web().login()
check("S9 다시 설치 성공", r.returncode == 0, r.stdout[-300:] if r.returncode else "")
check("S9 옮겨 둔 자료 자동 발견·되살림", "보존확인" in w.get("/collectors") and "gemini-keep" in w.get("/api-keys"))
check("S9 되살린 뒤 옮겨 둔 폴더 정리", not list((box / "opt/bidguard").glob("controller_data_*")))
stop_box(box)

# S10 지우기 (자료까지)
r = run(cdir / "uninstall_court_auction_controller.sh", box, {"BIDGUARD_DELETE_DATA": "1"}, label="s10")
check("S10 자료까지 지우면 아무것도 안 남음", r.returncode == 0 and not (box / "opt/bidguard").exists())

stop_box(box)
print("시험 폴더:", WORK)
print("ALL PASS" if ok_all else "SOME FAILED")
