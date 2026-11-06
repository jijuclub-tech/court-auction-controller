"""시험용 UF2 파일 만들기 (규격대로 만든 가짜 펌웨어 — 실제 보드에 넣는 용도 아님)"""
import struct

def make_uf2(families, blocks_per_family=4, corrupt_block=None):
    out = b""
    for fam in families:
        for n in range(blocks_per_family):
            payload = bytes([n % 256]) * 256
            head = struct.pack("<8I", 0x0A324655, 0x9E5D5157, 0x00002000, 0x10000000 + n * 256, 256, n, blocks_per_family, fam)
            data = payload + b"\x00" * (476 - len(payload))
            block = head + data + struct.pack("<I", 0x0AB16F30)
            out += block
    if corrupt_block is not None:
        i = corrupt_block * 512
        out = out[:i] + b"\xff\xff\xff\xff" + out[i + 4:]
    return out
