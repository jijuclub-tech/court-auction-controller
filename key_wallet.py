#!/usr/bin/env python3
"""BIDGUARD controller encrypted key wallet — beta, offline administrator CLI.

No key/passphrase is accepted via CLI arguments or stored in Git. Only encrypted
JSON is written. The cryptography package supplies Scrypt + AES-256-GCM.
This module is deliberately not imported by the controller HTTP service.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
except ImportError as exc:
    raise SystemExit('무료 검증 라이브러리 cryptography가 필요합니다: python3 -m pip install cryptography') from exc

WALLET_FORMAT = 'bidguard.wallet.v1'
LOG_FORMAT = 'bidguard.log.v1'
AAD_WALLET = b'BIDGUARD-WALLET-v1'
N = 2**15
MAX_LOG = 8 * 1024 * 1024


def b64(data):
    return base64.b64encode(data).decode('ascii')


def unb64(value):
    return base64.b64decode(value, validate=True)


def erase(buffer):
    """Best-effort only: Python and crypto internals may retain copies."""
    if isinstance(buffer, bytearray):
        buffer[:] = b'\0' * len(buffer)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def fingerprint(key):
    return hashlib.sha256(b'BIDGUARD-master-fingerprint-v1:' + bytes(key)).hexdigest()[:24]


def kdf(passphrase, salt):
    return Scrypt(salt=salt, length=32, n=N, r=8, p=1).derive(passphrase.encode('utf-8'))


def seal(key, plaintext, aad):
    nonce = secrets.token_bytes(12)
    return {'nonce': b64(nonce), 'ciphertext': b64(AESGCM(bytes(key)).encrypt(nonce, bytes(plaintext), aad))}


def open_seal(key, blob, aad):
    return AESGCM(bytes(key)).decrypt(unb64(blob['nonce']), unb64(blob['ciphertext']), aad)


def secure_parent(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('symbolic links not allowed')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Existing directories may be shared mountpoints: do not change their ACLs.
    return path


def check_private(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('symbolic links not allowed')
    if os.name == 'posix' and (path.stat().st_mode & 0o077):
        raise PermissionError('encrypted file must have permissions 0600')
    return path


def write_private(path, document, create=False):
    path = secure_parent(path)
    if create and path.exists():
        raise FileExistsError('wallet already exists; refusing replacement')
    raw = (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()
    fd, temporary = tempfile.mkstemp(prefix='.bidguard-wallet-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            # Atomic no-clobber: prevents two init processes overwriting each other.
            os.link(temporary, path)
            os.unlink(temporary)
        else:
            os.replace(temporary, path)
        if os.name == 'posix':
            dirfd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(dirfd)
            finally: os.close(dirfd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_private(path, expected_format):
    p = check_private(path)
    if p.stat().st_size > 2 * MAX_LOG:
        raise ValueError('encrypted file too large')
    with p.open('rb') as f:
        data = json.load(f)
    if data.get('format') != expected_format:
        raise ValueError('unsupported encrypted format')
    return data


def prompt(prompt_text, confirm=False, min_length=0):
    value = getpass.getpass(prompt_text)
    if len(value) < min_length:
        raise ValueError('password too short')
    if confirm and value != getpass.getpass('다시 입력: '):
        raise ValueError('password confirmation does not match')
    return value


def new_wallet(passphrase):
    if len(passphrase) < 12:
        raise ValueError('administrator password must be at least 12 characters')
    master = bytearray(secrets.token_bytes(32))
    salt = secrets.token_bytes(16)
    kek = bytearray(kdf(passphrase, salt))
    try:
        wallet = {
            'format': WALLET_FORMAT, 'created_at': stamp(),
            'kdf': {'name': 'scrypt', 'n': N, 'r': 8, 'p': 1, 'salt': b64(salt)},
            'wrapped_master': seal(kek, master, AAD_WALLET),
            'master_fingerprint': fingerprint(master)
        }
        return wallet, master
    finally:
        erase(kek)


def unlock(wallet, passphrase):
    info = wallet['kdf']
    if info['name'] != 'scrypt' or (info['n'], info['r'], info['p']) != (N, 8, 1):
        raise ValueError('unsupported KDF parameters')
    kek = bytearray(kdf(passphrase, unb64(info['salt'])))
    try:
        key = bytearray(open_seal(kek, wallet['wrapped_master'], AAD_WALLET))
        if len(key) != 32 or fingerprint(key) != wallet['master_fingerprint']:
            erase(key)
            raise ValueError('wallet integrity verification failed')
        return key
    finally:
        erase(kek)


def create_log(wallet, admin_password, staff_id, staff_password, plaintext):
    if not staff_id or len(staff_id) > 80 or not all(c.isalnum() or c in '-_.' for c in staff_id):
        raise ValueError('invalid staff ID')
    if not staff_password or len(staff_password) < 12:
        raise ValueError('staff password must be >=12 characters')
    if len(plaintext) > MAX_LOG:
        raise ValueError('log item exceeds size limit')
    master = unlock(wallet, admin_password)
    session = bytearray(secrets.token_bytes(32))
    salt = secrets.token_bytes(16)
    staff_kek = bytearray(kdf(staff_password, salt))
    item_id = str(uuid.uuid4())
    aad = (LOG_FORMAT + ':' + item_id + ':' + staff_id).encode()
    try:
        return {
            'format': LOG_FORMAT, 'item_id': item_id, 'staff_id': staff_id, 'created_at': stamp(),
            'staff_kdf': {'name': 'scrypt', 'n': N, 'r': 8, 'p': 1, 'salt': b64(salt)},
            'master_wrap': seal(master, session, aad + b':master'),
            'staff_wrap': seal(staff_kek, session, aad + b':staff'),
            'payload': seal(session, plaintext, aad + b':payload'),
        }
    finally:
        erase(master)
        erase(session)
        erase(staff_kek)


def decrypt_log(doc, password, wallet=None):
    aad = (LOG_FORMAT + ':' + doc['item_id'] + ':' + doc['staff_id']).encode()
    if wallet is None:
        inf = doc['staff_kdf']
        if inf['name'] != 'scrypt' or (inf['n'], inf['r'], inf['p']) != (N, 8, 1):
            raise ValueError('unsupported staff KDF')
        wrap_key = bytearray(kdf(password, unb64(inf['salt'])))
        wrap = doc['staff_wrap']
        context = aad + b':staff'
    else:
        wrap_key = unlock(wallet, password)
        wrap = doc['master_wrap']
        context = aad + b':master'
    session = None
    try:
        session = bytearray(open_seal(wrap_key, wrap, context))
        return open_seal(session, doc['payload'], aad + b':payload')
    finally:
        erase(wrap_key)
        erase(session)


def replace_admin_password(wallet, master, new_passphrase):
    if fingerprint(master) != wallet['master_fingerprint']:
        raise ValueError('offline master key does not match wallet')
    new, disposable = new_wallet(new_passphrase)
    erase(disposable)
    new['created_at'] = wallet['created_at']
    replacement_kek = bytearray(kdf(new_passphrase, unb64(new['kdf']['salt'])))
    try:
        new['wrapped_master'] = seal(replacement_kek, master, AAD_WALLET)
    finally:
        erase(replacement_kek)
    new['master_fingerprint'] = fingerprint(master)
    return new


def copy_encrypted(wallet_path, target):
    source = check_private(wallet_path)
    target = secure_parent(target)
    if target.is_symlink():
        raise ValueError('backup symlink rejected')
    if source == target:
        raise ValueError('backup target equals source')
    raw = source.read_bytes()
    src_sha = hashlib.sha256(raw).hexdigest()
    # Preserve encrypted bytes only, no unlock.
    tmp = target.with_name(target.name + '.tmp-' + secrets.token_hex(6))
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != src_sha:
            raise ValueError('encrypted backup checksum mismatch')
    finally:
        tmp.unlink(missing_ok=True)
    return src_sha


def require_tty():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError('interactive terminal required; key must not be redirected')


def print_master(wallet, password):
    require_tty()
    master = unlock(wallet, password)
    try:
        if input('마스터 키를 오프라인으로 인쇄할 준비가 되었으면 PRINT 입력: ').strip() != 'PRINT':
            raise ValueError('print not confirmed')
        print('\033[2J\033[H')
        print('BIDGUARD OFFLINE RECOVERY MASTER — NOT FOR ONLINE STORAGE')
        print('Master (hex):', master.hex())
        print('Fingerprint:', fingerprint(master))
        input('\n인쇄 및 확인 후 Enter를 누르면 화면을 지웁니다: ')
    finally:
        erase(master)
        print('\033[2J\033[3J\033[H', end='', flush=True)


def main(argv=None):
    p = argparse.ArgumentParser(description='BIDGUARD 관제기 암호화 지갑 (로컬 관리인 전용)')
    p.add_argument('--wallet', default=os.environ.get('BIDGUARD_WALLET', 'controller_data/key-wallet.json'))
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('init')
    sub.add_parser('status')
    sub.add_parser('print-master')
    sub.add_parser('recover')
    log_in = sub.add_parser('encrypt-log')
    log_in.add_argument('--staff-id', required=True)
    log_in.add_argument('--out', required=True)
    log_out = sub.add_parser('decrypt-log')
    log_out.add_argument('--item', required=True)
    log_out.add_argument('--as-staff', action='store_true')
    backup = sub.add_parser('backup')
    backup.add_argument('--local-dir', required=True)
    backup.add_argument('--nas-dir')
    backup.add_argument('--drive-remote')
    args = p.parse_args(argv)
    path = Path(args.wallet)
    try:
        if args.action == 'init':
            require_tty()
            pw = prompt('새 관리자 암호 (12자 이상): ', confirm=True, min_length=12)
            wallet, key = new_wallet(pw)
            try: write_private(path, wallet, create=True)
            finally: erase(key)
            print('암호화 지갑 생성 완료. 오프라인 복구키는 print-master 명령으로 인쇄하십시오.')
            return 0
        wallet = read_private(path, WALLET_FORMAT)
        if args.action == 'status':
            print('Encrypted wallet:', path, 'fingerprint:', wallet['master_fingerprint'])
            return 0
        if args.action == 'print-master':
            require_tty()
            print_master(wallet, prompt('관리자 암호: '))
            return 0
        if args.action == 'recover':
            require_tty()
            recovery = bytearray.fromhex(prompt('오프라인 인쇄본 마스터 키(hex): '))
            try:
                if len(recovery) != 32:
                    raise ValueError('master key must be 256 bits')
                new = replace_admin_password(wallet, recovery, prompt('새 관리자 암호: ', confirm=True, min_length=12))
                write_private(path, new)
            finally: erase(recovery)
            print('지갑 암호 재설정 완료')
            return 0
        if args.action == 'encrypt-log':
            if sys.stdin.isatty():
                raise ValueError('plaintext log must be piped through stdin, not saved as a file')
            pw = prompt('관리자 암호: ')
            staff_pw = prompt('직원 암호: ', min_length=12)
            if len((raw := sys.stdin.buffer.read(MAX_LOG+1))) > MAX_LOG:
                raise ValueError('log too large')
            enc = create_log(wallet, pw, args.staff_id, staff_pw, raw)
            write_private(args.out, enc, create=True)
            print('Encrypted log item saved:', enc['item_id'])
            return 0
        if args.action == 'decrypt-log':
            require_tty()
            enc = read_private(args.item, LOG_FORMAT)
            pw = prompt('직원 암호: ' if args.as_staff else '관리자 암호: ')
            plain = bytearray(decrypt_log(enc, pw, None if args.as_staff else wallet))
            try:
                print('복호화된 로그 (Enter 입력 후 화면 지움):')
                sys.stdout.write(plain.decode('utf-8', errors='replace'))
                input('\nEnter: ')
            finally:
                erase(plain)
                print('\033[2J\033[3J\033[H', end='', flush=True)
            return 0
        if args.action == 'backup':
            local = Path(args.local_dir) / 'key-wallet.json'
            print('local sha256:', copy_encrypted(path, local))
            if args.nas_dir:
                nas = Path(args.nas_dir)
                if not os.path.ismount(nas):
                    raise ValueError('NAS backup requires verified mountpoint')
                print('NAS sha256:', copy_encrypted(path, nas / 'key-wallet.json'))
            if args.drive_remote:
                if not shutil.which('rclone'):
                    raise RuntimeError('rclone required for encrypted Google Drive backup')
                # drive path is an rclone remote ending in a directory.
                subprocess.run(['rclone', 'copyto', str(path), args.drive_remote.rstrip('/') + '/key-wallet.json',
                                '--checksum'], check=True, timeout=180)
                print('Drive encrypted-only upload requested. Verify remote checksum separately.')
            return 0
    except (OSError, ValueError, RuntimeError, InvalidTag, KeyError) as exc:
        print('작업 실패:', type(exc).__name__, file=sys.stderr)  # never echo secrets
        return 2
    return 1


if __name__ == '__main__':
    raise SystemExit(main())