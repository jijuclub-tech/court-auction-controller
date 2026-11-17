# S-0.1.2-CONTROL 검수 요청 (쳇GPT)

브랜치: `claude/controller-S012-learning-20261117` (S-0.1.1 브랜치 위에 덧붙임)
관리인 지시 (2026-11-17): 관제기에 학습서 기능. 수집기가 사이트 방문을 알리면 새로 학습된 자료를 보냄.
학습서 작성·검수 AI는 메뉴에서 관리인이 5개(글록·쳇GPT·제미나이·클로드·딥시크) 중 선택, 기본 작성 = 글록 봇, 검수 = 쳇GPT.

바뀐 파일
- `learning.py` (새 파일): 경험 기록, 단계별 통계(코드로 셈, AI 안 씀), 작성→검수→관리인 승인(시험/전체 적용/되돌리기), 방문 시 학습서 전달, 5개 AI 호출
- `controller.py`: learning 연결 4곳만 (기존 주소·동작 변경 없음), 판 S-0.1.2-CONTROL
- `admin.html`: '학습서' 탭 추가 (기존 탭 그대로)
- `install/install_controller.sh`: 파일 목록에 learning.py, 기본 브랜치·판
- `test_controller.py`: 시험 4개 추가 (모두 14개 통과)
- `docs/LEARNING_PROTOCOL.md`: 수집기 쪽이 부를 주소 약속

검수 부탁 (고치지 말고 문제만)
1. 기존 주소·기능이 줄거나 바뀐 곳이 없는지
2. 경험 기록에 입력 글자·암호·사진이 저장될 길이 없는지
3. API 키가 화면·기록·DB로 새는 길이 없는지 (키 파일 0600)
4. 작업이 멈춘 채로 남는 경우(관제기 재시작, API 실패, 봇 무응답)
5. 각 AI API 호출 형식(OpenAI, Anthropic, Gemini, DeepSeek, xAI)
6. 수집기 쪽 약속(LEARNING_PROTOCOL.md)이 기존 수집기 구조와 맞는지

실제 AI 키로 호출하는 시험은 하지 않았습니다 (NOT VERIFIED). 시험에서는 AI 호출을 가짜로 바꿔 흐름만 확인했습니다.
