# 관제기(사무실 서버) 요청 규격 — 단독 수집기 v1.6

v1.6 부터 **관제기 접속이 필수**예요. 회차 시작 때 로그인 → 최신 DB 목록 → 사이트 참고자료(조각·화보·site_pack) 받기가
모두 되어야 작업을 시작해요. 하나라도 안 되면 **로컬 DB로 계속하지 않고** STOP 파일(`server_required <사유>`)을 남기고 끝나요.
관제기가 아직 없으면 같은 노트북에서 `court/local_control_server_v1_0/` 의 로컬 관제기를 띄워 같은 규격으로 써요.

## 0. 기본
- 주소: 사무실 내부망. 아직 미정. 수집기 `config.json`의 `kb_server.url`에 `http://<내부주소>:<포트>` 처럼 적어요(끝에 `/` 없이).
- 모든 경로 앞에 `/api/collector/v1` 이 붙어요.
- 로그인 아이디·비밀번호: **대표가 정함(실행 때 입력)**. 수집기는 실행 때 사람에게 직접 입력받고, 평문 파일로 저장하지 않아요(선택: 노트북 Windows DPAPI 암호화 저장).
- 글자: UTF-8, JSON.
- 시간: ISO 8601, KST(+09:00) 권장.
- **기본은 `https://` 만** 받아요(검수 H2). 사무실 내부망 시험으로 `http://` 를 쓰려면 수집기 `config.json`의 `kb_server.allow_http:true` + `host:"<내부주소>"` 를 **직접 켜야만** 됩니다(안 켜면 수집기가 시작 전에 막음). http면 기록에 경고 한 줄 남겨요.
- 수집기는 설정한 **호스트 하나로만** 붙고, 서버가 다른 호스트로 넘기기(301/302 등)를 하면 **따라가지 않고 실패** 처리해요(토큰을 다른 호스트로 안 보냄). 그러니 로그인·파일 받기는 모두 같은 호스트에서 끝내 주세요.
- `https` 를 쓰면 인증서를 검사해요(`verify_tls`, 기본 켬). 내부 사설 인증서면 대표가 `verify_tls:false` 를 직접 꺼야 하고, 그때도 http 와 달리 암호화는 유지돼요.

## 1. 로그인 — `POST /api/collector/v1/auth/login`
요청
```json
{"username": "<대표가 정함>", "password": "<대표가 정함>", "machine": "jijuclub5", "collector_version": "1.0.0-review"}
```
응답 200
```json
{"token": "<임의 문자열>", "expires_in": 3600}
```
- 틀리면 401 `{"error": "bad_login"}` → 수집기는 STOP(`server_required server_unreachable`), 재시도 안 함.
- 토큰은 이후 요청 머리에 `Authorization: Bearer <token>`.
- 서버는 비밀번호를 기록(로그)에 남기지 말아 주세요. 여러 번 틀리면 잠시 막는 것을 권해요.

## 2. 목록(매니페스트) — `GET /api/collector/v1/kb/manifest?have=<수집기가 가진 kb_version>`
응답 200
```json
{
  "kb_version": "2026-11-19.3",
  "generated_at": "2026-11-19T14:00:00+09:00",
  "files": [
    {"path": "errors/error_codes.json", "sha256": "<64자리 소문자>", "size": 261670, "updated_at": "2026-11-19T13:40:00+09:00"},
    {"path": "snippets/top_menu_gyeongmae.png", "sha256": "...", "size": 1820},
    {"path": "snippets/www.yanolja.com/all_category.png", "sha256": "...", "size": 2311},
    {"path": "sites/court_dongbu.json", "sha256": "...", "size": 3400}
  ]
}
```
- `kb_version` 이 수집기 것과 같으면 수집기는 파일을 하나도 받지 않아요(빈 `files` 를 줘도 됨).
- 다르면 수집기가 파일마다 SHA-256 을 자기 것과 비교해 **다른 것만** 받아요.
- `files` 는 서버 DB의 전체 목록(지울 파일 표시는 없음 — 수집기는 로컬 파일을 지우지 않아요).
- 토큰이 없거나 틀리면 401.

### path 규칙 (수집기가 이 밖은 거부)
- `/` 로 구분, 맨 앞 `/`·`\`·`..`·`:` 금지.
- 첫 폴더는 `snippets`, `map`, `errors`, `sites`, `knowhow`, `rules` 중 하나.
- 확장자는 `.png .json .jsonl .md .txt` 만. **프로그램 코드(.py 등)는 DB로 보내지 않아요**(코드는 검수 후 따로 배포).
- `snippet_rank.json`, `kb_manifest.json` 은 수집기 로컬 전용이라 보내도 무시해요.
- 파일 하나 20MB 이하, 한 번에 받을 합계 300MB 이하.

## 3. 파일 받기 — `GET /api/collector/v1/kb/file?path=<path>`
- 응답 200, 본문 = 파일 그대로(`application/octet-stream`).
- 수집기는 받은 본문의 SHA-256 이 매니페스트 값과 같을 때만 바꿔 끼워요. `.json` 은 열어 봐서 JSON 이 맞아야 해요.
- 없으면 404 `{"error":"not_found"}`.

## 4. 수집기 쪽 동작 (서버가 알아 둘 것)
| 상황 | 수집기 |
|---|---|
| 주소 비어 있음 | 시작 안 함, STOP `server_required server_url_missing` |
| 연결 안 됨·시간 초과(8초)·401·이상한 응답 | 시작 안 함, STOP `server_required server_unreachable` |
| 작업 중 잠깐 끊김 | 멈춤 진단(stall_diag) 복구 대기 → 같은 화면에서 이어감(새 회차 시작 안 함) |
| 같은 kb_version | 아무것도 안 받음 (`up_to_date`) |
| 일부 파일 실패(SHA 불일치 등) | **아무것도 안 바꿈**(전부 `.staging/`에 받아 확인 후 전부 맞을 때만 한꺼번에 교체). kb_version 은 옛 값 → 다음 회차 재시도 (`partial`) (검수 M4) |
| 바꿔 끼우기 전 | 원래 파일을 `collector_kb/.backup/<시각>/` 에 보관 |

## 5. 서버 DB 만들 때 권장
- 여러 수집기(1호·2호…)의 노하우를 모아 사람이 검수한 것만 `kb_version` 을 올려 내보내기(관제기 역할: 모아서 최고의 형태로).
- `errors/error_codes.json` 은 지금 수집기 표 형식 그대로(`actions`, `entries`).
- 사이트 단계 JSON(`sites/*.json`) 형식은 단독 수집기 README_KO.md 와 `collector_kb/sites/` 예시 참고.
- 비밀값(키·암호)은 DB에 절대 넣지 않기.

## 5-2. 같은 법원 계정 동시 사용 막기 (검수 H5) — 서버가 있으면
여러 수집기가 **같은 법원 아이디**로 동시에 로그인하면 한쪽이 끊겨요. v1.6 은 관제기가 필수라 아래 잠금을 늘 써요(로컬 관제기는 같은 PC 안에서만 잠금).

### `POST /api/collector/v1/account/lock`
요청 `{"account_tag": "<법원ID의 SHA256 앞 12자>", "collector_id": "nonmetro-01"}`
- 비어 있거나 같은 collector_id면 200 `{"ok": true, "ttl_sec": 900}` (그 수집기가 점유, 주기적으로 다시 호출해 연장).
- 다른 수집기가 쓰는 중이면 409 `{"ok": false, "holder": "metro-01"}` → **그 수집기는 이번 회차 시작 안 함**.
- `account_tag` 는 아이디의 해시라서 서버도 실제 아이디를 몰라요(비밀값 보호).

### `POST /api/collector/v1/account/unlock`
요청 `{"account_tag": "...", "collector_id": "nonmetro-01"}` → 200. 회차 끝(정상·실패 불문)에 풀어 줘요. 서버는 점유에 TTL(예 15분)을 두고, 연장이 끊기면 자동 해제하세요(수집기가 죽어도 영구 잠김 방지).

## 6. 시험
수집기 폴더 `tests/test_standalone.py` 의 `FakeKB`/`serve()` 가 이 규격대로 움직이는 가짜 서버예요. 서버를 만든 뒤 같은 요청을 보내 확인하면 돼요.


## 7. 사이트 참고자료(refpack/site_pack) — v1.6 (t844u·t846u)
모든 자료 파일은 **암호화(BGX1)** 상태로 저장·전송해요. 지금 키는 시험 키 `test`(표시 KEY-UPGRADE-AT-2026-12-01T00:00+09:00),
2026-12-01 00:00 KST 에 새 키로 바꿔요(수집기는 그 시각 뒤에도 시험 키면 시작 안 함). 서버는 파일을 풀 필요 없이 그대로 보관·전달하면 돼요.

### 7-1. `GET /api/collector/v1/refpack/manifest?site_id=<사이트>`
```json
{"site_id": "www.courtauction.go.kr", "grade": "A", "managed": true, "version": "20261119-2300",
 "format": "site_pack/1", "used_bytes": 16000000, "limit_bytes": 524288000,
 "files": [{"path": "site_pack/www.courtauction.go.kr/gallery/open_maegak/normal_full.png",
            "sha256": "<암호문 SHA-256>", "sha256_plain": "<평문 SHA-256>", "bytes": 812345, "enc": "BGX1",
            "kind": "gallery", "step": "open_maegak", "state": "normal", "scale": "full",
            "representative": true, "phash": 1234567890123, "success_rate": null, "ts": 1763560000}]}
```
- 등급: A 500MB(핵심) · B 100MB · C 10MB(대부분) · D 1MB. `managed:false`(관제기가 관리 안 하는 사이트)는 최대 100MB, 기본 C. 법원경매 기본 A.
- 합계(`bytes` 합)가 등급을 넘으면 수집기는 STOP `refpack_over_grade`. `kind:"gallery"`(전체 화면 화보)가 하나도 없으면 STOP `refpack_no_gallery`.
- path 첫 폴더: `snippets`, `map`, `gallery`, `site_pack`. 확장자 `.png .json .jpg .jpeg .jsonl .md .txt`. `..`·`\`·`:`·맨 앞 `/` 금지.
- `kind`: snippet · scale2/4/8 · gallery · gallery_2x/4x/8x · forbidden · steps · errors · routing · rank · timing · spec · knowhow.
- 묶음은 수집기 폴더 `tools/build_site_pack.py` 로 만들어요(manifest.json·files/·SHA256SUMS.txt·build_report.json).

### 7-2. `GET /api/collector/v1/refpack/file?site_id=&path=`
- 본문 = 저장된 바이트 그대로(암호문). 수집기는 SHA-256 이 매니페스트와 같을 때만 쓰고(다르면 STOP `sha_mismatch`), 풀어서 메모리에만 올려요.
- 디스크 임시 캐시 `state/refcache/<사이트>/<버전>/` (암호문, 같은 버전이면 다시 안 받음, 다른 버전 캐시는 지움).

### 7-3. `POST /api/collector/v1/refpack/report` — 회차 끝 보고(정리·시간)
```json
{"type": "refpack_prune", "site_id": "...", "grade": "A", "limit_bytes": 524288000, "before_bytes": 1, "after_bytes": 1,
 "over_limit_after": false, "removed": [{"path": "...", "reason": "duplicate|old", "bytes": 1, "near": "..."}],
 "removed_count": 0, "protected_count": 0, "collector_id": "metro-01", "version": "...", "used_bytes": 1,
 "upload": [{"path": "...", "bytes": 1, "sha256": "...", "kind": "snippet"}], "ts_kst": "2026-11-19T23:00:00+09:00"}
```
- 정리 순서: ① 같은 단계·종류에서 지각 해시 거리 ≤ 6 인 중복·유사 → ② 오래된 것. 대표 화보·성공률 상위 20%·설명/표 파일은 보호.

### 7-4. `POST /api/collector/v1/refpack/upload` — 새로 배운 자료(정리 후 남은 것만, 등급 한도 안)
`{"site_id","path","sha256"(평문),"sha256_enc","enc":"BGX1","kind","data_b64"(암호문)}` → 200 `{"ok": true}`.
서버는 사람 검수 뒤 다음 버전 묶음에 반영해요. 판단 담당표(`decision_routing.json`)·반응시간(`timing.json`)도 이 길로 올라와요(비밀값 없음).

### 7-5. 시험
수집기 `tests/test_standalone.py` 의 `FakeControl`(T22·T25), 그리고 `court/local_control_server_v1_0/tests/test_local_control.py`(실제 로컬 관제기 + 수집기).
