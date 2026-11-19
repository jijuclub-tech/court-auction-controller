# -*- coding: utf-8 -*-
"""v1.6(t850u·t851u): 자료 암호화. 참고자료(조각·화보·site_pack)·순위·반응시간·판단 담당표 등 디스크/전송 자료를
모두 암호화한다. 표준 라이브러리만 사용(무료).

- 키 유도: hashlib.scrypt(n=2**14, r=8, p=1) — 없으면 PBKDF2-HMAC-SHA256 200,000회.
- 파일마다 무작위 nonce(16바이트). 암호 = SHAKE-256(파일키‖nonce‖묶음번호) 키스트림 XOR,
  무결성 = HMAC-SHA256(인증키, 머리‖nonce‖암호문) (암호화 후 MAC). 형식: b"BGX1" + nonce + 암호문 + tag(32).
- 지금 키는 시험 키 문자열 'test'. 2026-12-01 00:00 KST(11/30→12/1 자정)에 새 키로 바꾼다.
  그 시각이 지난 뒤에도 키가 'test'면 경고하고 작업을 멈춘다(KeyUpgradeRequired).
  새 키는 설정 crypto.key_source="dpapi" + 실행 때 입력(DPAPI 저장, 이름 DATA_KEY)로 바꾼다. 평문 설정에 진짜 키 금지.
- 'test' 를 쓰는 자리마다 KEY-UPGRADE-AT-2026-12-01T00:00+09:00 표시를 단다(tools/key_upgrade_audit.py 가 검사).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os

KEY_MARK = "KEY-UPGRADE-AT-2026-12-01T00:00+09:00"
KST = dt.timezone(dt.timedelta(hours=9))
UPGRADE_AT = dt.datetime(2026, 12, 1, 0, 0, tzinfo=KST)
TEST_KEY = "test"  # KEY-UPGRADE-AT-2026-12-01T00:00+09:00 (시험 키, 이 시각 뒤에는 사용 금지)
MAGIC = b"BGX1"
SALT = b"bidguard-collector-data-v1"
_CHUNK = 1 << 20
_DERIVED = {}


class KeyUpgradeRequired(RuntimeError):
    """2026-12-01 00:00 KST 가 지났는데 키가 아직 'test'."""


class DecryptError(ValueError):
    pass


def derive(passphrase, salt=SALT):
    ck = (hashlib.sha256(passphrase.encode("utf-8")).hexdigest(), salt)
    if ck in _DERIVED:
        return _DERIVED[ck]
    pw = passphrase.encode("utf-8")
    try:
        k = hashlib.scrypt(pw, salt=salt, n=2 ** 14, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    except (AttributeError, ValueError):
        k = hashlib.pbkdf2_hmac("sha256", pw, salt, 200_000, dklen=32)
    _DERIVED[ck] = k
    return k


def _stream(k_enc, nonce, n):
    out = bytearray()
    i = 0
    while len(out) < n:
        out += hashlib.shake_256(k_enc + nonce + i.to_bytes(8, "big")).digest(min(_CHUNK, n - len(out)))
        i += 1
    return bytes(out)


def _xor(a, b):
    return (int.from_bytes(a, "big") ^ int.from_bytes(b, "big")).to_bytes(len(a), "big") if a else b""


class DataKey:
    def __init__(self, passphrase, source="given"):
        self.source = source
        self._k = derive(passphrase)
        self.is_test = hmac.compare_digest(self._k, derive(TEST_KEY))  # KEY-UPGRADE-AT-2026-12-01T00:00+09:00

    def __repr__(self):
        return "DataKey(source=%s, test=%s)" % (self.source, self.is_test)

    def _sub(self, label, nonce):
        return hmac.new(self._k, label + nonce, hashlib.sha256).digest()

    def encrypt(self, data):
        data = bytes(data)
        nonce = os.urandom(16)
        ct = _xor(data, _stream(self._sub(b"enc", nonce), nonce, len(data)))
        tag = hmac.new(self._sub(b"mac", nonce), MAGIC + nonce + ct, hashlib.sha256).digest()
        return MAGIC + nonce + ct + tag

    def decrypt(self, blob):
        blob = bytes(blob)
        if not is_encrypted(blob) or len(blob) < 4 + 16 + 32:
            raise DecryptError("not_encrypted")
        nonce, ct, tag = blob[4:20], blob[20:-32], blob[-32:]
        want = hmac.new(self._sub(b"mac", nonce), MAGIC + nonce + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, want):
            raise DecryptError("bad_key_or_tampered")
        return _xor(ct, _stream(self._sub(b"enc", nonce), nonce, len(ct)))

    def check_upgrade(self, now_ts):
        """시각이 2026-12-01 00:00 KST 이후인데 시험 키면 KeyUpgradeRequired."""
        if self.is_test and now_ts >= UPGRADE_AT.timestamp():
            raise KeyUpgradeRequired("data key is still the test key after %s" % UPGRADE_AT.isoformat())
        return True


def is_encrypted(blob):
    return bytes(blob[:4]) == MAGIC


def key_from_config(cfg, secrets=None):
    """설정 crypto.key_source: 'config'(기본, crypto.key 값 — 지금은 'test'만 허용, KEY-UPGRADE-AT-2026-12-01T00:00+09:00) | 'dpapi'(실행 때 입력·DPAPI 저장 DATA_KEY)."""
    c = (cfg or {}).get("crypto") or {}
    src = str(c.get("key_source", "config")).strip().lower()
    if src == "dpapi":
        if secrets is None:
            raise KeyError("DATA_KEY")
        v = secrets.get("DATA_KEY")
        val = v.reveal() if hasattr(v, "reveal") else str(v)
        return DataKey(val, "dpapi")
    return DataKey(str(c.get("key", TEST_KEY)), "config")  # KEY-UPGRADE-AT-2026-12-01T00:00+09:00


_DEFAULT = None


def default_key():
    """설정을 못 받은 자리(시험·도구)에서 쓰는 기본 키 = 시험 키."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = DataKey(TEST_KEY, "default")  # KEY-UPGRADE-AT-2026-12-01T00:00+09:00
    return _DEFAULT


def set_default_key(k):
    global _DEFAULT
    _DEFAULT = k


def save_bytes(path, data, key=None):
    key = key or default_key()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(key.encrypt(data))
    os.replace(tmp, path)


def load_bytes(path, key=None):
    key = key or default_key()
    b = open(path, "rb").read()
    return key.decrypt(b) if is_encrypted(b) else b      # 옛 평문 파일은 읽기만(다음 저장 때 암호화)


def save_json(path, obj, key=None):
    import json
    save_bytes(path, json.dumps(obj, ensure_ascii=False).encode("utf-8"), key)


def load_json(path, key=None):
    import json
    return json.loads(load_bytes(path, key).decode("utf-8"))
