# 로컬 관제기 v1.0 (임시, 사무실 관제기 대신)

작성: 수집기 1호, 2026-11-19 KST. 단독 수집기 v1.6 은 관제기 접속이 필수인데 사무실 관제기 서버가 아직 없어서,
같은 노트북(기본 127.0.0.1 전용)에서 같은 규격(`standalone_collector_v1_6/SERVER_API_KO.md`)을 흉내 내는 서버예요. 표준 라이브러리만(무료).

- 제공: 로그인, `/kb/manifest`(빈 목록), `/refpack/manifest`·`/refpack/file`(묶음 그대로, 암호문), `/refpack/report`(reports.jsonl),
  `/refpack/upload`(incoming/ 에 암호문 저장, 사람 검수 뒤 묶음에 반영), `/account/lock`·`/account/unlock`(같은 PC 안에서만 의미).
- 묶음: `--packs <폴더>\<site_id>\manifest.json + files\` (수집기 `tools/build_site_pack.py` 로 만듦).
- 로그인 아이디·비밀번호: 실행 때 입력(또는 환경변수 BG_CONTROL_USER / BG_CONTROL_PASSWORD). 파일에 저장하지 않고 해시로만 비교.
- 실행: `python local_control_server.py --packs D:\BIDGUARD\control_local\packs --port 8790`
- 사무실 내부망에서 여러 수집기가 쓰려면 `--bind 0.0.0.0` (http 라서 사무실 안에서만, 수집기 `allow_http:true`·`host` 필요).
- 한계: https 아님(같은 PC 전용 권장), 사람 검수·버전 올리기 화면 없음, 계정 잠금은 이 서버를 쓰는 수집기끼리만.
- 시험: `python tests/test_local_control.py` (수집기 v1.6 폴더가 옆에 있어야 함) → 3 run, 0 fail.
