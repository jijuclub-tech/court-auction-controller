"""
BIDGUARD 피코2(RP2350) 펌웨어 업데이트기  — 판: S-0.1.0

하는 일 (사람이 할 일은 "업데이트 시작" 한 번, 안 되면 BOOTSEL 단추 한 번)
  1. 넣을 펌웨어 파일(UF2)이 RP2350용이 맞는지, 지문(SHA256)이 기록과 같은지 검사
  2. USB로 꽂힌 피코2를 찾아 HEARTBEAT → ACK 로 살아 있는지 확인
  3. 보드를 '업데이트 모드(BOOTSEL)'로 자동 전환 (1200 보드 신호)
     → 안 되면 화면에 BOOTSEL 단추 누르는 법을 그림처럼 안내하고 기다림
  4. 업데이트 모드에서 나타나는 RP2350 드라이브에 펌웨어 복사
  5. 보드가 다시 켜지면 HEARTBEAT → ACK 로 새 펌웨어가 도는지 확인
  6. 이 PC에 설치 기록(판·지문·시각·포트) 저장

펌웨어 코드에는 판을 물어보는 명령이 없어서, 판은 이 PC의 설치 기록으로 관리한다.
펌웨어 소스(bidguard-hid-pico2.ino)는 이 프로그램이 건드리지 않는다.

사용
  화면:     python pico2_firmware_updater.py
  명령줄:   python pico2_firmware_updater.py --check            (검사만)
            python pico2_firmware_updater.py --update --yes     (묻지 않고 업데이트)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import string
import struct
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

UPDATER_VERSION = "S-0.1.0"
HERE = Path(__file__).resolve().parent
DEFAULT_FIRMWARE_DIR = HERE / "firmware"
DEFAULT_RECORD = HERE.parent / "firmware_install_record.json"

RASPBERRY_PI_USB_VID = 0x2E8A
HEARTBEAT_BAUD = 115200
TOUCH_BAUD = 1200  # arduino-pico: 1200 보드로 열었다 닫으면 업데이트 모드로 재시작

# ── UF2 형식 ─────────────────────────────────────────────────────
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
UF2_FLAG_FAMILY_ID = 0x00002000
FAMILY_RP2040 = 0xE48BFF56
FAMILY_RP2350 = {
    0xE48BFF57: "absolute",
    0xE48BFF58: "data",
    0xE48BFF59: "rp2350-arm-s",
    0xE48BFF5A: "rp2350-riscv",
    0xE48BFF5B: "rp2350-arm-ns",
}
FAMILY_RP2350_PROGRAM = {0xE48BFF59, 0xE48BFF5A, 0xE48BFF5B}


class UpdateError(Exception):
    """사람에게 그대로 보여 줄 수 있는 쉬운 말의 오류"""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_uf2(path: Path) -> Dict:
    """UF2 파일이 망가지지 않았고 RP2350(피코2)용인지 검사. 문제가 있으면 UpdateError"""
    data = path.read_bytes()
    if not data or len(data) % 512:
        raise UpdateError(f"펌웨어 파일 크기가 이상합니다 ({len(data)}바이트). 파일이 덜 받아졌거나 망가졌습니다.")
    families: Dict[int, int] = {}
    for i in range(0, len(data), 512):
        block = data[i:i + 512]
        m0, m1, flags, _addr, size, _no, _num, fam = struct.unpack_from("<8I", block, 0)
        (mend,) = struct.unpack_from("<I", block, 508)
        if m0 != UF2_MAGIC_START0 or m1 != UF2_MAGIC_START1 or mend != UF2_MAGIC_END:
            raise UpdateError(f"펌웨어 파일 {i // 512 + 1}번째 조각이 망가졌습니다. 새로 받은 파일을 쓰십시오.")
        if size > 476:
            raise UpdateError("펌웨어 파일 안의 조각 크기가 규격을 넘습니다.")
        if flags & UF2_FLAG_FAMILY_ID:
            families[fam] = families.get(fam, 0) + 1
    if FAMILY_RP2040 in families:
        raise UpdateError("이 파일은 피코1(RP2040)용입니다. 피코2(RP2350)에는 넣을 수 없습니다.")
    unknown = [hex(f) for f in families if f not in FAMILY_RP2350]
    if unknown:
        raise UpdateError(f"피코2용이 아닌 펌웨어 파일입니다 (종류 {', '.join(unknown)}).")
    if not any(f in FAMILY_RP2350_PROGRAM for f in families):
        raise UpdateError("피코2에서 실행되는 프로그램 조각이 없는 파일입니다.")
    return {"blocks": len(data) // 512, "bytes": len(data),
            "families": {FAMILY_RP2350[f]: n for f, n in families.items()}}


def load_firmware_package(firmware_dir: Path) -> Dict:
    """firmware_version.json + UF2 를 읽고 지문까지 검사"""
    info_path = firmware_dir / "firmware_version.json"
    if not info_path.exists():
        raise UpdateError(f"펌웨어 정보 파일이 없습니다: {info_path}")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    uf2 = firmware_dir / info["file"]
    if not uf2.exists():
        raise UpdateError(f"펌웨어 파일이 없습니다: {uf2.name}")
    actual = sha256_of(uf2)
    if actual != info["sha256"].lower():
        raise UpdateError("펌웨어 파일의 지문이 기록과 다릅니다. 파일이 바뀌었거나 망가졌습니다. 설치 묶음을 다시 받으십시오.")
    info["path"] = str(uf2)
    info["uf2"] = check_uf2(uf2)
    return info


def read_record(record_path: Path) -> Dict:
    try:
        return json.loads(Path(record_path).read_text(encoding="utf-8"))
    except Exception:
        return {}


# ── 장치 접근 (시험 때 가짜로 바꿀 수 있게 묶어 둠) ─────────────────
class DeviceAccess:
    def list_pico_ports(self) -> List[str]:
        try:
            from serial.tools import list_ports
        except ImportError:
            raise UpdateError("USB 시리얼 부품(pyserial)이 설치되어 있지 않습니다. 수집기 설치 프로그램을 다시 실행하십시오.")
        return [p.device for p in list_ports.comports() if p.vid == RASPBERRY_PI_USB_VID]

    def heartbeat(self, port: str, timeout: float = 5.0) -> str:
        """'ACK' / 'STORAGE_FULL' / '' (응답 없음). 포트를 다른 프로그램이 쓰면 UpdateError"""
        import serial
        try:
            with serial.Serial(port, HEARTBEAT_BAUD, timeout=0.3, write_timeout=2) as s:
                time.sleep(0.3)
                s.reset_input_buffer()
                s.write(b"HEARTBEAT\n")
                end = time.time() + timeout
                while time.time() < end:
                    line = s.readline().decode("utf-8", "replace").strip()
                    if line in ("ACK", "STORAGE_FULL"):
                        return line
        except serial.SerialException as e:
            if "denied" in str(e).lower() or "busy" in str(e).lower() or "PermissionError" in repr(e):
                raise UpdateError(f"{port} 포트를 다른 프로그램이 쓰고 있습니다. 수집기·시리얼 모니터를 모두 닫고 다시 누르십시오.")
            return ""
        return ""

    def touch_1200(self, port: str) -> None:
        import serial
        try:
            s = serial.Serial(port, TOUCH_BAUD)
            s.dtr = False
            s.close()
        except Exception:
            pass  # 보드가 곧바로 재시작하며 끊기는 것은 정상

    def drive_roots(self) -> List[Path]:
        if os.name == "nt":
            return [Path(f"{c}:\\") for c in string.ascii_uppercase[3:]]
        roots = []
        for base in ("/media", "/run/media", "/Volumes"):
            p = Path(base)
            if p.exists():
                roots += [d for d in p.glob("*/*") if d.is_dir()] + [d for d in p.glob("*") if d.is_dir()]
        return roots


def find_bootsel_drive(roots: List[Path]) -> Optional[Path]:
    """INFO_UF2.TXT 가 있는 드라이브를 찾음. 피코1 드라이브면 UpdateError"""
    for r in roots:
        info = Path(r) / "INFO_UF2.TXT"
        try:
            if not info.exists():
                continue
            text = info.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "RP2350" in text:
            return Path(r)
        if "RP2040" in text:
            raise UpdateError(f"{r} 는 피코1(RP2040) 보드입니다. 피코2(RP2350) 보드를 꽂으십시오.")
    return None


class FirmwareUpdater:
    """업데이트 순서 전체. 화면·명령줄이 같은 순서를 씀"""

    def __init__(self, firmware_dir: Path = DEFAULT_FIRMWARE_DIR, record_path: Path = DEFAULT_RECORD,
                 device: Optional[DeviceAccess] = None, say: Callable[[str, str], None] = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time,
                 auto_wait: float = 20, manual_wait: float = 180, return_wait: float = 40):
        self.firmware_dir = Path(firmware_dir)
        self.record_path = Path(record_path)
        self.device = device or DeviceAccess()
        self.say = say or (lambda kind, msg: print(f"[{kind}] {msg}"))
        self.sleep, self.clock = sleep, clock
        self.auto_wait, self.manual_wait, self.return_wait = auto_wait, manual_wait, return_wait
        self.cancelled = False

    # 1~2
    def inspect(self) -> Dict:
        pkg = load_firmware_package(self.firmware_dir)
        self.say("ok", f"넣을 펌웨어: {pkg['version']} ({pkg['uf2']['blocks']}조각, 지문 확인됨)")
        record = read_record(self.record_path)
        if record:
            self.say("info", f"이 PC 기록상 마지막 설치: {record.get('version', '?')} ({record.get('installed_at', '')[:16].replace('T', ' ')})")
        else:
            self.say("info", "이 PC에는 아직 펌웨어 설치 기록이 없습니다.")
        ports = self.device.list_pico_ports()
        status = {"package": pkg, "record": record, "ports": ports, "port": None, "reply": "", "bootsel": None}
        status["bootsel"] = find_bootsel_drive(self.device.drive_roots())
        if status["bootsel"]:
            self.say("ok", f"보드가 이미 업데이트 모드입니다 (드라이브 {status['bootsel']}).")
        elif not ports:
            self.say("warn", "피코2가 보이지 않습니다. USB 선이 꽂혀 있는지 확인하십시오. (충전 전용 선은 안 됩니다)")
        for p in ports:
            reply = self.device.heartbeat(p)
            if reply:
                status["port"], status["reply"] = p, reply
                self.say("ok", f"피코2 연결됨: {p} → {reply}")
                if reply == "STORAGE_FULL":
                    self.say("warn", "보드 저장 공간이 가득 찬 상태입니다 (펌웨어 업데이트와는 별개).")
                break
        if ports and not status["port"]:
            status["port"] = ports[0]
            self.say("warn", f"{ports[0]} 에 보드가 있지만 응답이 없습니다. 업데이트는 진행할 수 있습니다.")
        return status

    def same_version_installed(self, status: Dict) -> bool:
        rec = status.get("record") or {}
        return rec.get("version") == status["package"]["version"] and rec.get("sha256") == status["package"]["sha256"]

    def _wait(self, cond, seconds: float, tick: Optional[Callable[[int], None]] = None):
        end = self.clock() + seconds
        while self.clock() < end:
            if self.cancelled:
                raise UpdateError("사용자가 취소했습니다.")
            r = cond()
            if r:
                return r
            if tick:
                tick(int(end - self.clock()))
            self.sleep(0.5)
        return None

    # 3~6
    def update(self, status: Dict) -> Dict:
        pkg = status["package"]
        drive = status.get("bootsel") or find_bootsel_drive(self.device.drive_roots())
        if not drive and status.get("port"):
            self.say("step", f"보드를 업데이트 모드로 바꾸는 중 ({status['port']})...")
            self.device.touch_1200(status["port"])
            drive = self._wait(lambda: find_bootsel_drive(self.device.drive_roots()), self.auto_wait)
        if not drive:
            self.say("guide", "자동 전환이 안 됐습니다. 아래처럼 해 주십시오:\n"
                              "  ① 피코2의 USB 선을 뽑습니다.\n"
                              "  ② 보드 가운데 흰 BOOTSEL 단추를 누른 채로 USB 선을 다시 꽂습니다.\n"
                              "  ③ 2초 뒤 단추에서 손을 뗍니다. (RP2350 드라이브가 열리면 자동으로 이어집니다)")
            drive = self._wait(lambda: find_bootsel_drive(self.device.drive_roots()), self.manual_wait,
                               tick=lambda left: self.say("wait", f"BOOTSEL 기다리는 중... {left}초 남음"))
        if not drive:
            raise UpdateError("업데이트 모드 드라이브(RP2350)가 나타나지 않았습니다. 다른 USB 선이나 다른 USB 구멍으로 다시 해 보십시오.")
        self.say("ok", f"업데이트 모드 확인: {drive}")

        src = Path(pkg["path"])
        dst = Path(drive) / src.name
        self.say("step", f"펌웨어 넣는 중... ({pkg['uf2']['bytes'] // 1024}KB)")
        try:
            with open(src, "rb") as fi, open(dst, "wb") as fo:
                fo.write(fi.read())
                fo.flush()
                try:
                    os.fsync(fo.fileno())
                except OSError:
                    pass  # 다 쓰는 순간 보드가 재시작하며 드라이브가 사라지면 생기는 정상 현상
        except OSError as e:
            if Path(drive).exists() and (Path(drive) / "INFO_UF2.TXT").exists():
                raise UpdateError(f"펌웨어를 드라이브에 쓰지 못했습니다: {e}")
        self.say("step", "보드가 새 펌웨어로 다시 켜지는 중...")
        self._wait(lambda: not (Path(drive) / "INFO_UF2.TXT").exists(), 20)
        port = self._wait(lambda: (self.device.list_pico_ports() or [None])[0], self.return_wait)
        if not port:
            raise UpdateError("보드가 다시 연결되지 않았습니다. USB 선을 뽑았다가 다시 꽂고 '다시 찾기'를 누르십시오.")
        reply = ""
        for _ in range(5):
            self.sleep(2)
            reply = self.device.heartbeat(port)
            if reply:
                break
        if not reply:
            raise UpdateError(f"새 펌웨어가 응답하지 않습니다 ({port}). USB 선을 다시 꽂고 '다시 찾기'로 확인하십시오.")
        record = {
            "version": pkg["version"], "sha256": pkg["sha256"], "file": src.name,
            "installed_at": datetime.now().isoformat(timespec="seconds"),
            "port": port, "reply": reply, "updater": UPDATER_VERSION,
        }
        self.record_path.parent.mkdir(parents=True, exist_ok=True)
        self.record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        self.say("done", f"완료: 피코2 펌웨어 {pkg['version']} 설치 확인 ({port} → {reply})")
        if reply == "STORAGE_FULL":
            self.say("warn", "펌웨어는 정상이지만 보드 저장 공간이 가득 찼다고 답합니다. 관리자에게 알리십시오.")
        return record


# ── 화면 ───────────────────────────────────────────────────────
def run_gui(updater_factory):
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.title("BIDGUARD 피코2 펌웨어 업데이트")
    root.geometry("640x520")
    root.minsize(560, 460)
    font = ("Malgun Gothic", 10)
    events: "queue.Queue" = queue.Queue()

    tk.Label(root, text="피코2 펌웨어 업데이트", font=("Malgun Gothic", 15, "bold"), fg="#0f5c37").pack(anchor="w", padx=16, pady=(14, 2))
    tk.Label(root, text="수집기를 닫고, 피코2를 USB로 꽂은 뒤 '업데이트 시작'을 누르십시오.\n"
                        "자동으로 안 되면 화면에 나오는 대로 BOOTSEL 단추를 한 번 눌러 주시면 됩니다.",
             font=font, justify="left", fg="#444").pack(anchor="w", padx=16)
    info_var = tk.StringVar(value="확인 중...")
    tk.Label(root, textvariable=info_var, font=("Malgun Gothic", 11, "bold"), justify="left").pack(anchor="w", padx=16, pady=8)
    log = tk.Text(root, height=14, font=font, wrap="word", bg="#f6f9f7", relief="flat")
    log.pack(fill="both", expand=True, padx=16)
    for tag, color in (("ok", "#11623a"), ("warn", "#9a5b00"), ("error", "#a12a2a"), ("guide", "#1d4f91"), ("done", "#11623a")):
        log.tag_config(tag, foreground=color)
    btns = tk.Frame(root)
    btns.pack(fill="x", padx=16, pady=12)
    start_btn = tk.Button(btns, text="업데이트 시작", font=("Malgun Gothic", 11, "bold"), bg="#167a4a", fg="white", width=14)
    again_btn = tk.Button(btns, text="다시 찾기", font=font, width=10)
    cancel_btn = tk.Button(btns, text="취소", font=font, width=8, state="disabled")
    close_btn = tk.Button(btns, text="닫기", font=font, width=8, command=root.destroy)
    for b in (start_btn, again_btn, cancel_btn):
        b.pack(side="left", padx=(0, 8))
    close_btn.pack(side="right")

    state = {"status": None, "updater": None, "busy": False}

    def say(kind, msg):
        events.put((kind, msg))

    def pump():
        try:
            while True:
                kind, msg = events.get_nowait()
                if kind == "wait":
                    info_var.set(msg)
                    continue
                if kind == "__status__":
                    st = msg
                    pkg = st["package"]
                    rec = st.get("record") or {}
                    board = f"{st['port']} ({st['reply'] or '응답 없음'})" if st.get("port") else ("업데이트 모드" if st.get("bootsel") else "찾지 못함")
                    info_var.set(f"넣을 판: {pkg['version']}    기록상 설치 판: {rec.get('version', '없음')}\n보드: {board}")
                    continue
                if kind == "__finished__":
                    state["busy"] = False
                    for b in (start_btn, again_btn):
                        b.config(state="normal")
                    cancel_btn.config(state="disabled")
                    continue
                log.insert("end", msg + "\n", kind)
                log.see("end")
        except queue.Empty:
            pass
        root.after(150, pump)

    def work(do_update):
        upd = updater_factory(say)
        state["updater"] = upd
        try:
            st = upd.inspect()
            state["status"] = st
            events.put(("__status__", st))
            if do_update:
                upd.update(st)
                events.put(("__status__", upd.inspect()))
        except UpdateError as e:
            say("error", f"멈춤: {e}")
        except Exception as e:  # 예상 못 한 오류도 화면에 남김
            say("error", f"예상하지 못한 오류: {e}")
        finally:
            events.put(("__finished__", None))

    def begin(do_update):
        if state["busy"]:
            return
        if do_update and state["status"]:
            st = state["status"]
            upd = state["updater"]
            if upd and upd.same_version_installed(st):
                if not messagebox.askyesno("같은 판", f"이 PC 기록상 {st['package']['version']} 이(가) 이미 설치되어 있습니다.\n그래도 다시 넣을까요?"):
                    return
        state["busy"] = True
        for b in (start_btn, again_btn):
            b.config(state="disabled")
        cancel_btn.config(state="normal" if do_update else "disabled")
        log.insert("end", ("\n── 업데이트 시작 ──\n" if do_update else "\n── 보드 찾기 ──\n"))
        threading.Thread(target=work, args=(do_update,), daemon=True).start()

    def cancel():
        if state["updater"]:
            state["updater"].cancelled = True

    start_btn.config(command=lambda: begin(True))
    again_btn.config(command=lambda: begin(False))
    cancel_btn.config(command=cancel)
    root.after(150, pump)
    root.after(300, lambda: begin(False))
    root.mainloop()


def main(argv=None):
    ap = argparse.ArgumentParser(description="BIDGUARD 피코2 펌웨어 업데이트기")
    ap.add_argument("--firmware-dir", default=str(DEFAULT_FIRMWARE_DIR))
    ap.add_argument("--record", default=str(DEFAULT_RECORD))
    ap.add_argument("--check", action="store_true", help="검사만 하고 끝냄")
    ap.add_argument("--update", action="store_true", help="화면 없이 업데이트")
    ap.add_argument("--yes", action="store_true", help="같은 판이어도 묻지 않고 진행")
    a = ap.parse_args(argv)

    def factory(say=None):
        return FirmwareUpdater(Path(a.firmware_dir), Path(a.record), say=say)

    if not (a.check or a.update):
        run_gui(factory)
        return 0
    upd = factory()
    try:
        st = upd.inspect()
        if a.check:
            return 0
        if upd.same_version_installed(st) and not a.yes:
            print("[info] 같은 판이 이미 설치된 기록이 있습니다. 다시 넣으려면 --yes 를 붙이십시오.")
            return 0
        upd.update(st)
        return 0
    except UpdateError as e:
        print(f"[error] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
