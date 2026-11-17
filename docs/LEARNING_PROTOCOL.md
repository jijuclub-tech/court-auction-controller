# 학습서 주소 약속 (S-0.1.2-CONTROL)

관제기 쪽은 만들어 두었습니다. 수집기 쪽(코드 담당: 쳇GPT)은 아래 두 주소를 부르도록 덧붙이면 됩니다.
기존 주소와 기능은 그대로입니다. 수집기 토큰(`X-Collector-Token`)은 기존과 같이 씁니다.

## 1. 경험 기록 보내기 — `POST /api/collector/experience`

수집 사이사이(단계가 끝날 때마다, 또는 몇 건 모아서) 보냅니다. 한 번에 1~200건.

```json
{"name": "jiju01",
 "records": [
  {"site_id": "courtauction", "route_id": "court_appraisal", "step": 4,
   "screen": "물건상세", "action": "CLICK", "x": 312, "y": 88,
   "result": "fail", "seconds": 6.2, "level": 2, "ai": "gemini",
   "popup": "알림창이 이름을 가림", "error": "체크칸 50px 빗나감", "book_version": 3}
 ]}
```

- `result`: `ok` / `fail` / `skip` 중 하나 (필수). `site_id` 필수.
- `level`: 그 단계를 푼 AI 단계(1~4). `ai`: 실제로 쓴 AI 이름.
- `book_version`: 그때 쓰고 있던 학습서 판 (없으면 0). 판별 성공률 비교에 씁니다.
- 위에 없는 칸(입력한 글자, 암호, 화면 사진 등)은 관제기가 버리고 저장하지 않습니다. 그래도 보내지 않는 것이 원칙입니다.

## 2. 사이트 들어갈 때 — `POST /api/collector/visit`

```json
{"name": "jiju01", "site_id": "courtauction", "version": 3}
```

`version`은 수집기가 가진 학습서 판(없으면 생략). 답:

```json
{"ok": true, "site_id": "courtauction", "version": 4, "trial": false,
 "book": {"summary": "...", "changes": ["..."],
  "steps": [{"route_id": "court_appraisal", "step": 4, "screen": "물건상세",
             "difficulty": "hard", "start_level": 2, "hint": "...", "wait_seconds": 5,
             "obstacles": [{"sign": "알림창", "response": "닫기 먼저"}]}]}}
```

- 같은 판이면 `"unchanged": true`이고 `book`이 없습니다.
- 승인된 학습서가 없으면 `"book": null` → 기존 정해진 경로대로 합니다.
- `start_level`: 쉬움 1(딥시크 등 1단계부터), 어려움 2(제미나이 등 2단계부터). 그 단계의 기존 `start_level` 대신 씁니다.
- `trial: true`는 이 수집기가 시험 대상이라는 뜻입니다.

## 3. 글록 봇 (작성 AI가 글록 '봇' 방식일 때)

- `GET /api/bot/learning` (`X-Bot-Token`) → 할 일 목록 `[{id, stage: "write"|"review", site_id, prompt, input}]`
- `POST /api/bot/learning/<id>` `{"stage": "write", "result": {학습서 JSON}}`
- 검수도 글록 봇에게 맡겼다면 `{"stage": "review", "result": {"verdict": "pass|fix", "problems": [...], "note": ""}}`
- 봇은 자기 담당 단계만 답할 수 있습니다.
