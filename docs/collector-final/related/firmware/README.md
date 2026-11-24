# 1.0.8-v8 r2 릴리스 (미시험 시험판)

| 파일 | SHA256 |
|---|---|
| bidguard-hid-pico2-1.0.8-v8-r2-16MB.uf2 | `cc81a4b08ba4167dc38fa206351b6245cd4c14869d0aa4c591c163b2d9f393bb` |
| PICO2_INSTALL_1.0.8-v8-r2.zip (Install.ps1 + INSTALL.bat) | SHA256SUMS.txt 참조 |

컴파일: `compile_1.0.8-v8-r2.log` (arduino-cli 1.6.0, rp2040 6.3.2, rpipico2 16MB FS rest). 소스 SHA 는 SHA256SUMS.txt.
1호가 올린 `uf2/bidguard-hid-pico2-1.0.8-v8-16MB.uf2` (901874ed…, = r1) 은 그대로 둔다.

## 복사 방식 업그레이드 (수집기 1호·2호, 사람이 확인)
1. 지금 보드 기록: 시리얼에 `PING`, `STATUS`(없으면 생략), `CAPS` 를 보내 응답을 저장.
2. BOOTSEL: 시리얼 `REBOOT_BOOTSEL` 또는 BOOT 버튼 누른 채 USB 연결 → `RP2350` 드라이브, `INFO_UF2.TXT` 에 RP2350 확인.
3. 이 UF2 를 드라이브에 복사 → 자동 재부팅.
4. 확인: `PING` → `PONG BIDGUARD_HID v8` / `1.0.8-v8` / `HUMAN=n`, `CAPS` 에 STOP·F8·STATUS 포함, `STATUS` 의 fs=READY.
5. 원복: 같은 방법으로 `backup_sources_20261120/1.0.7_v7.3_installer/fw107.uf2` (1.0.7) 또는 `uf2/bidguard-hid-pico2-1.0.8-v8-16MB.uf2` (r1) 복사.
UF2 복사는 프로그램 영역만 쓰므로 LittleFS 기록은 남는 것이 정상 (실기기 미확인). 기록을 지우려면 `INIT`.
**수집기 v1.6 은 PROTOCOLS 에 v8 이 없어 접속을 거부한다** → 수집기 쪽 1줄 수정 전에는 수집기로 운용 불가 (V8_CONTRACT.md §6).
