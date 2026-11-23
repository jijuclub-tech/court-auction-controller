# BIDGUARD 수집기 옵션 확장 T-v1.8.0 — 이슈 #50 검수안

기준 지시: https://github.com/jijuclub-tech/court-auction-controller/issues/50

## 상태와 범위

- **구현한 것**: 옵션 파서 `/lock`, `/unlock`, `/upload`, `/kiosk`; 원본 호출을 보존하는 주입형 런타임; 60초 주기 HTTPS 업로드 재시도; SQLite 대기열; 관제기 인증 파일 수신·SHA256 디스크 재검증·ACK; 수집기별 NAS 계정 임시 세션 인터페이스; 정확한 HID 인스턴스 ID 기반 잠금/복원 모듈; 독립 Chrome 프로필·허용 도메인 탭 종료 모듈; 오프라인 회귀 테스트.
- **구현 범위 밖 / 현장 확인 필요**: 기준본 `court/standalone_collector_v1_7_4p/`의 실제 파일/진입점/해시가 현재 GitHub main·연결된 Drive에서 제공되지 않았다. 그래서 **실제 v1.7_4p 본체에 연결·실행파일 빌드·완성 ZIP 제작을 수행하지 않았다.** 이 PR은 v1.8.0의 *가산형 모듈 검수안*이며 운영 릴리스가 아니다.
- **운영 전제**: #47 키 금고의 실제 승인/대여 API, 등록 장치 키, 백업/NAS 운영계정, 원격 잠금 해제 경로, Pico2 PnP instance ID, Windows 관리자 권한, field-verified Windows boot watchdog가 필요하다.
- **보안/개인정보**: 코드·합성 시험 데이터만 GitHub에 제출. 실제 토큰/비밀번호/수집 원문/개인 경로는 GitHub에 남기지 않음. 수집 원본 파일 삭제 없음. 새 GitHub Actions·배포 없음.

## 파일과 사용 지점

| 파일 | 책임 |
|---|---|
| `collector_launch_options_T_v1_8_0.py` | 옵션 파서·엄격 HTTPS URL·증분 대기열·서버 ACK 검증·kiosk allowlist·잠금 사전검증 |
| `collector_file_ingest_T_v1_8_0.py` | 등록 수집기별 HTTP POST 파일 수신; SHA256+디스크 재검증, 중복 ACK, 원본 불변 저장 |
| `controller.py` | 위 intake route와 DB 테이블 초기화만 추가. 기존 API 기능 삭제 0 |
| `collector_safe_windows_T_v1_8_0.py` | Pico2 제외·장치명 정확 일치·잠금/복원 journal·독립 watchdog 확인·Chrome 전용 프로필 CDP 모니터 |
| `collector_account_lease_T_v1_8_0.py` | #47 approved runtime key callback을 통한 수집기별 NAS UNC 임시 인증·종료 시 비밀 bytearray 지움 |
| `collector_feature_runtime_T_v1_8_0.py` | v1.7 진입점이 부르는 옵션별 콜백, PDF 검증 완료 시 큐 등록, 원격 pause.flag 처리, 60초마다 재전송 |
| `test_collector_*_T_v1_8_0.py` | 가짜 디바이스·가짜 파일·가짜 업로드로 원복/재시도/거부/호환 시험 |

## v1.7_4p 연결에 필요한 정확한 변경

1. 원본 소스/설치 ZIP을 SHA256으로 보존한 뒤 **현행 프로그램 이름/DB/설정/Pico2/F8/법원 수집 함수의 출입점**을 찾는다.
2. 실행 명령 맨 앞에서 `split_options(argv)`로 옵션만 분리하고, 나머지 인자를 원본 함수에 그대로 전달한다. **옵션이 없으면 원래 진입점 그대로** 실행한다.
3. 원본 저장 파이프라인에서 실제 PDF 헤더·사건/물건 일치·SHA256 완료 **후에만** `runtime.on_verified_document(local_path, "appraisal")`를 호출한다. /upload 대기열은 수집 원본이나 기존 NAS 큐를 삭제하거나 이동시키지 않는다.
4. 원본 Pico2 명령 직전에 `runtime.pico_input_permitted()` 확인: `remote_guard pause.flag`가 있으면 **업무 일시정지 및 진행상태 유지**; RDP 연결과 F8을 차단하지 않는다.
5. `/kiosk` 전용 Chrome 프로필을 원본 브라우저 실행기와 연결해 같은 프로필에서만 실행하며 `check_once`가 허용하지 않는 탭을 닫는지 확인한다. 사용자의 일반 Chrome·원격 데스크톱은 종료하지 않는다.
6. `/lock`은 화면 차단 흉내가 아니라 지정한 물리 HID *정확한 인스턴스 ID*만 PnP로 비활성하는 선택 실행이다. Pico2 VID 2E8A를 제외한다. **독립 watchdog ONSTART + RDP 원격 /unlock 실행·정상/강제 종료 복원 시험이 모두 PASS하지 않으면 잠금하지 말 것.** 자동 실행/자동 적용하지 않는다. 파일 기반 journal은 재부팅에도 남고 watchdog이 만료 시 복원한다. 15분 만료로 운영 중에도 잠금이 풀릴 수 있으므로 실제 장시간 잠금에는 독립 heartbeat/watchdog 연장 기능을 추가 검증해야 한다.
7. `/unlock`은 원본 수집기가 실행되지 않아도 별도 프로세스/관리자 권한으로 `recover(journal)`를 실행하도록 기존 EXE 인자 핸들러에 연결한다. 원격 접속에서 실증. 잠금 중 수집기 에러·강제종료·타임아웃·재부팅 시 자동 복구를 입증할 것.
8. #47 키 금고에서 승인된 `nas_write` 계정만 일시 대여한다. **서버 NAS ACL이 실제로 `\\server\\share\\collector_id` 자기 폴더 쓰기만 허용하는지** 반드시 점검한다. X: 드라이브가 없을 때는 허용된 HTTPS URL/비인증 실패 분기를 사용하며 사용자 실계정을 재사용하지 않는다.

## 관제기 /upload 환경 계약

관제기에는 비밀값이 아닌 **관리자가 확인한 절대 저장 루트**만 `BIDGUARD_COLLECTOR_UPLOAD_DIR`로 설정한다. 새 경로 `POST /api/v1/collector/upload`은 등록/승인된 `X-Collector-Name`, 장치별 `X-Collector-Token`, HTTPS/TLS를 필수 요구한다. 본문은 원본 바이트, 헤더는 `X-Content-SHA256`, `X-Document-Type`, `Content-Length`. 1건 최대 128MiB. 서버는 `root/<collector>/<sha256 첫 두 글자>/<sha256>`로 원본을 저장한다. 성공 ACK는 `status=controller_stored`, `sha256`, `size`, `backup_verified=false`다.

**중요**: 중앙 수신 디스크 적재를 NAS·Drive 백업 성공으로 표기하지 않는다. 추후 원장/SHA 일치·NAS·Drive 2차 확인 후 별도 상태를 갱신해야 한다. 네트워크 끊김·401/403/5xx·응답 소실·해시 불일치면 로컬 대기열 유지. 원본 해시가 달라지면 `NEEDS_REVIEW`로 보존하고 자동 전송을 멈춘다.

## 시험 절차 (실행 전용: 실제 결과 주장 금지)

```bash
python -m unittest -v \
  test_collector_issue50_T_v1_8_0 \
  test_collector_safe_windows_T_v1_8_0 \
  test_collector_account_lease_T_v1_8_0 \
  test_collector_feature_runtime_T_v1_8_0
python -m py_compile collector_*_T_v1_8_0.py
```

**실행 보류**: 이 GitHub 연결에서는 원본 v1.7 ZIP과 Windows/Pico2/원격 관제 호스트에 접근할 수 없어 위 시험을 실제로 실행했다고 기록하지 않는다. 기존 GitHub Actions는 켜지 않는다.

## 현장 승인 체크리스트

- [ ] 사용자 기준본 v1.7_4p의 SHA256 확보 및 보존
- [ ] 옵션 없음, 주요 기능 1:1 회귀 PASS
- [ ] /upload: X: 미연결, 서버 201·200 중복, 403, 네트워크 5xx, 재부팅 후 대기열 재전송, Drive/NAS 미확인 분리 PASS
- [ ] /kiosk: 법원 허용 URL 외 탭 종료, 일반 Chrome 및 RDP 유지 PASS
- [ ] /lock: Pico2 정상 인식, exact HID 일치, 원격 세션·비상 /unlock, 강제종료·재부팅 후 자동 복구 PASS
- [ ] 키 금고 승인/대여/회수·NAS 개인 계정 ACL 검증
- [ ] 대표 승인, 1대 파일럿, 운영 변경 이력·롤백 준비

## 이번 PR의 판정

GitHub 코드 작성은 진행하되 **실기기 시험 전 병합/자동 설치 금지**. 원본 수집기 코드를 임의 수정하지 않으며 원본을 확보한 뒤 그대로 포함한 `standalone_collector_T_v1.8.0.zip`을 별도 빌드/검수해야 한다.
