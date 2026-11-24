# 사건 목록 형식 (queue_today.json)
실행기 run_today.ps1는 plans\queue_today.json 을 읽습니다. 값은 JSON 배열입니다.

필수 필드(3호가 실제로 쓴 형식):
- p: 우선순위 숫자 (작을수록 먼저). 당일 매각(낙찰)된 물건은 가장 먼저 둡니다.
- court: 화면에 보이는 법원 이름 (예: 서울동부지방법원)
- code: 법원 코드 (서울중앙 B000210, 동부 B000211, 남부 B000212, 북부 B000213, 서부 B000215)
- case: 사건번호 (예: 2025타경12345)
- year, num: 검색칸에 넣을 연도와 숫자
- folder: 저장 폴더명 (예: seouldongbu_2025-12345)
- items: 물건번호 배열 (예: ["1"])
- alts: 법원 이름 OCR 대안 (예: ["서울동부지방법원", "서울동부"])

선택:
- syear, snum: 병합 사건일 때, 검색은 이 번호로 하고 저장·확인은 case/year/num 으로 합니다.
- have: 이미 받았으면 true (실행기는 주로 results.jsonl의 ok/failed/skipped/notice로 건너뜁니다)

견본: queue_today.sample.json (가짜 번호 00000). 3호의 인천·경기 목록은 넣지 않았습니다.
