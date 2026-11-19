# BIDGUARD 수집기·관제기·온라인 임대형 시험판 설치·검증 (2026-11-20)

## 기능 범위
- 회원 페이지 /rental: 사이트별 승인된 경로 선택, 1회 수집/최대 12시간 예약 수집, 즐겨찾기 10곳, 작업 신청/상태 보기, 버튼을 누를 때만 안내.
- 관제기 페이지 /admin/rentals: 수집기 등록 상태, 회원 시험코드 발급, 작업 승인·일시정지·복귀·복구 확인.
- 관제기 rental_control.py: 승인·할당·진행기록·lease/session/epoch·checkpoint·자체업무 우선·끊김 후 복구확인.
- Windows 수집기 control/rental_client.py: 장치 토큰/세션으로 인증, 기존 RouteRunner 실행, 안전 단계에서 일시정지, checkpoint에서 재개. 관제형 탭에서 명시적으로 임대 사용 선택.
- 이름 BIDGUARD 유지, 기존 수집/Pico2/F8·NAS·회원/포인트 기능 삭제 금지.

## 시험 방법
1. Ubuntu 관제기를 운영 DB와 별도 시험 환경에서 구동하고, 다음 회귀 시험을 실행한다:
   python3 -m unittest -v test_rental_control test_dispatch test_controller
2. 정식 운영이 아닌 승인된 시험 관제기에서만 환경변수 BIDGUARD_RENTAL_PILOT_ENABLED=1을 설정한다. 기본값 비활성.
3. 기존 CONTROLLER_ADMIN_TOKEN과 개별 장치 토큰을 외부에 공개하지 않는다. 외부 접근은 HTTPS/제한된 프록시 뒤에서만 운영하며 8770 포트 직접 공개 금지.
4. 관제기 관리화면에서 관제형 수집기 이름·장치 토큰을 등록 및 승인한다.
5. Windows 수집기에서 다음 회귀 시험을 실행한다:
   cd standalone_collector
   python -m unittest -v test_controller_transport test_control_collector test_rental_client test_rental_checkpoint
6. 관제형 탭에서 서버 주소/장치 토큰 연결을 확인, Pico2 ARM 후 임대형 사용 버튼을 선택한다. 기존 수동·관제 실행도 정상인지 확인한다.
7. 관리자 /admin/rentals에서 시험용 회원 이용코드를 발급, 회원 /rental에서 승인된 작업을 신청, 관리자가 승인하고 수집기 할당과 결과 보고를 확인한다.
8. 기존 자체 수집 작업이 시작되면 임대 작업이 안전한 단계에서 일시정지되는지 확인하고, 체크포인트를 보존한 채 복귀하는지 검수한다.
9. 수집기가 중간에 재시작되면 새 세션에서 임의 재실행하지 않고 RECOVERY_REQUIRED가 표시되는지 확인, 관리자가 복구 확인 후 재배정하는지 시험한다.
10. 실원문은 사건/물건 번호, SHA256, NAS 원본 및 Google Drive 백업까지 검증한 후 완료 판정한다.

## 중요 미완료
- 정식 회원 DB 및 P 장부, 실제 결제 연동이 아직 없다. 별도 rental_members 접속코드는 시험용이며 정식 서비스 인증 대용으로 출시하지 않는다.
- 고객의 개인정보 직접 입력 분리 및 캐시/녹화 미저장, 회원 결과물 전달, 실제 수집 원본과 NAS/Drive 귀속 검증, TLS·실기 배포는 아직 완료되지 않았다.
- 임대형 유휴 정지·복귀를 위한 유휴 임계시간은 설정 전이며 운영정책에서 결정 후 반영해야 한다.
- 12시간은 현재 시험용으로 예약의 누적 실행시간 기준이다.
- 공개 서비스 시작 전 관련 법령·사이트 접근권 및 보안정책 검수 필수. 보안차단이나 CAPTCHA 자동 우회 금지.
- GitHub Actions 사용하지 않음. 현장 Python 시험이 완료된 것처럼 보고하지 않는다.

## 장애·원복
- 관제기 시험 환경변수 BIDGUARD_RENTAL_PILOT_ENABLED를 해제하면 임대 API는 비활성화되며 기존 기능은 그대로 유지.
- 수집기 관제형 메뉴에서 임대형 중지를 선택하면 다음 안전 단계에서 일시정지. 기존 수동 실행과 Pico2 F8은 변경하지 않음.
- 임대 업무 DB, 감사 이벤트, 진행 기록을 원복 과정에서도 임의 삭제하지 않음.

## 책임
Claude: 읽기 전용 검수와 오류·위험·재현 시험 제안. 코드 수정·병합·배포 금지.
Grok: 최종 구현·현장 시험·검수. 기존 기능 변경·삭제는 별도 승인 전 금지.
ChatGPT: 관제 연동 구조·회귀 위험 검수, 실사용 증거와 설계 상태 구분.
