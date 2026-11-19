# -*- coding: utf-8 -*-
"""비밀값(AI 키·법원 로그인·DB 서버 로그인) 입력·보관.

- 실행할 때 사람이 직접 입력한다(getpass: 화면에 글자가 안 보임).
- 기본은 메모리에만 둔다. 평문 파일로는 절대 저장하지 않는다.
- 선택: Windows DPAPI(현재 Windows 사용자 전용 암호화)로만 노트북에 저장할 수 있다.
  저장 위치는 수집기 폴더·공용 폴더 밖인 %LOCALAPPDATA%\\BIDGUARD\\standalone_secrets\\ 이다.
- 값은 repr/str/로그에 나오지 않는다(Secret 객체, Redactor).
"""
from __future__ import annotations

import base64
import getpass
import json
import os
import sys

NAMES = {
    "DEEPSEEK_API_KEY": "딥시크 API 키",
    "GEMINI_API_KEY": "제미나이 API 키",
    "COURT_LOGIN_ID": "법원 아이디",
    "COURT_LOGIN_PW": "법원 비밀번호",
    "KB_SERVER_USER": "DB 서버 아이디(대표가 정함)",
    "KB_SERVER_PASSWORD": "DB 서버 비밀번호(대표가 정함)",
    "DATA_KEY": "자료 암호화 키(2026-12-01 갱신용, crypto.key_source=dpapi 일 때만)",
}
SECRET_WORDS = ("key", "pw", "password", "passwd", "secret", "token", "login_id", "authorization", "cookie")


class Secret:
    """값을 감싸서 실수로 출력돼도 *** 만 보이게 한다."""
    __slots__ = ("_v",)

    def __init__(self, v: str):
        self._v = str(v)

    def reveal(self) -> str:
        return self._v

    def __bool__(self):
        return bool(self._v)

    def __repr__(self):
        return "Secret(***)"

    __str__ = __repr__


# ---------------- Windows DPAPI (ctypes, 무료·표준 기능) ----------------
class _DPAPI:
    CRYPTPROTECT_UI_FORBIDDEN = 0x01

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes

        class BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
        self.BLOB = BLOB
        self.crypt32 = ctypes.windll.crypt32
        self.kernel32 = ctypes.windll.kernel32

    def _blob(self, data: bytes):
        buf = self.ct.create_string_buffer(data, len(data))
        return self.BLOB(len(data), self.ct.cast(buf, self.ct.POINTER(self.ct.c_char))), buf

    def _out(self, blob) -> bytes:
        try:
            return self.ct.string_at(blob.pbData, blob.cbData)
        finally:
            self.kernel32.LocalFree(blob.pbData)

    def protect(self, data: bytes) -> bytes:
        bin_, _keep = self._blob(data)
        out = self.BLOB()
        ok = self.crypt32.CryptProtectData(self.ct.byref(bin_), "BIDGUARD", None, None, None,
                                           self.CRYPTPROTECT_UI_FORBIDDEN, self.ct.byref(out))
        if not ok:
            raise OSError("CryptProtectData failed")
        return self._out(out)

    def unprotect(self, data: bytes) -> bytes:
        bin_, _keep = self._blob(data)
        out = self.BLOB()
        ok = self.crypt32.CryptUnprotectData(self.ct.byref(bin_), None, None, None, None,
                                             self.CRYPTPROTECT_UI_FORBIDDEN, self.ct.byref(out))
        if not ok:
            raise OSError("CryptUnprotectData failed")
        return self._out(out)


def default_dpapi():
    if sys.platform != "win32":
        return None
    try:
        return _DPAPI()
    except Exception:  # noqa: BLE001
        return None


def default_store_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "BIDGUARD", "standalone_secrets", "secrets.dpapi.json")


class SecretStore:
    """get(name) 순서: 이번 실행 메모리 → (있으면) DPAPI 저장본 → 사람에게 입력받기.

    prompt_fn 은 글자가 안 보이는 입력(getpass). ask_fn 은 예/아니오 질문.
    dpapi 가 None 이면(윈도우 아님/시험) 저장하지 않고 메모리에만 둔다.
    """

    def __init__(self, path=None, dpapi="auto", prompt_fn=None, ask_fn=None, allow_save=True):
        self.path = path or default_store_path()
        self.dpapi = default_dpapi() if dpapi == "auto" else dpapi
        self.prompt = prompt_fn or (lambda label: getpass.getpass(label + " 입력(화면에 안 보임): "))
        self.ask = ask_fn or (lambda q: input(q + " [y/N]: ").strip().lower() in ("y", "yes", "예", "ㅇ"))
        self.allow_save = allow_save
        self._mem: dict[str, Secret] = {}

    # --- 저장본 ---
    def _load_file(self) -> dict:
        if not self.dpapi or not os.path.exists(self.path):
            return {}
        try:
            return json.load(open(self.path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def _from_file(self, name):
        enc = self._load_file().get(name)
        if not enc:
            return None
        try:
            return self.dpapi.unprotect(base64.b64decode(enc)).decode("utf-8")
        except Exception:  # noqa: BLE001
            return None

    def _save_file(self, name, value: str):
        d = self._load_file()
        d[name] = base64.b64encode(self.dpapi.protect(value.encode("utf-8"))).decode("ascii")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, self.path)

    # --- 공개 ---
    def has_saved(self, name) -> bool:
        return name in self._load_file()

    def get(self, name, required=True) -> Secret | None:
        if name in self._mem:
            return self._mem[name]
        v = self._from_file(name)
        if not v and required:
            v = (self.prompt(NAMES.get(name, name)) or "").strip()
            if v and self.dpapi and self.allow_save and self.ask(
                    NAMES.get(name, name) + "을(를) 이 노트북에 Windows 암호화(DPAPI)로 저장할까요? 저장 안 하면 다음 실행 때 다시 물어요"):
                self._save_file(name, v)
        if not v:
            return None
        s = Secret(v)
        self._mem[name] = s
        return s

    def values(self):
        return [s.reveal() for s in self._mem.values() if s]

    def forget_saved(self, names=None):
        """저장본 지우기(대표가 암호를 바꾸면). names=None 이면 파일 통째로."""
        if names is None:
            if os.path.exists(self.path):
                os.remove(self.path)
            self._mem.clear()
            return
        d = self._load_file()
        for n in names:
            d.pop(n, None)
            self._mem.pop(n, None)
        if self.dpapi:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            json.dump(d, open(self.path, "w", encoding="utf-8"))


class Redactor:
    """로그에 쓰기 전 비밀값과 비밀 이름의 값을 *** 로 바꾼다."""

    def __init__(self, store: SecretStore | None = None):
        self.store = store

    def text(self, s: str) -> str:
        s = str(s)
        for v in (self.store.values() if self.store else []):
            if v and len(v) >= 2:
                s = s.replace(v, "***")
        return s

    def obj(self, o):
        if isinstance(o, Secret):
            return "***"
        if isinstance(o, dict):
            return {k: ("***" if any(w in str(k).lower() for w in SECRET_WORDS) else self.obj(v)) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [self.obj(v) for v in o]
        if isinstance(o, str):
            return self.text(o)
        return o
