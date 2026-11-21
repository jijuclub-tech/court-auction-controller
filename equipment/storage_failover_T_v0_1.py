"""BIDGUARD storage failover sidecar: staged local source always retained.

This module does NOT run automatically. Integrate only after controller and
collector adapter review. No credentials or personal data belong in GitHub.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str) -> Path:
    raw = value.replace("\\", "/")
    p = PurePosixPath(raw)
    if not raw or p.is_absolute() or ":" in raw or any(part in ("", ".", "..") for part in p.parts):
        raise ValueError("unsafe relative path")
    return Path(*p.parts)


def is_real_remote_mount(root: Path) -> bool:
    """A writable local folder or Windows subst X: is NOT evidence of NAS."""
    root = Path(root)
    if platform.system() == "Windows":
        value = str(root)
        drive = "\\\\" if value.startswith("\\\\") else os.path.splitdrive(value)[0] + "\\"
        return bool(drive) and ctypes.windll.kernel32.GetDriveTypeW(drive) == 4
    if platform.system() != "Linux":
        return False
    try:
        destination = root.resolve()
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    mounts = []
    for line in lines:
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        a, b = left.split(), right.split()
        if len(a) < 6 or not b:
            continue
        mountpoint = Path(a[4].replace("\\040", " "))
        if destination == mountpoint or destination.is_relative_to(mountpoint):
            mounts.append((len(str(mountpoint)), b[0], a[5].split(",")))
    if not mounts:
        return False
    _, fstype, flags = max(mounts)
    return fstype in {"cifs", "smb3", "nfs", "nfs4"} and "ro" not in flags


class StorageSpool:
    """Non-destructive local-first outbox, with NAS and Drive verification."""
    def __init__(self, local_root: Path, nas_root: Path | None = None):
        self.local = Path(local_root)
        self.nas = Path(nas_root) if nas_root else None
        self.local.mkdir(parents=True, exist_ok=True)
        self.audit = self.local / "transfer_events.jsonl"

    def _event(self, rel: Path, state: str, hash_value: str, detail: str = ""):
        row = {
            "at": datetime.now(timezone.utc).isoformat(),
            "relative": rel.as_posix(),
            "state": state,
            "sha256": hash_value,
            "detail": detail[:120],
        }
        with self.audit.open("a", encoding="utf-8") as out:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            os.fsync(out.fileno())

    def stage(self, source: Path, relative_name: str) -> tuple[Path, str]:
        rel = safe_relative(relative_name)
        src = Path(source)
        checksum = sha256(src)
        target = self.local / "files" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256(target) != checksum:
                raise FileExistsError("existing source differs; never overwrite")
        else:
            temp = target.with_name(target.name + ".tmp." + str(os.getpid()))
            with src.open("rb") as inp, temp.open("xb") as out:
                shutil.copyfileobj(inp, out)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, target)
        if sha256(target) != checksum:
            raise IOError("local checksum mismatch")
        self._event(rel, "LOCAL_STAGED", checksum)
        return target, checksum

    def copy_to_nas(self, relative_name: str) -> bool:
        rel = safe_relative(relative_name)
        src = self.local / "files" / rel
        digest = sha256(src)
        if not self.nas or not is_real_remote_mount(self.nas):
            self._event(rel, "NAS_PENDING", digest, "remote mount not verified")
            return False
        target = self.nas / rel
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if sha256(target) != digest:
                    raise FileExistsError("remote path collision")
            else:
                temp = target.with_name(target.name + ".tmp." + str(os.getpid()))
                with src.open("rb") as inp, temp.open("xb") as out:
                    shutil.copyfileobj(inp, out)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temp, target)
            if sha256(target) != digest:
                raise IOError("remote checksum mismatch")
        except OSError as error:
            self._event(rel, "NAS_PENDING", digest, type(error).__name__)
            return False
        self._event(rel, "NAS_VERIFIED", digest)
        return True

    def upload_to_drive(self, relative_name: str, uploader) -> bool:
        """uploader(path, relative, sha256) must verify the REMOTE file.

        Return {"sha256": expected_checksum, "verified": True} only after an
        independent remote checksum/download confirmation. This is an adapter;
        it does not silently use an unconfigured Drive account or token.
        """
        rel = safe_relative(relative_name)
        src = self.local / "files" / rel
        digest = sha256(src)
        try:
            receipt = uploader(src, rel.as_posix(), digest)
            verified = (isinstance(receipt, dict)
                        and receipt.get("sha256") == digest
                        and receipt.get("verified") is True)
        except Exception as error:
            self._event(rel, "DRIVE_PENDING", digest, type(error).__name__)
            return False
        self._event(rel, "DRIVE_VERIFIED" if verified else "DRIVE_PENDING", digest)
        return verified
