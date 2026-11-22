# 지휘자 반복 업무 자동화 T-v0.1 (이슈 #43)

정한영 지시(2026-11-23 04:21 KST): 지휘자 업무를 프로그램·루틴으로 만들어 관제기가 실행한다.
**홀수일은 지휘자 봇, 짝수일은 관제기 자동화**가 하고, 성과를 비교해 지휘자 봇이 관제기 운영
프로그램과 규칙을 계속 고친다. **짝수일이어도 정한영 지시는 예외**로 지휘자가 바로 처리한다.

모듈: `conductor_automation_T_v0_1.py` (표준 라이브러리만, 유료 서비스·외부 AI 호출 없음).
`controller.py`에는 init·route·스레드 연결 3곳만 더했고 기존 주소·기능은 그대로입니다.

## 1. 누가 하는가 (KST 날짜의 '일')

| 경우 | 기록 actor | 관제기 동작 |
|---|---|---|
| 홀수일 | `conductor` | 하지 않음 |
| 짝수일 + 정한영 지시 예외 등록 | `conductor` | 하지 않음 |
| 짝수일 + 작업이 **실기 확인 전**(verified=0) | `conductor_fallback` (지휘자가 기록) | **그림자 실행**: 결과는 `ops/out/shadow/`에만, 봇 지시 안 보냄 |
| 짝수일 + 실기 확인됨 | `controller` | 실제 실행, 봇 지시 큐에 넣음 |

- 31일 → 1일처럼 홀수가 이어지는 날도 날짜 숫자 그대로 판정합니다.
- 실기 확인은 관리인이 `POST /auto/admin/task {"task":..,"verified":true,"note":"근거"}`로 작업별로 켭니다. 근거(note) 없이는 켤 수 없습니다.
- 메타 작업 `parity_compare_report`(23:40 비교 리포트)는 홀짝과 무관하게 관제기가 합니다.

## 2. 작업 목록 (시각 KST, 처음 설치 때 DB에 등록)

| task | 일정 | 하는 일 | 입력(비공개 운영 폴더 `ops/`) |
|---|---|---|---|
| ai_usage_compare | 매일 18:00 | 봇 보고 수집, 감소율 그래프(SVG), 미보고·미감소·최하위 벌점, 벌점 봇에 통지 | `bots.json`, `ai_usage/<날짜>/<봇>.json` `{"ai_calls":N}` |
| bot_health_check | 2시간마다 :05 | 상태 파일 오래됨(기본 150분), 막힘, `*.flag`, 이슈 새 댓글 대기 수 | `bots/<봇>/status.json` `{"updated_at","state","blocked","reason"}` |
| regional_news_assign | 일요일 09:00 | 60개 봇에 지역 배정(모든 봇 1곳 이상) | `news/regions.json` `{"targets":[..]}` |
| regional_news_score | 일요일 20:00 | 100점 표 채점, 기준의 70% 미만 재작업 지시, 요약(md) | `news/regional/<날짜>/<봇>.json` |
| world_news_assign | 월요일 09:00 | 국가별 배정 + 언론사 목록 요구(#42) | `news/countries.json` |
| world_news_score | 월요일 20:00 | 100점 표(언론사 목록 10점 포함), 재작업, 요약 | `news/world/<날짜>/<봇>.json` |
| auction_price_fill(_pm) | 11/23~27 10:00, 16:00 | 봇 결과를 매각물건에 합치고, 빈 칸은 20건씩 봇에 배정 | `auction/sold_items.json`, `auction/price_results/*.json` |
| issue_reply_digest | 매시 :20 | GitHub 이슈 새 댓글 → 본문에 이름이 나온 봇에게 전달, 나머지는 지휘자 목록 | 환경 `CONDUCTOR_GITHUB_REPOS`, 선택 `CONDUCTOR_GITHUB_TOKEN` |
| parity_compare_report | 매일 23:40 | 최근 14일 홀짝 비교 | 실행 기록 |

자료 파일이 없으면 지어내지 않고 **실패로 기록**한 뒤 재시도합니다(5→15→45분, 작업별 횟수).
6시간 넘게 지난 일정은 뒤늦게 돌리지 않고 '놓친 일'로 셉니다. 시작만 하고 30분 넘게 끝 기록이 없으면 실패로 보고 다시 합니다.

### 뉴스 100점 표
지역: 수량 30 · 출처 URL 20 · 기간(7일) 내 15 · 중복 없음 15 · 요약 40자 이상 10 · 지역 일치 10.
세계: 수량 25 · 출처 20 · 기간 15 · 중복 10 · 요약 10 · 국가 일치 10 · 언론사 목록(배정 국가 대비) 10.
질 항목은 `max(제출 수, 최소 기사 수)`로 나눕니다(1건만 내고 비율만 높은 경우 방지).
기준 점수 `CONDUCTOR_NEWS_BASELINE`(기본 100) × 0.70 미만이면 재작업. 최소 기사 수 `CONDUCTOR_NEWS_MIN_ITEMS`(기본 10).
> 가정: 이슈의 '기준'을 고정 기준점수로 보았습니다. 다른 기준(예: 지난주 평균)이면 알려 주세요.

## 3. 봇에게 지시 보내기 (pull)

관제기는 Grok 채팅에 직접 보낼 수 없으므로 **관제기가 큐를 만들고, 봇이 시작할 때 가져갑니다.**

1. 봇 시작 시: `GET /auto/bot/orders?bot=<봇이름>` (헤더 `X-Bot-Token`) → 유효 지시 목록(`order_id`, `kind`, `body`, `sha256`, `expires_at`).
2. 받으면 `POST /auto/bot/ack {"bot","order_id","outcome":"received"}`, 끝나면 `done` 또는 `failed`(+`note`).
3. HTTP를 못 쓰는 봇은 공용 폴더 사본 `ops/queue/<봇>.json`을 읽고, 지휘자가 대신 ACK 합니다.
- 같은 봇·작업·회차·종류의 지시는 하나만 만들어집니다(재시도해도 중복 없음). 아직 안 가져간 지시만 내용이 갱신됩니다.
- 기한이 지난 지시는 내려가지 않습니다. 닫힌 지시에 다른 결과로 ACK 하면 409.

## 4. 실행 기록

`auto_runs` 표: `date, parity, actor(conductor|controller|conductor_fallback), task, started, finished, ok, items, errors, ai_steps, notes`
\+ `mode(live|shadow|meta)`, `slot`, `attempt`, `expected_actor`, `rule_ok`, `source`.

지휘자는 자기가 한 일을 기록합니다(홀수일·예외·fallback):
```
POST /auto/run-log   (X-Bot-Token)
{"actor":"conductor","task":"ai_usage_compare","started":"2026-11-23T18:00:00+09:00",
 "finished":"2026-11-23T18:20:00+09:00","ok":true,"items":60,"ai_steps":4,"errors":[],"notes":""}
```
또는 서버에서 `python3 conductor_automation_T_v0_1.py log '<같은 JSON>'`.
그날 규칙과 다른 actor로 기록하면 받되 `rule_ok=0`(규칙 위반)으로 표시됩니다.

정한영 지시 예외: `POST /auto/exemption {"date":"2026-11-24","task":"*" 또는 작업명,"note":"지시 요지"}`.

## 5. 홀짝 성과 비교

`GET /auto/admin/report?from=YYYY-MM-DD&to=YYYY-MM-DD` (JSON + markdown), CLI `report`,
매일 23:40 `ops/out/reports/compare_latest.md`.
작업별 홀/짝: 성공률(예정 회차 대비 성공), 평균 시간, 평균 AI 단계, 놓친 일, 짝수일 수행자 분포, 규칙 위반, 그림자 성공 수.

## 6. 주소 요약 (모두 HTTPS 또는 신뢰 프록시, #41과 같은 판정)

| 주소 | 권한 |
|---|---|
| GET /auto/today | 봇 |
| GET /auto/bot/orders, POST /auto/bot/ack | 봇 |
| POST /auto/run-log, POST /auto/exemption | 봇(지휘자) |
| GET /auto/admin/status, GET /auto/admin/report | 관리인 |
| POST /auto/admin/task (enabled / verified) | 관리인 |
| POST /auto/admin/run `{"task","shadow":true}` | 관리인 (기본 그림자) |

시험키 `test`는 2026-12-01 00:00 KST부터 `/auto/*`에서 거부합니다.
`CONDUCTOR_AUTOMATION=0`이면 관제기 안 일정 스레드를 켜지 않습니다(그때는 cron으로 `... tick`).

## 7. 실기 확인 순서 (병합 전)

1. 운영 서버 `ops/`에 실제 `bots.json`, `news/regions.json`, `news/countries.json`, `auction/sold_items.json` 배치 (GitHub에 올리지 않음).
2. 짝수일에 그림자 결과(`ops/out/shadow/`)와 지휘자 `conductor_fallback` 결과를 비교.
3. 맞으면 작업별로 `verified=true` + 근거. 이후 짝수일부터 관제기가 실제로 지시를 보냄.
4. 봇 쪽 pull/ACK 연동(Grok 담당)과 지휘자 run-log 기록이 붙어야 비교 리포트가 의미 있습니다.

## 미확인 / 남은 일
- 실제 운영 서버·봇·공용 폴더 연결 시험 **미실행**. 합성 자료 단위시험만 통과.
- 봇 pull 클라이언트, 지휘자 run-log 호출은 봇 쪽 작업이 별도로 필요합니다.
- 봇별 개별 토큰이 아니라 공용 `CONTROLLER_BOT_TOKEN` 하나로 봇을 구분합니다(봇 이름은 자기 신고). 봇별 키가 필요하면 #31 키 지갑 방식 적용 검토.
- 관리인 화면(admin.html) 탭은 아직 없습니다. 지금은 API/CLI로 봅니다.
