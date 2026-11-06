# 법원경매정보 수집기

## 설치
1. `install.bat` 실행
2. 수집기 이름 입력
   - 보급형: 사용자 ID
   - 관제형: jiju*** 로 시작
3. 유형 선택 (1=보급형, 2=관제형)

## 실행
```bat
venv\Scripts\activate.bat
python main.py
```

## 환경
- 수집기: Windows
- 관제기: Ubuntu (서버)

## AI 계정 입력
- 보급형·관제형 모두 개인 AI 계정 입력 메뉴 필요
- 사용자가 설치된 컴퓨터에서 수작업으로 입력
- 관제형도 내장 API 외에 개인 계정 추가 가능

## 현재 지원
- 감정평가서 다운로드 흐름 (맵 기반 열람하기 다이얼로그 처리)
- 사이트 맵 로드
- 보급형 / 관제형 구분
- API 키(Gemini 등): 관제기에서 가져옴 (수집기는 보관하지 않음)
- 감정평가서 다운로드: 지장 요소 철저 배제 (방해 로직·불필요 재시도·불완전 연결 분리)

## 관제기 메뉴 (추가)
- 수집기 AI 계정 설정 (개인 계정 등록·수정·우선순위)
- 수집기 IP 주소 설정 (연결·식별)

## 다음 단계
- 실제 피코2 (HID) 연동
- 대화창 UI
- 중앙 관제 연동 (관제형)

## 관제형 로그인 (관제형 수집기만)

관제기로부터 허락된 행위에 한해:
- 아이디/암호를 **직접 입력**하거나
- 브라우저에 **자동 저장된 기록**이 있으면 이를 활용하여 로그인

보급형 수집기는 이 기능을 사용할 수 없습니다.

```python
from core.login import ControlTypeLogin

login = ControlTypeLogin("수집기-A", collector_type="control")
result = login.login(
    site="courtauction.go.kr",
    username="user",          # 직접 입력 시
    password="pass",
    use_browser_saved=True,   # 자동 저장 기록 우선 시도
    permission_from_controller=True,
)
```
