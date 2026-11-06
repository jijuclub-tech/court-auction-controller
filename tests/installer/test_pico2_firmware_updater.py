"""피코2 펌웨어 업데이트기 자동 시험 — 가짜 보드·가짜 드라이브로 실제 순서를 그대로 돌림"""
import hashlib, json, os, sys, tempfile, shutil
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "installer" / "windows_firmware"))
sys.path.insert(0, str(HERE))
import pico2_firmware_updater as fw
from uf2_test_files import make_uf2

ok_all = True
def check(label, cond, detail=""):
    global ok_all; ok_all &= bool(cond); print(("PASS" if cond else "FAIL"), label, detail)

def expect_error(fn, words):
    try:
        fn(); return False, "오류가 나지 않음"
    except fw.UpdateError as e:
        return all(w in str(e) for w in words), str(e)

work = Path(tempfile.mkdtemp())

# 1) UF2 검사
good = work / "good.uf2"; good.write_bytes(make_uf2([0xE48BFF57, 0xE48BFF59]))
info = fw.check_uf2(good)
check("RP2350 펌웨어 통과", info["families"] == {"absolute": 4, "rp2350-arm-s": 4}, str(info))
p = work / "pico1.uf2"; p.write_bytes(make_uf2([0xE48BFF56]))
check("피코1(RP2040) 파일 거부", *expect_error(lambda: fw.check_uf2(p), ["피코1"]))
p = work / "broken.uf2"; p.write_bytes(make_uf2([0xE48BFF59], corrupt_block=2))
check("망가진 조각 거부", *expect_error(lambda: fw.check_uf2(p), ["3번째"]))
p = work / "short.uf2"; p.write_bytes(make_uf2([0xE48BFF59])[:-100])
check("덜 받아진 파일 거부", *expect_error(lambda: fw.check_uf2(p), ["크기"]))
p = work / "dataonly.uf2"; p.write_bytes(make_uf2([0xE48BFF58]))
check("프로그램 조각 없는 파일 거부", *expect_error(lambda: fw.check_uf2(p), ["프로그램"]))

# 2) 펌웨어 묶음 (지문)
fwdir = work / "firmware"; fwdir.mkdir()
uf2 = fwdir / "bidguard-hid-pico2-S-0.2.5.uf2"; uf2.write_bytes(good.read_bytes())
meta = {"version": "S-0.2.5", "file": uf2.name, "sha256": hashlib.sha256(uf2.read_bytes()).hexdigest()}
(fwdir / "firmware_version.json").write_text(json.dumps(meta), encoding="utf-8")
check("지문 일치 묶음 통과", fw.load_firmware_package(fwdir)["version"] == "S-0.2.5")
bad = work / "fw_bad"; shutil.copytree(fwdir, bad); (bad / uf2.name).write_bytes(make_uf2([0xE48BFF59], blocks_per_family=5))
check("지문 다르면 거부", *expect_error(lambda: fw.load_firmware_package(bad), ["지문"]))

# 3) 가짜 보드
class FakeBoard(fw.DeviceAccess):
    def __init__(self, root, auto_bootsel=True, reply_after=True, plugged=True):
        self.root = Path(root); self.drive = self.root / "RP2350"
        self.state = "running" if plugged else "absent"
        self.auto_bootsel, self.reply_after = auto_bootsel, reply_after
        self.touched = 0; self.flashed = None
    def _tick(self):
        if self.state == "bootsel":
            uf2s = list(self.drive.glob("*.uf2"))
            if uf2s:
                self.flashed = uf2s[0].read_bytes(); shutil.rmtree(self.drive); self.state = "new"
    def list_pico_ports(self):
        self._tick(); return ["COM7"] if self.state in ("running", "new") else []
    def heartbeat(self, port, timeout=5.0):
        if self.state == "running": return "ACK"
        if self.state == "new": return "ACK" if self.reply_after else ""
        return ""
    def touch_1200(self, port):
        self.touched += 1
        if self.auto_bootsel: self.enter_bootsel()
    def enter_bootsel(self, model="RP2350"):
        self.drive.mkdir(parents=True, exist_ok=True)
        (self.drive / "INFO_UF2.TXT").write_text(f"UF2 Bootloader v1.0\nModel: Raspberry Pi {model}\nBoard-ID: {model}\n")
        self.state = "bootsel"
    def drive_roots(self):
        self._tick(); return [self.drive] if self.drive.exists() else []

def new_updater(board, record, msgs, manual_hook=None):
    t = {"now": 0.0}
    def sleep(s):
        t["now"] += s
        if manual_hook: manual_hook(t["now"])
    return fw.FirmwareUpdater(fwdir, record, device=board, say=lambda k, m: msgs.append((k, m)),
                              sleep=sleep, clock=lambda: t["now"])

# 3-1 정상: 자동 전환
rec = work / "rec1.json"; msgs = []
b = FakeBoard(work / "b1"); u = new_updater(b, rec, msgs)
st = u.inspect()
check("보드 찾고 ACK 확인", st["port"] == "COM7" and st["reply"] == "ACK")
r = u.update(st)
check("자동 전환(1200) 후 펌웨어가 보드에 그대로 들어감", b.touched == 1 and b.flashed == uf2.read_bytes())
check("재시작 후 ACK 확인·설치 기록 저장", r["reply"] == "ACK" and json.loads(rec.read_text())["version"] == "S-0.2.5")
check("다음에는 같은 판 설치 기록으로 알림", u.same_version_installed(u.inspect()))

# 3-2 자동 전환 실패 → BOOTSEL 안내 → 사람이 누름
rec = work / "rec2.json"; msgs = []
b = FakeBoard(work / "b2", auto_bootsel=False)
u = new_updater(b, rec, msgs, manual_hook=lambda now: b.enter_bootsel() if now > 35 and b.state == "running" else None)
r = u.update(u.inspect())
guide = [m for k, m in msgs if k == "guide"]
check("자동 전환 안 되면 BOOTSEL 단추 안내", guide and "BOOTSEL 단추를 누른 채로" in guide[0])
check("사람이 단추 누르면 이어서 완료", r["reply"] == "ACK")

# 3-3 끝까지 BOOTSEL 안 됨
b = FakeBoard(work / "b3", auto_bootsel=False); u = new_updater(b, work / "rec3.json", [])
check("업데이트 모드가 안 나타나면 쉬운 말로 멈춤", *expect_error(lambda: u.update(u.inspect()), ["다른 USB"]))
check("실패하면 설치 기록 안 남김", not (work / "rec3.json").exists())

# 3-4 새 펌웨어 응답 없음
b = FakeBoard(work / "b4", reply_after=False); u = new_updater(b, work / "rec4.json", [])
check("새 펌웨어가 응답 안 하면 완료로 처리 안 함", *expect_error(lambda: u.update(u.inspect()), ["응답하지"]))
check("응답 없으면 설치 기록 안 남김", not (work / "rec4.json").exists())

# 3-5 실제 DeviceAccess 코드: 가짜 pyserial 모듈로 응답 해석·포트 사용 중·부품 없음 시험
import types
fake_serial = types.ModuleType("serial"); fake_tools = types.ModuleType("serial.tools"); fake_lp = types.ModuleType("serial.tools.list_ports")
class SerialException(Exception): pass
class FakePort:
    lines = []; fail = None; opened = []
    def __init__(self, port, baud, **kw):
        FakePort.opened.append((port, baud))
        if FakePort.fail: raise SerialException(FakePort.fail)
        self.buf = list(FakePort.lines); self.dtr = True
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def reset_input_buffer(self): pass
    def write(self, b): self.sent = b
    def readline(self): return (self.buf.pop(0) + "\n").encode() if self.buf else b""
    def close(self): pass
fake_serial.Serial = FakePort; fake_serial.SerialException = SerialException
class P:
    def __init__(self, dev, vid): self.device, self.vid = dev, vid
fake_lp.comports = lambda: [P("COM3", 0x1234), P("COM7", 0x2E8A)]
fake_serial.tools = fake_tools; fake_tools.list_ports = fake_lp
real = fw.DeviceAccess()
saved = {k: sys.modules.get(k) for k in ("serial", "serial.tools", "serial.tools.list_ports")}
sys.modules.update({"serial": fake_serial, "serial.tools": fake_tools, "serial.tools.list_ports": fake_lp})
check("라즈베리파이 USB 장치만 고름 (VID 2E8A)", real.list_pico_ports() == ["COM7"])
FakePort.lines = ["[INFO] System booted normally.", "[INFO] LittleFS total: 1 used: 0", "ACK"]
check("부팅 문구 사이에서 ACK 찾아냄", real.heartbeat("COM7", timeout=1) == "ACK")
FakePort.lines = ["STORAGE_FULL"]
check("저장 공간 가득 응답 구분", real.heartbeat("COM7", timeout=1) == "STORAGE_FULL")
FakePort.lines = []
check("응답 없으면 빈 값", real.heartbeat("COM7", timeout=0.5) == "")
FakePort.fail = "could not open port 'COM7': PermissionError(13, 'Access is denied.', None, 5)"
check("포트 사용 중이면 수집기 닫으라고 안내", *expect_error(lambda: real.heartbeat("COM7"), ["닫고"]))
FakePort.fail = None; FakePort.opened.clear(); real.touch_1200("COM7")
check("업데이트 모드 전환은 1200 보드로 열고 닫음", FakePort.opened == [("COM7", 1200)])
for k, v in saved.items():
    if v is None: sys.modules.pop(k, None)
    else: sys.modules[k] = v
sys.modules["serial"] = None  # 부품 없음 흉내
check("pyserial 부품이 없으면 다시 설치하라고 안내", *expect_error(lambda: fw.DeviceAccess().list_pico_ports(), ["pyserial"]))
sys.modules.pop("serial", None)

# 3-6 피코1 보드가 업데이트 모드로 꽂힘
b = FakeBoard(work / "b6", plugged=False); b.enter_bootsel("RP2040"); u = new_updater(b, work / "rec6.json", [])
check("피코1 보드에는 넣지 않음", *expect_error(lambda: u.inspect(), ["피코1"]))

# 3-7 이미 업데이트 모드로 꽂혀 있음 (보드가 실행 중이 아님)
b = FakeBoard(work / "b7", plugged=False); b.enter_bootsel(); u = new_updater(b, work / "rec7.json", [])
st = u.inspect(); r = u.update(st)
check("이미 업데이트 모드여도 바로 진행", st["bootsel"] and r["reply"] == "ACK" and b.touched == 0)

# 3-8 취소
b = FakeBoard(work / "b8", auto_bootsel=False); msgs = []
u = new_updater(b, work / "rec8.json", msgs, manual_hook=lambda now: setattr(u, "cancelled", True) if now > 30 else None)
check("기다리는 중 취소 가능", *expect_error(lambda: u.update(u.inspect()), ["취소"]))

# 4) 명령줄
sys.modules.update({"serial": fake_serial, "serial.tools": fake_tools, "serial.tools.list_ports": fake_lp}); fake_lp.comports = lambda: []
check("명령줄 검사 모드 (보드 없어도 멈추지 않음)", fw.main(["--check", "--firmware-dir", str(fwdir), "--record", str(work / "c.json")]) == 0)
shutil.rmtree(work, ignore_errors=True)
print("ALL PASS" if ok_all else "SOME FAILED")
