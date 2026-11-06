"""
Windows 바로가기 생성 (Python)
한글 경로·OneDrive 바탕화면에서도 동작하도록 COM 사용
"""
import os
import sys
from pathlib import Path

def create_shortcut():
    try:
        import win32com.client
    except ImportError:
        print("[WARN] pywin32 not installed. Trying alternative...")
        return create_with_powershell()

    desktop = Path.home() / "Desktop"
    # OneDrive 바탕화면 대응
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        od_desktop = Path(onedrive) / "Desktop"
        if od_desktop.exists():
            desktop = od_desktop
        # 한글 "바탕 화면"
        od_korean = Path(onedrive) / "사진" / "바탕 화면"
        if od_korean.exists():
            desktop = od_korean

    if not desktop.exists():
        desktop = Path.home() / "Desktop"
    if not desktop.exists():
        desktop = Path.cwd()

    name = os.environ.get("COLLECTOR_NAME", "Collector")
    shortcut_path = desktop / f"Collector_{name}.lnk"
    target = Path.cwd() / "run.bat"
    icon = Path.cwd() / "jiju.ico"

    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(str(shortcut_path))
    shortcut.Targetpath = str(target)
    shortcut.WorkingDirectory = str(Path.cwd())
    if icon.exists():
        shortcut.IconLocation = str(icon)
    shortcut.WindowStyle = 1
    shortcut.Description = f"Court Auction Collector - {name}"
    shortcut.save()

    print(f"[OK] Shortcut created: {shortcut_path}")
    return True


def create_with_powershell():
    """pywin32 없을 때 PowerShell 폴백"""
    import subprocess
    desktop = Path.home() / "Desktop"
    onedrive = os.environ.get("OneDrive")
    if onedrive:
        for p in [Path(onedrive)/"Desktop", Path(onedrive)/"사진"/"바탕 화면"]:
            if p.exists():
                desktop = p
                break
    if not desktop.exists():
        desktop = Path.cwd()

    name = os.environ.get("COLLECTOR_NAME", "Collector")
    shortcut = desktop / f"Collector_{name}.lnk"
    target = Path.cwd() / "run.bat"
    icon = Path.cwd() / "jiju.ico"

    ps = f'''
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut("{shortcut}")
$s.TargetPath = "{target}"
$s.WorkingDirectory = "{Path.cwd()}"
if (Test-Path "{icon}") {{ $s.IconLocation = "{icon},0" }}
$s.WindowStyle = 1
$s.Description = "Court Auction Collector"
$s.Save()
Write-Host "OK"
'''
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    if r.returncode == 0 and shortcut.exists():
        print(f"[OK] Shortcut created: {shortcut}")
        return True
    print(f"[WARN] Failed: {r.stderr}")
    return False


if __name__ == "__main__":
    ok = create_shortcut()
    sys.exit(0 if ok else 1)
