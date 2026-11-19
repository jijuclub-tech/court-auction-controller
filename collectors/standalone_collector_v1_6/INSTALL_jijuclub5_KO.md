# 노트북 jijuclub5 설치·실행 (단독 수집기 v1.6 + 로컬 관제기)

작성: 수집기 1호, 2026-11-19 KST. 상자에서 시험만 했고 노트북에서는 아직 돌려 보지 않았어요(처음 실행은 사람이 화면을 보며).
jijuclub5 = 1호(수도권, `collector_id: metro-01`, `region: metro`).

## 0. 관제기(사무실 서버)는 지금 어떻게 하나
- **사무실 관제기 서버 주소는 아직 없어요**. 관제기 API(수집기 쪽 규격 `SERVER_API_KO.md`)는 아직 만들어지지 않았어요.
  공용 기록에도 "관제기 API가 아직 없어 전송하지 않음"으로 남아 있고, 실제 주소는 정해지지 않았어요.
- v1.6 은 관제기 접속이 **필수**라 그대로는 시작하지 않아요(STOP `server_required server_url_missing`/`server_unreachable`).
- 그래서 **같은 노트북에 로컬 관제기**를 띄워요: `court/local_control_server_v1_0/local_control_server.py`
  (표준 라이브러리만, 127.0.0.1 에만 열림). 수집기는 이것을 관제기로 보고 똑같이 로그인·자료 받기·보고·올리기를 해요.
- 나중에 사무실 관제기가 생기면 `config.json` 의 `kb_server` 만 바꾸면 돼요(아래 6).

## 1. 설치 경로
| 무엇 | 노트북 경로 |
|---|---|
| 수집기 v1.6 | `D:\BIDGUARD\standalone_collector_v1_6\` (공용 폴더 `court/standalone_collector_v1_6/` 통째 복사) |
| 로컬 관제기 | `D:\BIDGUARD\control_local\` (공용 폴더 `court/local_control_server_v1_0/` 통째 복사) |
| 참고자료 묶음 | `D:\BIDGUARD\control_local\packs\www.courtauction.go.kr\` (아래 3 에서 만듦) |
| 수집 결과 | 그대로 `D:\BIDGUARD\court\` (`storage.local_root`), NAS `X:\BIDGUARD\court` |

파이썬: 기존 1호 가상환경을 써도 되고(`D:\BIDGUARD\Collector_S0322\auto_flow_S0325\.venv_af\Scripts\python.exe`), 새로 만들려면
`cd D:\BIDGUARD\standalone_collector_v1_6` → `py -3.13 -m venv .venv` → `.venv\Scripts\python.exe -m pip install -r requirements.txt`
(pyserial·numpy·pillow·winocr, 모두 무료). 아래에서 `PY` = 그 python.exe 경로.

## 2. 기존 D:\BIDGUARD 자료와의 호환
- 옛 폴더(`Collector_S0322`, `standalone_collector_v1_5` 등)는 **건드리지 않아요**. v1.6 은 자기 폴더 안의 `collector_kb`·`state`·`logs` 만 써요.
- 수집 결과 저장 위치(`D:\BIDGUARD\court`, NAS)는 v1.5 와 같아요.
- 옛 평문 `snippet_rank.json`·`screen_timing_local.json` 은 읽을 수 있고, **다음 저장 때 암호문으로 바뀌어요**. 그래서 v1.6 의 `collector_kb` 를
  옛 판(v1.5 이하)과 **함께 쓰지 마세요**(옛 판은 암호문을 못 읽음).
- 옛 법원 화면 사진(.png)은 3 에서 화보로 바꿔 넣어요. 이름에 `loading/error/popup`(또는 로딩/오류/팝업)이 들어가면 그 상태로, `인증서/cert` 가 들어가면 금지 그림으로 따로 분류돼요.

## 3. 참고자료 묶음 만들기(처음 1번, 사진이 늘면 다시)
```
cd D:\BIDGUARD\standalone_collector_v1_6
PY tools\build_site_pack.py --kb collector_kb --site www.courtauction.go.kr --shots <법원 화면 사진 폴더> --out D:\BIDGUARD\control_local\packs\www.courtauction.go.kr
```
- `<법원 화면 사진 폴더>` 에는 **전체 화면 사진이 최소 1장** 있어야 해요(없으면 수집기가 `refpack_no_gallery` 로 시작 안 함).
- 비밀값이 든 파일(이름·내용 검사)은 빠지고 `build_report.json` 의 `excluded_secret` 에 적혀요. 등급(법원경매 A 500MB)을 넘으면 정리돼요.
- 결과: `manifest.json`, `files\`(암호문), `SHA256SUMS.txt`, `build_report.json`.

## 4. 로컬 관제기 띄우기(수집 전마다, 창 하나를 켜 둠)
```
cd D:\BIDGUARD\control_local
PY local_control_server.py --packs D:\BIDGUARD\control_local\packs --port 8790
```
- 아이디·비밀번호를 물어요(화면에 안 보임, 파일에 저장 안 함). **수집기 `keys` 의 'DB 서버 아이디/비밀번호'와 같게** 정해요.
- `[관제기] http://127.0.0.1:8790 에서 대기. 묶음: www.courtauction.go.kr` 가 보이면 준비 끝. 이 창은 수집 내내 켜 둬요.

## 5. 수집기 설정·키(처음 1번)
- `config.example.json` → `config.json` 복사. 이미 `kb_server.url: "http://127.0.0.1:8790"`, `allow_http: true`, `host: "127.0.0.1"`,
  `collector_id: "metro-01"`, `region: "metro"` 로 되어 있어요. `crypto.key` 는 시험 키 `test`(2026-12-01 00:00 KST 전까지).
- `PY -m standalone keys` → 딥시크·제미나이 키, 법원 아이디·비밀번호, DB 서버(=로컬 관제기) 아이디·비밀번호 입력, DPAPI 저장 '예'.

## 6. 시작 명령 한 줄(로컬 관제기 창이 켜진 상태에서)
```
cd /d D:\BIDGUARD\standalone_collector_v1_6 && .venv\Scripts\python.exe -m standalone run collector_kb\sites\court_dongbu.json --minutes 30
```
- 화면에 `[수집기] 해당 사이트 학습 중입니다…` → `학습 완료 ...` → `작업 중` → 끝나면 `끝: 메모리 해제`.
- 시작이 안 되면 `STOP_court_dongbu` 파일 첫 줄에 사유가 있어요: `server_required server_unreachable`(관제기 창 꺼짐·비번 다름),
  `refpack_no_gallery`(3 다시), `key_upgrade_required`(2026-12-01 뒤 시험 키) 등. 원인을 고치고 사람이 STOP 파일을 지워요.
- 사무실 관제기가 생기면: `kb_server` 를 `{"url": "https://<사무실 관제기 주소>", "allow_http": false, "host": "", "verify_tls": true}` 로 바꾸고,
  묶음(3 의 packs 폴더 내용)을 사무실 관제기로 옮겨요. 로컬 관제기는 끄면 돼요.

## 7. 2026-12-01 00:00 KST 키 갱신
- 그 시각부터 시험 키 `test` 로는 시작 안 해요(경고 + STOP `key_upgrade_required`).
- `config.json` 의 `crypto.key_source` 를 `"dpapi"` 로 → `PY -m standalone keys` 에서 DATA_KEY(새 키) 입력.
- 새 키로 묶음을 다시 만들어야 해요(3 다시, 같은 DATA_KEY). `PY tools\key_upgrade_audit.py` 로 시험 키 자리 목록 확인.
