# 설치 프로그램 모듈 — 수집기(윈도우) · 피코2 펌웨어(윈도우) · 관제기(우분투)

판: **S-0.1.0** (작업판) · 펌웨어 묶음 판: S-0.2.5 · 2026-11-06

> 원칙: 설치가 번거로우면 안 된다. 사람이 하는 일은 **더블클릭 → [다음] 몇 번** 뿐.
> 설치하는 컴퓨터는 인터넷이 필요 없다. 실패하면 설치 전 상태로 되돌린다. 자료는 지킨다.

## 1. 묶음 3종

| 묶음 (ZIP) | 누가 쓰나 | 진입점 | 설치 자리 |
|---|---|---|---|
| `Court-Auction-Collector-Windows-Setup-S-0.1.0` | 수집기 PC (윈도우 10/11 64비트) | `Install_Court_Auction_Collector.bat` | `C:\BIDGUARD\Court_Auction_Collector` |
| `Pico2-Firmware-Update-Windows-S-0.2.5` | 펌웨어만 새로 나왔을 때 | `Update_Pico2_Firmware.bat` | (설치된 수집기 사용) |
| `Court-Auction-Controller-Ubuntu-Setup-S-0.1.0` | 관제기 (우분투 22.04 이상) | `install_court_auction_controller.sh` | `/opt/bidguard/controller` |

모든 ZIP은 2026-10-03 배포 경로 계약을 따른다: 최상위 폴더 1개(= `archive_root`), `version.json`, `deployment.json`, `BUILD-MANIFEST.json`(모든 파일 SHA256). 설치기는 시작하자마자 이 지문을 전부 확인하고, 하나라도 다르면 아무것도 바꾸지 않고 멈춘다.

## 2. 윈도우 수집기 설치 마법사

`installer/windows_collector/` — PowerShell(윈도우 기본 내장) + WinForms 화면. 관리자 권한 불필요.

| 화면 | 내용 |
|---|---|
| 1 시작 | 무엇을 하는지 쉬운 말로 |
| 2 점검 | 윈도우·디스크·묶음 지문·이전 설치·예전 방식 수집기 폴더·실행 중인 수집기([닫기] 단추)·파이썬·**화면(브라우저) 해상도** |
| 3 설정 | 설치 폴더(영어만, 내려받기·바탕화면·OneDrive 금지), 수집기 이름(입력하는 대로 관제형/보급형 표시), 관제기 주소([연결 확인]), 바탕화면·시작 메뉴·자동 실행 |
| 4 설치 | 단계별 ✔ 표시 + 진행 막대 + 기록 |
| 5 완료 | 다음에 할 일, 화면 안내, [수집기 실행] [피코2 펌웨어 업데이트] [설치 기록 열기] |

**화면(브라우저) 해상도 안내** — 점검·완료 화면과 `READ_ME_FIRST.txt`
- 권장: 1920×1080, 윈도우 배율 100%, 브라우저 최대화·확대 100%(Ctrl+0)
- 이 PC의 실제 해상도·배율을 재서 "브라우저에 실제로 보이는 크기"(실제 해상도 ÷ 배율)를 보여 주고 좋음/보통/문제로 판정
- 문제가 있으면: "법원경매 페이지 전체가 브라우저 한 화면에 들어와야 수집기가 단추와 글자를 잘 알아봅니다" + ① 배율 100% ([디스플레이 설정 열기] 단추) ② 브라우저 Ctrl+- 로 90%·80% ③ 해상도 1920×1080
- 설정값: `installer_settings.json`의 `screen` (권장 1920×1080, 최소 1366×768)

**파이썬**: 수집기 화면이 tkinter를 쓰므로 정식 설치 파일(3.12.10)을 묶음에 넣음.
1. 이전에 이 설치가 깐 전용 파이썬이 있으면 그대로 사용
2. 이 PC에 파이썬 3.12가 이미 있고 tkinter가 되면 그것으로 전용 실행 공간(venv)을 만듦 (남의 파이썬을 바꾸지 않음)
3. 없으면 묶음 안 파이썬을 `설치폴더\runtime\python`에 이 프로그램 전용으로 설치 (PATH·파일 연결 안 건드림)
부품(requests·python-dotenv·pyserial)은 묶음 안 wheel로만 설치(`--no-index`).

**되돌리기**: 새 판(app·tools·venv)을 옆에 다 만든 뒤 한 번에 바꿔 끼움. 자가점검(실제 import, 펌웨어 파일 점검)까지 통과해야 옛 판을 지움. 실패하면 옛 판·바로가기·윈도우 앱 목록·새로 깐 파이썬을 되돌림.

**지키는 자료**: `config.json`, `settings.json`, `credentials_local.json`, `work_policy.json`, `reports`, `data`, `map_submissions`
**예전 방식 수집기**(압축 풀고 install.bat → 바탕화면 `Collector_이름.lnk`)를 찾으면 이름·설정·자료를 옮기고 예전 폴더·바로가기를 정리(선택).
**지우기**: 시작 메뉴 BIDGUARD 또는 윈도우 설정 > 앱. 자료는 기본으로 `C:\BIDGUARD\Court_Auction_Collector_Data_날짜`에 옮겨 두고, 다시 설치하면 찾아서 되살릴지 묻는다.

## 3. 피코2 펌웨어 업데이트기

`installer/windows_firmware/pico2_firmware_updater.py` — 수집기 설치에 같이 들어가 시작 메뉴 `BIDGUARD Pico2 Firmware Update`로 실행.

1. 펌웨어 파일 검사: UF2 규격, **RP2350(피코2)용만**(피코1 RP2040 파일 거부), 지문(firmware_version.json)
2. 라즈베리파이 USB 장치(VID 2E8A)만 골라 `HEARTBEAT → ACK` 확인. 수집기가 포트를 쓰는 중이면 "수집기를 닫고 다시"
3. 1200 보드 신호로 업데이트 모드(BOOTSEL) 자동 전환 → 안 되면 화면에 BOOTSEL 단추 안내 후 3분 기다림(취소 가능)
4. RP2350 드라이브 확인(피코1 드라이브면 거부) → 복사
5. 재시작 후 `HEARTBEAT → ACK` 확인되어야 완료. 그때만 설치 기록(`firmware_install_record.json`) 저장
6. 같은 판 기록이 있으면 다시 넣을지 묻는다

펌웨어 소스에는 판을 묻는 명령이 없어(HEARTBEAT/MOVE/CLICK/TYPE 만) 판은 PC의 설치 기록으로 관리한다. 펌웨어 코드는 건드리지 않았다.

## 4. 우분투 관제기 설치기

`installer/ubuntu_controller/` — bash + whiptail(우분투 기본) 창, whiptail이 없으면 글자 질문.

- 파일 관리자에서 '프로그램으로 실행' → 터미널이 저절로 열리고 sudo 비밀번호 한 번
- 점검: 우분투·파이썬 3.10+·venv·디스크·묶음 지문·**이 파이썬에 맞는 부품 유무**·이전 설치·글록 판 서비스·systemd·실행 계정
- 부품: `python3 -m venv --without-pip` + 묶음 안 pip wheel 로 오프라인 설치 → 우분투에 `python3-venv`/`pip` 패키지를 따로 깔 필요 없음
- 서비스 `bidguard-court-auction-controller`: **설치한 사람 계정으로 실행**(글록 판의 `User=ubuntu` 고정 문제 해결), 포트 지정, `UMask=0077`(DB·API 키 파일을 그 계정만 읽음), 재부팅 자동 실행
- 실제로 켜서 `/login` 화면이 열려야 성공. 아니면 서비스·파일·자료를 설치 전으로 되돌림
- 글록 판 서비스(`bidguard-controller`)가 있으면 자료를 지키고 새 서비스로 바꾼 뒤 정리
- 완료 화면: 브라우저 주소, **수집기에 넣을 관제기 주소**, ufw 방화벽이 켜져 있으면 집 안 네트워크만 열지 묻기
- 지우기: 자료는 기본으로 `/opt/bidguard/controller_data_날짜`에 옮겨 둠 → 다시 설치하면 되살릴지 물음

## 5. 묶음 만들기

인터넷이 되는 컴퓨터에서 한 번:
```bash
python3 installer/build/build_release_bundles.py --download --firmware-uf2 <빌드된 bidguard-hid-pico2 UF2>
```
- 파이썬 설치 파일·윈도우 부품(cp312 win_amd64)·우분투 부품(파이썬 3.10~3.13, manylinux)을 받아 `RELEASE-CACHE/`에 둠
- 묶음마다 **인터넷 없이 설치되는지 미리 풀어 보는 시험**(pip dry-run --no-index)을 통과해야 만듦
- 펌웨어를 소스에서 빌드: `--arduino-cli arduino-cli --firmware-source <펌웨어 저장소>` (FQBN `rp2040:rp2040:rpipico2`)
- 결과: `RELEASE-STAGE/*.zip`. 이미 있는 폴더·ZIP은 덮어쓰지 않음
- 판 올리기: `installer/build/build_settings.json`의 `installer_version`

## 6. 시험

```bash
sudo bash tests/installer/run_installer_tests.sh
```
2026-11-06: **77개 통과 / 0개 실패** — 펌웨어 업데이트기 28, 묶음 제작기 18, 우분투 설치기 31(격리 상자에서 실제 설치·실행 10개 경우).

시험하지 못한 것 (윈도우·실장치가 필요):
- 윈도우 설치 마법사 실제 실행 — 이 시험 환경에 PowerShell이 없어 괄호·따옴표 짝만 검사. **윈도우 PC에서 첫 시험 필요**
- 실제 파이썬 3.12.10 설치 파일·윈도우 부품으로 만든 묶음 (시험은 이름만 같은 빈 파일)
- 실제 피코2 보드 전환·복사 (가짜 보드·가짜 드라이브로 순서 시험), 펌웨어 화면(tkinter)
- systemd가 있는 우분투에서의 서비스 등록·재부팅 자동 실행 (시험 상자에는 systemd가 없어 직접 켜는 길로 시험)
