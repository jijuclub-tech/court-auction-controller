# 단독 수집기 v1.7.0-4p (보조공학 4원칙 운영 가드)

날짜: 2026-11-20 (KST Asia/Seoul)  
근거: 정한영 직접 지시 t134u, 지휘자 전달, 최상위 개발 지시「기능 삭제 금지, 개발 계속」.  
원본 `standalone_collector_v1_6` 은 손대지 않고 이 폴더만 고쳤다. 노트북·법원·GitHub 반영 없음.

## 원칙
기능 코드(_login, vk_login, DPAPI 저장, 임대 resume 지우기)는 남긴다. 기본 동작만 원칙에 맞게 끈다.

## 설정표
`collector_kb/settings/assistive_agent.json` — 상한은 이 표에서 바꾸되 천장(ceiling)을 넘을 수 없다.

정한 기본값:
- pico_proxy_login: false (사람이 로그인 완료 후 시작)
- wait_for_human_login: true, human_login_wait_sec: 600
- reuse_saved_login_secrets: false
- require_operator_present: true, operator_present_max_age_sec: 900 (CLI `--i-am-here` 또는 OPERATOR_PRESENT 파일)
- max_run_minutes: 30, max_run_minutes_ceiling: 120, apply_time_cap: true
- min_gap_sec / court_min_gap_sec: 1.0
- html_per_connection: 5 (천장 10), max_html_total: 100 (천장 200), max_items_per_day: 80
- require_local_session_lock / single_session_per_machine: true
- lease_resume_requires_tenant_confirm: true, tenant_confirm_max_age_sec: 600

## 항목
1. 로그인 아이디·비밀번호는 본인이 입력. Pico2 대신 입력은 정책으로 꺼 두고 코드는 남김.
2. 무인 모드는 사람이 그 자리에서 시작했을 때만. 저장 비밀번호 자동 재사용 안 함. 실행 시간 상한.
3. 수집 속도·HTML 저장량에 서버 부하 상한.
4. 1인 1세션. 관제기 없을 때는 같은 PC 로컬 `single_session.lock`과 안전한 기본(첫 세션만 허용). 여러 PC를 관제기 없이 막는 것은 관제기가 필요하다.
5. 임대 재개는 임차인이 그 시각에 직접 지시(TENANT_RESUME_CONFIRM 또는 관제기 확인)했을 때만.

## 시험
`PYTHONPATH=tests:. python3 tests/run_all.py` (기존+신규 모의시험). 상자만, 법원·노트북 접속 없음.

## t1013u 통합 사건 폴더 (2026-11-20)

마이옥션·법원 자료를 출처 폴더로 나누지 않고, 사건번호 하나(`seouldongbu_2023-57705/appraisal.pdf`)로 저장한다.
설정표: `collector_kb/settings/storage_path_table.json` (공용 원본 `court/cases/storage_path_table.json`).
규칙 문서: `/workspace/bidguard-shared/court/cases/README_KO.md`.
출처·받은 시각·SHA256 은 사건 폴더의 `SOURCES.json` 에만 적는다. SHA 가 같으면 파일 하나, 다르면 `_v2`.
기존 파일은 옮기지 않는다. v1.6 원본·노트북은 바꾸지 않았다.
모의시험: `tests/test_case_paths.py`.
