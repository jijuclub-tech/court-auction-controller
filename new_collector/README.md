# 그록 버전 수집기

법원경매정보 수집기 — HumanHID(피코2) 포함 전체 버전

## 특징
- **HumanHID**: 베지어 곡선 마우스 이동, 가변 속도(0.5x~1.8x), 오타·백스페이스, 미세 정지
- 피코2 전용 입력 (Selenium/Playwright 자동 우회 없음)
- 관제기 연결 → 업무 지시 수신·수행 (관제형)
- 감정평가서 다운로드, 등기부등본, 보안문자(사람 호출)
- 보급형 / 관제형 구분 (이름 `jiju####` → 자동 관제형)

## 설치
1. 압축 해제
2. `install.bat` 실행
3. 수집기 이름 입력
   - 관제형: `jiju0001` 형태
   - 보급형: 그 외
4. 바탕화면 바로가기 생성 확인

## 실행
- 바로가기 또는 `run.bat`

## 관제기 연동
Settings 탭에서 관제기 주소 입력 후 연결  
관제형: Status → Start → 지시 대기

## 포함 모듈
- `core/human_input.py` — HumanHID
- `core/login.py` — 관제형 로그인 (HumanHID)
- `core/appraisal.py` — 감정평가서
- `core/captcha.py` — 보안문자 (사람 호출)
- `core/access_policy.py` — 공공기관 자동 금지
- `ui/app.py` — Tkinter UI

## 환경
- Windows 10/11
- Python 3.10+
- 피코2 (실제 HID 입력 시)
