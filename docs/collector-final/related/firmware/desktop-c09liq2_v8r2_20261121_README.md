# DESKTOP-C09LIQ2(새 1호기) 피코2 1.0.3 → 1.0.8-v8 r2 굽기 (2026-11-21 KST)
- 장치: COM4, USB VID_2E8A PID_000C (Pico 2, RP2350)
- 굽기 전: PING => PONG BIDGUARD_HID v7.3 / 1.0.3 / HUMAN=2, VERSION => 1.0.3, STATUS/BUILD => ERR:UNKNOWN_CMD
  CAPS => MOVE_TO,HOVER,CLICK_DWELL,RCLICK,DRAG,RDRAG,MOVE_CFG,BOUNDS,WHERE,WHEEL,ABORT,SET_MOUSE_MODE,SET_DISPLAY,PC_ON,PC_OFF,PC_FORCE_OFF,INIT,KEY,SET_MASTER
- 기준: GitHub jijuclub-tech/BIDGUARD-PICO2-FIRMWARE-PRIVATE 브랜치 grok/firmware-1.0.8-v8-gameui-20261120 최신 커밋 115c605 (r2). 그보다 새 판 없음(main 00fa674는 문서만).
- UF2: bidguard-hid-pico2-1.0.8-v8-r2-16MB.uf2, SHA256 cc81a4b08ba4167dc38fa206351b6245cd4c14869d0aa4c591c163b2d9f393bb (복사 전 PC에서 해시 일치 확인)
- 방법: 시리얼 REBOOT_BOOTSEL => ACK:REBOOT_BOOTSEL (1.0.3 CAPS엔 없지만 동작, 버튼 불필요) → D: RP2350, INFO_UF2.TXT "UF2 Bootloader v1.0 / Model: Raspberry Pi RP2350 / Board-ID: RP2350" → UF2 복사 → COM4 재연결
- 굽기 후: PING => PONG BIDGUARD_HID v8 / 1.0.8-v8 / HUMAN=2, VERSION => v8, BUILD => 1.0.8-v8
  STATUS => build=1.0.8-v8,proto=v8,armed=0,fs=READY,used=12288,total=16244736,full=0,wdt_reboot=0 (LittleFS 기록 보존)
  CAPS 에 STOP·F8·STATUS·REBOOT_BOOTSEL·HEARTBEAT·ARM·EMERGENCY_STOP 포함
- 원복: 1.0.3 은 UF2 백업 없음(RP2350 BOOTSEL 드라이브는 현재 펌웨어를 꺼내 주지 않음). 소스만 GitHub backup_sources_20261120/1.0.3_source 와 branch firmware-1.0.3-source.
  원복용 UF2 는 새 PC C:\BIDGUARD\pico2_upgrade_20261121\ 에 둠: fw107.uf2 (45148c00…d0ec, 1.0.7), bidguard-hid-pico2-1.0.8-v8-16MB.uf2 (901874ed…48079, r1)
- 새 PC 기록 원본: C:\BIDGUARD\pico2_upgrade_20261121\ (fw_sha256.txt, pre_flash_probe.txt, flash_log.txt, info_uf2.txt, post_flash_tests.txt)
- 수집기 v1_6_v8 호환: PING 둘째 줄 빌드 1.0.8-v8 로 판정(min_build 1.0.7 통과), PROTOCOLS 에 v8 있음, VERSION 응답은 쓰지 않음. REQUIRED_CAPS 모두 있음. 실제 수집기 연결 시험(PicoActuator 열기)은 새 PC 승인 대기로 아직 못 함.
