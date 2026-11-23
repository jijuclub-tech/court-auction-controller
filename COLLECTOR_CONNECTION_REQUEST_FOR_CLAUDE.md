# 수집기 ↔ 관제기 연결 요청서 (Claude용)

**작성일**: 2026-11-06  
**대상**: Claude  
**범위**: 수집기 UI + 관제기 연결 (업무 지시 수신·수행)

---

## 제외 사항 (이번 작업에서 다루지 말 것)

- `core/human_input.py` (HumanHID, 베지어, 가변 타이핑 등)
- 피코2 입력 구현 상세
- Selenium / Playwright 자동 우회 로직
- 보안문자 자동 인식·우회

위 부분은 별도로 다시 추가할 예정이므로, 이번 요청에서는 **연결·지시 수신·UI**만 다룬다.

---

## 1. 수집기 UI 수정 (이미 반영된 것 확인·보완)

| 항목 | 내용 |
|------|------|
| 배율 옵션 | 삭제 (100% 경고 없음) |
| 로그인 | 테스트용 `admin` / `admin` 기본값 |
| 옵션 창 | 버튼 문구 **저장 닫기** |
| Collect 탭 (관제형) | "관제형 수집기는 관제기의 지시를 수행합니다." 만 표시 |
| Collect 탭 (보급형) | "수집할 내용 입력하세요" (빈 입력란) |
| 하드코딩 | `2025타경56881` 기본값 제거 |

---

## 2. 관제기 ↔ 수집기 연결 (핵심)

### 2.1 수집기 → 관제기
- Settings에서 관제기 주소 입력 후 연결
- `POST /api/collector/connect` 로 이름·유형·사이트 전달
- 연결 성공 시 Status에 "Controller: connected" 표시

### 2.2 관제기 → 수집기 (업무 지시)
- 관제기에서 지시서 발행 (`generate_instruction`)
- 수집기가 `GET /api/collector/instruction/<name>` 으로 지시서 수령
- 관제형: Collect 탭에서 지시 대기 상태 표시, 지시가 오면 수행
- 지시서에 포함된 사건번호·작업 유형·저장 경로 사용

### 2.3 결과 보고
- 수집 완료 후 관제기에 결과 보고 (기존 `reporter.py` 활용)
- 관제기에서 작업 상태 갱신

---

## 3. 파일 범위

**수정 가능**
- `new_collector/ui/app.py` (UI, 연결, 지시 수신)
- `new_collector/core/login.py` (로그인 호출만)
- `new_collector/core/appraisal.py` (다운로드 호출만)
- `new_collector/core/reporter.py` (보고)
- `controller/web_ui.py` (지시 발행 UI, 이미 PR #1에서 개선됨)

**수정 금지**
- `new_collector/core/human_input.py`
- `new_collector/core/captcha.py` (자동 우회 관련)
- 피코2 관련 구현

---

## 4. 기대 결과

1. 관제기에서 수집기 등록 → 목록에 표시
2. 관제기에서 업무 지시 발행 → 대기 지시서 목록에 표시
3. 수집기에서 관제기 연결 → 지시서 수령
4. 관제형 수집기는 지시서대로 동작 (사건번호 직접 입력 없음)
5. 결과 보고 후 관제기 작업 상태 갱신

---

## 5. 검수

- Claude 수정 후 ChatGPT 검수 (원인만)
- Grok이 Human 부분 다시 추가 후 최종 패키지 생성
