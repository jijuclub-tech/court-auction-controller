"""
BIDGUARD 법원경매 수집기·관제기 설치 묶음 제작기  — 판: build_settings.json 의 installer_version

만드는 묶음 (2026-10-03 배포 경로 계약을 따름: ZIP 안 최상위 폴더 1개, version.json·deployment.json·BUILD-MANIFEST.json)
  1. Court-Auction-Collector-Windows-Setup-<판>.zip    수집기 + 피코2 펌웨어 업데이트기 + 펌웨어 + 파이썬 + 부품
  2. Pico2-Firmware-Update-Windows-<펌웨어판>.zip      펌웨어만 새로 나왔을 때 (설치된 수집기의 파이썬을 씀)
  3. Court-Auction-Controller-Ubuntu-Setup-<판>.zip   관제기 + 부품 (파이썬 3.10~3.13 용)

묶음을 만드는 컴퓨터에서 한 번만 인터넷이 필요하다 (파이썬 설치 파일·부품 받기). 설치하는 컴퓨터는 인터넷이 필요 없다.

사용 예
  # 인터넷이 되는 컴퓨터에서 전부 받아서 만들기
  python installer/build/build_release_bundles.py --download --firmware-uf2 path/to/bidguard-hid-pico2.ino.uf2

  # 이미 받아 둔 것으로 만들기 (인터넷 없이)
  python installer/build/build_release_bundles.py --python-installer cache/python-3.12.10-amd64.exe \\
      --windows-wheels cache/win_wheels --linux-wheels cache/linux_wheels --firmware-uf2 fw.uf2

  # 펌웨어를 소스에서 직접 빌드 (arduino-cli 와 arduino-pico 코어 필요)
  ... --arduino-cli arduino-cli --firmware-source ../BIDGUARD-PICO2-FIRMWARE-PRIVATE

기존 RELEASE-STAGE 폴더는 덮어쓰지 않는다 (계약). 같은 판을 다시 만들려면 그 폴더를 먼저 지운다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALLER = REPO / "installer"
SETTINGS = json.loads((INSTALLER / "build" / "build_settings.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(INSTALLER / "windows_firmware"))
import pico2_firmware_updater as fw  # UF2 검사를 같은 코드로 함


class BuildError(Exception):
    pass


def say(msg):
    print(msg, flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "unknown"


def copy_tree(src: Path, dst: Path, exclude=()):
    def ignore(d, names):
        return [n for n in names if n in exclude or n.endswith(".pyc")]
    shutil.copytree(src, dst, ignore=ignore)


# ── 준비물 받기 / 검사 ─────────────────────────────────────────────
def download(url: str, dst: Path):
    say(f"  받는 중: {url}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)


def pip_download(req: Path, dst: Path, platforms, pyver: str):
    cmd = [sys.executable, "-m", "pip", "download", "-q", "--only-binary=:all:", "--python-version", pyver,
           "--implementation", "cp", "-d", str(dst), "-r", str(req)]
    for p in platforms:
        cmd += ["--platform", p]
    say(f"  부품 받는 중: 파이썬 {pyver} / {platforms[0]}")
    subprocess.run(cmd, check=True)


def verify_wheels(wheels: Path, req: Path, platforms, pyver: str):
    """설치하는 컴퓨터와 같은 조건(인터넷 없음)으로 미리 풀어 보아 빠진 부품이 없는지 확인"""
    with tempfile.TemporaryDirectory() as tmp:
        cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--no-index",
               "--find-links", str(wheels), "--only-binary=:all:", "--python-version", pyver, "--implementation", "cp",
               "--target", tmp, "-r", str(req), "-q", "--disable-pip-version-check"]
        for p in platforms:
            cmd += ["--platform", p]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise BuildError(f"부품이 모자랍니다 (파이썬 {pyver}, {platforms[0]}):\n{r.stderr.strip()[-800:]}")


def prepare_firmware(args, work: Path) -> Path:
    if args.firmware_uf2:
        uf2 = Path(args.firmware_uf2)
    elif args.arduino_cli and args.firmware_source:
        sketch = Path(args.firmware_source) / SETTINGS["firmware_sketch"]
        out = work / "fw_build"
        say(f"  펌웨어 빌드: {sketch} ({SETTINGS['firmware_fqbn']})")
        subprocess.run([args.arduino_cli, "compile", "--fqbn", SETTINGS["firmware_fqbn"], "--output-dir", str(out), str(sketch.parent)], check=True)
        found = sorted(out.glob("*.uf2"))
        if not found:
            raise BuildError("펌웨어 빌드 결과에 UF2 파일이 없습니다.")
        uf2 = found[0]
    else:
        raise BuildError("펌웨어가 없습니다. --firmware-uf2 (빌드된 UF2) 또는 --arduino-cli 와 --firmware-source 를 주십시오.")
    if not uf2.exists():
        raise BuildError(f"펌웨어 파일이 없습니다: {uf2}")
    try:
        info = fw.check_uf2(uf2)
    except fw.UpdateError as e:
        raise BuildError(f"펌웨어 파일 검사 실패: {e}")
    say(f"  펌웨어 검사 통과: {info}")
    return uf2


# ── 묶음 만들기 ───────────────────────────────────────────────────
def write_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def finish_bundle(stage_root: Path, kind: str, version_info: dict, out_dir: Path) -> Path:
    b = SETTINGS["bundles"][kind]
    dep = {"archive_root": stage_root.name, "entrypoint": b["entrypoint"], "target_path": b["target_path"], "platform": b["platform"]}
    write_json(stage_root / "deployment.json", dep)
    write_json(stage_root / "version.json", version_info)
    if not (stage_root / b["entrypoint"]).is_file():
        raise BuildError(f"실행 진입점이 없습니다: {b['entrypoint']}")
    files = {}
    for p in sorted(stage_root.rglob("*")):
        if p.is_file() and p.name != "BUILD-MANIFEST.json":
            files[p.relative_to(stage_root).as_posix()] = sha256(p)
    write_json(stage_root / "BUILD-MANIFEST.json", {"generated_at": datetime.now().isoformat(timespec="seconds"),
                                                     "archive_root": stage_root.name, "files": files})
    zpath = out_dir / f"{stage_root.name}.zip"
    if zpath.exists():
        raise BuildError(f"이미 있는 묶음은 덮어쓰지 않습니다: {zpath}")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(stage_root.rglob("*")):
            if p.is_dir():
                continue
            arc = f"{stage_root.name}/{p.relative_to(stage_root).as_posix()}"
            zi = zipfile.ZipInfo(arc, date_time=datetime.now().timetuple()[:6])
            zi.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if p.suffix == ".sh" else 0o644   # 우분투에서 풀었을 때 바로 실행되게
            zi.external_attr = (0o100000 | mode) << 16
            z.writestr(zi, p.read_bytes())
    check_zip(zpath, stage_root.name, b["entrypoint"])
    say(f"  ✔ {zpath.name}  ({zpath.stat().st_size // 1024} KB, 파일 {len(files) + 1}개)")
    return zpath


def check_zip(zpath: Path, root: str, entrypoint: str):
    """계약 재검사: 최상위 폴더 1개, 이중 폴더 없음, 진입점 있음, 지문 일치"""
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        tops = {n.split("/", 1)[0] for n in names}
        if tops != {root}:
            raise BuildError(f"ZIP 최상위 폴더가 하나가 아닙니다: {tops}")
        if any(n.startswith(f"{root}/{root}/") for n in names):
            raise BuildError("ZIP 안에 같은 이름 폴더가 겹쳐 있습니다 (이중 폴더).")
        if f"{root}/{entrypoint}" not in names:
            raise BuildError(f"ZIP 안에 실행 진입점이 없습니다: {entrypoint}")
        m = json.loads(z.read(f"{root}/BUILD-MANIFEST.json"))
        for rel, want in m["files"].items():
            if hashlib.sha256(z.read(f"{root}/{rel}")).hexdigest() != want:
                raise BuildError(f"ZIP 안 파일 지문이 다릅니다: {rel}")
        extra = {n[len(root) + 1:] for n in names} - set(m["files"]) - {"BUILD-MANIFEST.json"}
        if extra:
            raise BuildError(f"지문 목록에 없는 파일이 ZIP 에 있습니다: {sorted(extra)[:5]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="BIDGUARD 설치 묶음 제작기")
    ap.add_argument("--out", default=str(REPO / "RELEASE-STAGE"))
    ap.add_argument("--only", choices=["all", "windows", "firmware", "ubuntu"], default="all")
    ap.add_argument("--download", action="store_true", help="파이썬 설치 파일·부품을 인터넷에서 받음")
    ap.add_argument("--cache", default=str(REPO / "RELEASE-CACHE"), help="받은 것을 두는 곳")
    ap.add_argument("--python-installer")
    ap.add_argument("--windows-wheels")
    ap.add_argument("--linux-wheels")
    ap.add_argument("--firmware-uf2")
    ap.add_argument("--arduino-cli")
    ap.add_argument("--firmware-source")
    a = ap.parse_args(argv)

    ver = SETTINGS["installer_version"]; fwver = SETTINGS["firmware_version"]
    out_dir = Path(a.out); stage_dir = out_dir / "stage"
    cache = Path(a.cache)
    commit = git_commit(); built = datetime.now().isoformat(timespec="seconds")
    want = {"windows", "firmware", "ubuntu"} if a.only == "all" else {a.only}
    made = []
    try:
        with tempfile.TemporaryDirectory() as work:
            work = Path(work)
            stage_dir.mkdir(parents=True, exist_ok=True)
            win_req = INSTALLER / "windows_collector" / "collector_requirements.txt"
            lin_req = REPO / "controller" / "requirements.txt"

            # 준비물
            py_exe = None
            if "windows" in want:
                pyset = SETTINGS["windows_python"]
                py_exe = Path(a.python_installer) if a.python_installer else cache / pyset["file"]
                if not py_exe.exists():
                    if not a.download:
                        raise BuildError(f"파이썬 설치 파일이 없습니다: {py_exe} (--download 또는 --python-installer)")
                    download(pyset["url"], py_exe)
                if py_exe.name != pyset["file"]:
                    raise BuildError(f"파이썬 설치 파일 이름이 설정과 다릅니다: {py_exe.name} ≠ {pyset['file']}")
                py_sha = sha256(py_exe)
                if pyset["sha256"] and py_sha != pyset["sha256"].lower():
                    raise BuildError("파이썬 설치 파일 지문이 설정값과 다릅니다. 받은 파일을 지우고 다시 받으십시오.")
                win_wheels = Path(a.windows_wheels) if a.windows_wheels else cache / "windows_wheels"
                if a.download and not a.windows_wheels:
                    pip_download(win_req, win_wheels, ["win_amd64"], SETTINGS["windows_python"]["minor"])
                verify_wheels(win_wheels, win_req, ["win_amd64"], SETTINGS["windows_python"]["minor"])
                say("  ✔ 윈도우 부품 빠짐없음 (인터넷 없이 설치 시험 통과)")
            if "ubuntu" in want:
                lin_wheels = Path(a.linux_wheels) if a.linux_wheels else cache / "linux_wheels"
                if a.download and not a.linux_wheels:
                    for v in SETTINGS["linux_python_versions"]:
                        pip_download(lin_req, lin_wheels, SETTINGS["linux_platforms"], v)
                    subprocess.run([sys.executable, "-m", "pip", "download", "-q", "--only-binary=:all:", "--no-deps", "-d", str(lin_wheels), "pip"], check=True)
                if not list(lin_wheels.glob("pip-*.whl")):
                    raise BuildError("우분투 묶음에 pip 부품(pip-*.whl)이 없습니다.")
                checked = []
                for v in SETTINGS["linux_python_versions"]:
                    try:
                        verify_wheels(lin_wheels, lin_req, SETTINGS["linux_platforms"], v); checked.append(v)
                    except BuildError:
                        if a.download:
                            raise
                if not checked:
                    raise BuildError("우분투 부품이 어느 파이썬 판에도 맞지 않습니다.")
                say(f"  ✔ 우분투 부품 빠짐없음 (파이썬 {', '.join(checked)})")
            uf2 = prepare_firmware(a, work) if want & {"windows", "firmware"} else None

            def firmware_payload(dst: Path):
                dst.mkdir(parents=True)
                name = f"bidguard-hid-pico2-{fwver}.uf2"
                shutil.copy2(uf2, dst / name)
                write_json(dst / "firmware_version.json", {"version": fwver, "file": name, "sha256": sha256(dst / name),
                                                           "source": SETTINGS["firmware_repository"], "fqbn": SETTINGS["firmware_fqbn"],
                                                           "uf2": fw.check_uf2(dst / name)})

            base_info = {"label": ver, "stage": ver.split("-")[0], "version": ver.split("-", 1)[1], "built_at": built,
                         "source_commit": commit, "firmware_version": fwver}

            if "windows" in want:
                root = stage_dir / SETTINGS["bundles"]["windows_collector"]["archive_root"].format(version=ver)
                if root.exists():
                    raise BuildError(f"이미 있는 RELEASE-STAGE 폴더는 덮어쓰지 않습니다: {root}")
                wc = INSTALLER / "windows_collector"
                root.mkdir(parents=True)
                for f in ("Install_Court_Auction_Collector.bat", "Update_Pico2_Firmware.bat", "READ_ME_FIRST.txt"):
                    shutil.copy2(wc / f, root / f)
                (root / "installer").mkdir()
                for f in ("Court_Auction_Collector_Installer.ps1", "Court_Auction_Collector_Uninstaller.ps1", "installer_settings.json"):
                    shutil.copy2(wc / f, root / "installer" / f)
                pre = root / "prerequisites"; pre.mkdir()
                shutil.copy2(py_exe, pre / py_exe.name)
                shutil.copy2(win_req, pre / win_req.name)
                copy_tree(win_wheels, pre / "wheels")
                copy_tree(REPO / "new_collector", root / "payload" / "collector", SETTINGS["exclude_from_collector_payload"])
                (root / "payload" / "firmware_updater").mkdir(parents=True)
                shutil.copy2(INSTALLER / "windows_firmware" / "pico2_firmware_updater.py", root / "payload" / "firmware_updater")
                firmware_payload(root / "payload" / "firmware")
                info = dict(base_info, product="BIDGUARD_Court_Auction_Collector", python=SETTINGS["windows_python"]["version"], python_sha256=py_sha)
                made.append(finish_bundle(root, "windows_collector", info, out_dir))

            if "firmware" in want:
                root = stage_dir / SETTINGS["bundles"]["windows_firmware"]["archive_root"].format(firmware_version=fwver)
                if root.exists():
                    raise BuildError(f"이미 있는 RELEASE-STAGE 폴더는 덮어쓰지 않습니다: {root}")
                root.mkdir(parents=True)
                shutil.copy2(INSTALLER / "windows_collector" / "Update_Pico2_Firmware.bat", root)
                (root / "payload" / "firmware_updater").mkdir(parents=True)
                shutil.copy2(INSTALLER / "windows_firmware" / "pico2_firmware_updater.py", root / "payload" / "firmware_updater")
                firmware_payload(root / "payload" / "firmware")
                (root / "READ_ME_FIRST.txt").write_bytes(b"\xef\xbb\xbf" + (
                    f"피코2 펌웨어 업데이트 {fwver}\r\n\r\n"
                    "1. 압축을 끝까지 풉니다.\r\n2. 수집기를 닫고 피코2를 USB로 꽂습니다.\r\n"
                    "3. Update_Pico2_Firmware.bat 더블클릭 → [업데이트 시작]\r\n"
                    "   자동 전환이 안 되면 화면 안내대로 BOOTSEL(흰 단추)을 누른 채 USB를 다시 꽂습니다.\r\n\r\n"
                    "수집기가 먼저 설치되어 있어야 합니다 (수집기 설치에 들어 있는 파이썬으로 돕니다).\r\n").encode("utf-8"))
                info = dict(base_info, product="BIDGUARD_Pico2_Firmware_Update")
                made.append(finish_bundle(root, "windows_firmware", info, out_dir))

            if "ubuntu" in want:
                root = stage_dir / SETTINGS["bundles"]["ubuntu_controller"]["archive_root"].format(version=ver)
                if root.exists():
                    raise BuildError(f"이미 있는 RELEASE-STAGE 폴더는 덮어쓰지 않습니다: {root}")
                uc = INSTALLER / "ubuntu_controller"
                root.mkdir(parents=True)
                shutil.copy2(uc / "install_court_auction_controller.sh", root)
                shutil.copy2(uc / "READ_ME_FIRST.txt", root)
                (root / "installer").mkdir()
                shutil.copy2(uc / "uninstall_court_auction_controller.sh", root / "installer")
                pre = root / "prerequisites"; pre.mkdir()
                shutil.copy2(lin_req, pre / "controller_requirements.txt")
                copy_tree(lin_wheels, pre / "wheels")
                copy_tree(REPO / "controller", root / "payload" / "controller", SETTINGS["exclude_from_controller_payload"])
                info = dict(base_info, product="BIDGUARD_Court_Auction_Controller", python_versions=SETTINGS["linux_python_versions"])
                made.append(finish_bundle(root, "ubuntu_controller", info, out_dir))
    except (BuildError, subprocess.CalledProcessError) as e:
        say(f"✖ 묶음 만들기 멈춤: {e}")
        return 1
    say(f"완료: {len(made)}개 묶음 → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
