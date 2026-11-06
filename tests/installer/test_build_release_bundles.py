"""묶음 제작기 시험 — 배포 경로 계약(최상위 폴더 1개·진입점·지문)과 막아야 할 경우들
사용: python test_build_release_bundles.py <python-installer.exe> <windows_wheels> <linux_wheels>
"""
import json, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from uf2_test_files import make_uf2
PY, WW, LW = sys.argv[1:4]
BUILD = REPO / "installer/build/build_release_bundles.py"
ok_all = True
def check(label, cond, detail=""):
    global ok_all; ok_all &= bool(cond); print(("PASS" if cond else "FAIL"), label, detail)
def build(out, *extra, py=PY, ww=WW, lw=LW, uf2=None):
    cmd = [sys.executable, str(BUILD), "--out", str(out), "--python-installer", py, "--windows-wheels", ww, "--linux-wheels", lw, "--firmware-uf2", str(uf2)] + list(extra)
    return subprocess.run(cmd, capture_output=True, text=True)
w = Path(tempfile.mkdtemp())
good = w / "fw.uf2"; good.write_bytes(make_uf2([0xE48BFF57, 0xE48BFF59], 8))

r = build(w / "out1", uf2=good)
zips = sorted((w / "out1").glob("*.zip"))
check("묶음 3개 생성", r.returncode == 0 and len(zips) == 3, r.stdout[-300:])
for z in zips:
    names = zipfile.ZipFile(z).namelist(); root = z.stem
    m = json.loads(zipfile.ZipFile(z).read(f"{root}/BUILD-MANIFEST.json"))
    d = json.loads(zipfile.ZipFile(z).read(f"{root}/deployment.json"))
    check(f"{root}: 최상위 폴더 1개 = archive_root, 진입점 있음", {n.split('/')[0] for n in names} == {root} and d["archive_root"] == root and f"{root}/{d['entrypoint']}" in names)
    check(f"{root}: 판 정보", json.loads(zipfile.ZipFile(z).read(f"{root}/version.json"))["label"] in ("S-0.1.0",))
    base = lambda n: n.rstrip("/").rsplit("/", 1)[-1]
    bad = [n for n in names if "__pycache__" in n or "/venv/" in n or base(n) in ("controller.db", "config.json", "settings.json", "install.bat", "run.bat", "credentials_local.json", "apikeys.enc", ".apikey_secret")]
    check(f"{root}: 실행 중 생기는 파일·옛 설치 파일 안 들어감", not bad, str(bad[:3]))
wz = [z for z in zips if "Collector-Windows" in z.name][0]
names = zipfile.ZipFile(wz).namelist()
check("윈도우 묶음: human_input·captcha·펌웨어·업데이트기 포함", all(any(n.endswith(x) for n in names) for x in ("core/human_input.py", "core/captcha.py", "firmware_version.json", "pico2_firmware_updater.py")))
fwinfo = json.loads(zipfile.ZipFile(wz).read(f"{wz.stem}/payload/firmware/firmware_version.json"))
check("펌웨어 판·지문 기록", fwinfo["version"] == "S-0.2.5" and len(fwinfo["sha256"]) == 64)

r = build(w / "out1", uf2=good)
check("이미 있는 묶음은 덮어쓰지 않음 (계약)", r.returncode == 1 and "덮어쓰지" in r.stdout)

pico1 = w / "pico1.uf2"; pico1.write_bytes(make_uf2([0xE48BFF56], 8))
r = build(w / "out2", uf2=pico1)
check("피코1(RP2040) 펌웨어로는 묶음을 안 만듦", r.returncode == 1 and "피코1" in r.stdout and not list((w / "out2").glob("*.zip")))

ww2 = w / "ww2"; shutil.copytree(WW, ww2); [p.unlink() for p in ww2.glob("pyserial*")]
r = build(w / "out3", ww=str(ww2), uf2=good)
check("윈도우 부품이 빠지면 묶음을 안 만듦", r.returncode == 1 and "부품이 모자랍니다" in r.stdout)

lw2 = w / "lw2"; shutil.copytree(LW, lw2); [p.unlink() for p in lw2.glob("cryptography*")]
r = build(w / "out4", "--only", "ubuntu", lw=str(lw2), uf2=good)
check("우분투 부품이 빠지면 묶음을 안 만듦", r.returncode == 1 and "맞지 않습니다" in r.stdout)

wrong = w / "python-3.11.9-amd64.exe"; wrong.write_bytes(b"MZ")
r = build(w / "out5", "--only", "windows", py=str(wrong), uf2=good)
check("파이썬 설치 파일 판이 다르면 안 만듦", r.returncode == 1 and "다릅니다" in r.stdout)

r = subprocess.run([sys.executable, str(BUILD), "--out", str(w / "out6"), "--only", "firmware"], capture_output=True, text=True)
check("펌웨어 없이 펌웨어 묶음을 만들려 하면 안내", r.returncode == 1 and "--firmware-uf2" in r.stdout)
shutil.rmtree(w, ignore_errors=True)
print("ALL PASS" if ok_all else "SOME FAILED")
