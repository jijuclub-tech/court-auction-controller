# BIDGUARD 관제탑 탐색정보 중앙 동기화 — 시험판 (2026-11-20)

## 목적
기존 수집기가 찾아낸 사이트 탐색·학습 정보는 중앙 관제탑이 검수하고, 외부 독립 설치형 수집기는 승인된 버전을 HTTPS로 받아 갱신한다. 새 정보를 발견한 외부 수집기는 **탐색 구조 메타데이터만 제안**할 수 있으며, 관리자 승인 전에는 다른 수집기에 배포하지 않는다.

## 현행 소스 재사용
- `learning.py`: 기존 `learn_books`, `for_visit()` 승인 학습서를 유지. `/api/collector/exploration/sync`에 사이트별 승인 학습서를 함께 전달.
- `site_map_ingest.py`: 기존 공식 사이트 구조지도의 수신·다운로드 API `/api/v1/site-maps/batches`, `/api/v1/site-maps/latest` 보존. 새 `exploration_registry.py`는 구조 선택자 버전 관리와 관리자 승인만 담당하며, 기존 원본·지도 파일과 중복 저장하지 않는다.
- `collector_dispatch.py`: 기존 사건·물건·문서 중복방지/ACK/lease 및 해당 DB 보존.
- 독립형 기존 로컬 `local_data/`, `local_data/controller_learning/`와 별도로 `local_data/controller_exploration/<site_id>/`에 승인 버전 사본 저장.

## API
| 주체 | API | 역할 |
|---|---|---|
| 등록 수집기 | `GET /api/collector/exploration/catalog?name=...` | 등록된 사이트 및 최신 승인 버전 |
| 등록 수집기 | `GET /api/collector/exploration/sync?name=...&site_id=...&have_version=N` | 변경분(버전+SHA256) 및 기존 학습서 |
| 등록 수집기 | `POST /api/collector/exploration/propose` | 새 탐색 정보 **제안**·멱등 접수 |
| 관리자 | `GET /api/admin/exploration/state` | 제출 대기·사이트별 승인 버전 |
| 관리자 | `POST /api/admin/exploration/decision` | 제안 승인·반려, 승인 시 버전 승격 |

관제탑 메뉴 `/admin/exploration`에서 승인 처리. `/admin` 네비게이션에서 '탐색자료'로 진입.

## 허용되는 제안 데이터
```json
{
  "name":"independent-1",
  "site_id":"courtauction",
  "request_key":"request-example-0001",
  "snapshot":{
    "url":"https://www.courtauction.go.kr/search",
    "captured_at":"2026-11-20T00:00:00+00:00",
    "page_kind":"search",
    "structure_sha256":"<원본 화면구조에서 계산한 SHA256 64자리>",
    "elements":[{"name":"search","selector":"#search"},
                {"name":"results","selector":".results"}]
  }
}
```
`url`은 관제기에 등록된 HTTPS 사이트 도메인에 일치해야 한다. 댓글 전문·HTML 본문·스크린샷·계정/비밀번호/쿠키/세션·키/개인정보는 이 API에 전달하지 않는다. 수집기 등록 및 비밀 토큰, 관리자 인증, 로컬 역프록시/TLS 연결이 필수다. 외부 장치가 접근차단·약관을 회피해서 자료를 확보하는 기능이 아니다.

## 운영 순서
1. 관제탑 시험 서버 DB/학습서/지도 정보를 먼저 백업하고, 기존 프로그램을 새로 설치하는 대신 **증분 코드**를 검수한다.
2. 관리자 화면에서 기존 `collectors` 장치 ID·승인 상태·장치별 토큰을 확인한다. 운영자 개인 인증이 없는 수집기는 이 API 사용 불가.
3. 독립 설치형 수집기는 기존 로컬 내용 보존, '관제탑 갱신' 선택 또는 연결 상태에서 매시간 메타데이터/학습서 갱신. 갱신 실패 시 마지막으로 검증된 로컬 버전 유지.
4. 새 탐색 패턴을 발견한 수집기는 인증된 `propose` API에 구조 변경 제안을 접수. 승인 전 데이터는 중앙 관리 화면에만 나타난다.
5. 관리자가 사이트·출처·변경 구조를 확인하고 승인 → 새 번호 버전 생성 → 외부 수집기 다음 폴링에서 해시 검증 후 수신.
6. 모든 제안/승인/반려를 중앙 SQLite 이력에 저장한다. 이전 승인 버전은 변경·삭제 금지.
7. 관제기 원본 및 메타DB를 실제 NAS 마운트 확인 후 암호화 백업, Google Drive 24시간 복구 정책에 연계한다. **이 단계는 현장 연결·복구 시험 전**이다.

## 실험/검수 명령
관제탑:
```
python3 -m unittest -v test_exploration_registry test_controller test_independent_ai_policy
```
Windows 독립형:
```
cd standalone_collector
python -m unittest -v test_exploration_sync test_controller_ai_client test_independent_collector test_local_manager
```
실험 항목: 새 등록/중복 요청/잘못된 토큰/비인가 관리자/다른 도메인/민감 필드/해시 위조/승인 전 미배포/버전 순차 배포/같은 버전 재호출/오프라인 기존 사본 유지/사이트 학습서만 존재/장치 2대 교대 갱신/Win·Ubuntu 실제 연결.

## 글록 수집기 정보 확인 상태
Grok 저장 경로 조사 지시 `court-auction-controller/issues/26`은 GitHub 등록됐으나 **2026-11-20 확인 시 보고 댓글 0건**. 현장 실제 파일·NAS·Drive 경로, 건수, 최종시각은 확인되지 않음. 기록된 소스 기본 경로는 실제 파일 존재를 의미하지 않음.

## 역할
- ChatGPT: 중앙 통신·검수용 코드와 최소 기능 UI 작성.
- **Claude: 읽기 전용 실험 검토 및 의견만. 코드 수정·커밋·PR 생성·병합·배포 금지.**
- Grok: 전체 수집기 실제 데이터 경로 확보·관제탑/NAS·Drive 원본 대조 후 최종 설치·현장 시험.
- 시험 모듈이 작성됐다는 사실은 테스트 PASS나 정식 서비스 배포를 의미하지 않는다.
