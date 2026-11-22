# 수집 PC 상태 (pc_health) G v1.0.0

한영님 지시(2026-11-22): 노트북 감시 루틴이 확인하는 연결·배터리·충전기·봇 호출·사람 호출을 관제기에서 볼 수 있게 한다.  
상태값 자체는 GitHub에 커밋하지 않는다. 이 문서는 프로그램과 JSON 형식만 설명한다.

관련 이슈: #28. Python 모듈 파일명은 import 를 위해 기존 `T_v0_2` 처럼 점을 밑줄로 쓴다 (`pc_health_G_v1_0_0.py`). 문서와 PC 실행 스크립트는 `*_G_v1.0.0.*` 이다.

## 수집 PC 스크립트

경로: `collectors/standalone_collector_v1_6/report_pc_health_G_v1.0.0.py`  
PC에 복사할 위치: `D:\BIDGUARD\standalone_collector_v1_6_v8\` (새 PC는 `C:\BIDGUARD\...`).

```
py -3 report_pc_health_G_v1.0.0.py --collector-root D:\BIDGUARD\standalone_collector_v1_6_v8 --controller-url http://관제기주소:8770
```

- 항상 `<수집기>\state\pc_health.json` 에 기록한다. 관제기 주소가 없거나 POST 가 실패해도 파일은 남는다.
- 읽는 값: `Win32_Battery` 의 `EstimatedChargeRemaining`, `BatteryStatus==2`(충전; CIM 충전값 6~9 도 충전으로 봄), `state\bot_ask\pending.json` 존재 여부, `state\human_call_latest.json` 의 `time`, 수집 프로세스 실행 여부, 시각(KST), 머신 이름.
- 키·비밀번호·ID 파일은 읽거나 출력하지 않는다. 작업 스케줄러로 20분마다 실행하면 된다.
- 관제기에서 `CONTROLLER_PC_HEALTH_TOKEN` 을 쓰려면 PC에도 같은 값을 환경변수 또는 `--token` 으로만 넣고, 화면에 찍지 않는다.

## JSON 형식 (`bidguard.pc_health.v1`)

```json
{
  "schema": "bidguard.pc_health.v1",
  "observed_at": "2026-11-22T13:23:00+09:00",
  "machine_name": "jijuclub5",
  "battery_percent": 96,
  "charging": true,
  "ac_plugged": true,
  "bot_ask_pending": false,
  "human_call_time": "2026-11-21T11:55:00+09:00",
  "collector_running": true,
  "collector_root": "D:\\BIDGUARD\\standalone_collector_v1_6_v8"
}
```

숫자는 0–100, 모르면 `null`. `human_call_time` 이 없으면 `null`. 비밀 필드명은 거부한다.

## 관제기

- `POST /api/collector/pc_health` : 수집 PC가 보낸다. 수집기 접속 키는 쓰지 않는다. `CONTROLLER_PC_HEALTH_TOKEN` 이 있으면 헤더 `X-PC-Health-Token` 이 같다.
- `GET /api/admin/pc_health` : 관리인 토큰 필요. `/api/admin/state` 의 `pc_health` 에도 같은 스냅샷이 들어간다.
- `/admin` 수집기 탭의 **수집 PC 상태** 카드에 표시한다.
- 경고: 마지막 보고 후 N분 경과(기본 30, 환경변수 `PC_HEALTH_OFFLINE_MIN` 또는 `?offline_min=`), 충전기 빠짐, 배터리 20% 미만, 최근 24시간 안의 사람 호출. 봇 호출 대기는 상태로 표시한다.

## 시험

저장소 루트에서 `python test_pc_health_G_v1_0_0.py`.
