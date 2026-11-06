"""시험용 부품(wheel) 만들기 — 인터넷 없는 시험 환경용
  linux: 이 환경에 이미 설치된 flask·cryptography 등을 그대로 wheel 로 다시 묶음 (실제로 설치·실행 가능한 진짜 부품)
  windows: 이름·판만 있는 빈 부품 (윈도우용 진짜 부품은 묶음 제작 컴퓨터에서 --download 로 받음)
  사용: python make_test_wheels.py <linux_dir> <windows_dir>
"""
import base64, csv, hashlib, io, sys, zipfile, glob, os, shutil
from importlib import metadata
from pathlib import Path

LINUX = ["flask", "werkzeug", "jinja2", "markupsafe", "itsdangerous", "click", "blinker", "cryptography", "cffi", "pycparser"]

def rehash(data):
    return "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()

def repack(name, out):
    d = metadata.distribution(name)
    tag = [l.split(":", 1)[1].strip() for l in d.read_text("WHEEL").splitlines() if l.startswith("Tag:")][0]
    base = Path(d.locate_file(""))
    fname = f"{d.metadata['Name'].replace('-', '_')}-{d.version}-{tag}.whl"
    rows, info_dir = [], None
    with zipfile.ZipFile(out / fname, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.files:
            s = str(f)
            if s.startswith("..") or "__pycache__" in s or s.endswith(".pyc"):
                continue
            if ".dist-info/" in s and s.rsplit("/", 1)[1] in ("RECORD", "INSTALLER", "REQUESTED", "direct_url.json"):
                info_dir = s.rsplit("/", 1)[0]; continue
            p = base / s
            if p.is_file():
                data = p.read_bytes(); z.writestr(s, data); rows.append((s, rehash(data), str(len(data))))
        buf = io.StringIO(); w = csv.writer(buf)
        for r in rows: w.writerow(r)
        w.writerow((f"{info_dir}/RECORD", "", ""))
        z.writestr(f"{info_dir}/RECORD", buf.getvalue())
    return fname

def dummy(name, version, out):
    norm = name.replace("-", "_"); di = f"{norm}-{version}.dist-info"
    fname = f"{norm}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(out / fname, "w") as z:
        z.writestr(f"{norm}_placeholder.py", "# test placeholder\n")
        z.writestr(f"{di}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        z.writestr(f"{di}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr(f"{di}/RECORD", "")
    return fname

if __name__ == "__main__":
    lin, win = Path(sys.argv[1]), Path(sys.argv[2])
    lin.mkdir(parents=True, exist_ok=True); win.mkdir(parents=True, exist_ok=True)
    for n in LINUX: print("linux", repack(n, lin))
    import ensurepip
    pipwhl = glob.glob(os.path.join(os.path.dirname(ensurepip.__file__), "_bundled", "pip-*.whl"))[0]
    shutil.copy2(pipwhl, lin); print("linux", os.path.basename(pipwhl))
    for n, v in [("requests", "2.32.3"), ("python-dotenv", "1.0.1"), ("pyserial", "3.5")]:
        print("windows(빈 부품)", dummy(n, v, win))
