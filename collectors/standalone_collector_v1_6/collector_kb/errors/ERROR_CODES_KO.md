# 수집기 오류 코드 대응표 (사람용) — 2026-11-19 수집기 1호

프로그램은 `error_codes.json`을, 감지는 `page_guard.py`를 씁니다. 페이지가 바뀔 때마다·클릭 전마다 화면 OCR·창 제목으로 이 표를 먼저 확인하고, 문제가 없을 때만 마우스를 움직입니다. AI는 쓰지 않습니다.

- 전체 항목: **377개** (실제 겪은 것 26개, 나머지는 미발생(예방))
- HTTP 47개(IANA 등록 3xx·4xx·5xx 전부), 크롬 ERR_* 266개(chromium net_error_list.h 전부), Cloudflare 32개, 차단·대기열·한글 문구·법원 화면·노트북 32개
- 우회 금지: 캡차·차단·인증서·결제는 누르지 않고 멈춥니다. 피코2 입력·OCR만 씁니다.

## 1. 대응 종류 (심각한 순)

| 대응 | 할 일 | 재시도 | 지휘자 보고 |
|---|---|---|---|
| `STOP_REPORT` | 즉시 멈춤·아무것도 안 누름·화면 저장·지휘자 보고·사람 확인 | 0 | 예 |
| `BLOCK_STOP_REST` | 차단 화면: 즉시 멈춤·보고·최소 180분 쉼(우회 금지) | 0 | 예 |
| `RATE_LIMIT` | 요청 과다: 마우스 멈춤→창 닫기→즉시 로그아웃→최소 120분 쉼→요청 간격 2배로 재개 | 1 | 예 |
| `FORCED_LOGOUT` | 다른 곳 로그인: 멈춤·재로그인 안 함·같은 아이디 쓰는 다른 수집기 확인·보고 | 0 | 예 |
| `QUEUE_WAIT` | 접속 대기열: 새로고침·클릭 금지, 그대로 최대 10분 기다림, 넘으면 멈춤·보고 | 0 | 예 |
| `SERVER_ERROR_WAIT` | 서버 오류(5xx): 로그아웃 뒤 30분 쉬고 1회 재시도, 또 나면 멈춤·보고 | 1 | 예 |
| `MAINTENANCE_WAIT` | 점검 중: 로그아웃, 끝 시각이 보이면 그 뒤 5분, 없으면 30분 쉬고 1회 재시도 | 1 | 예 |
| `NETWORK_WAIT` | 연결 오류: 60초 쉬고 인터넷 확인 뒤 1회 다시, 두 번째도 실패면 300초 뒤 1회, 그래도면 멈춤·보고 | 2 | 예 |
| `SESSION_RELOGIN` | 세션 만료/로그아웃됨: 멈춤 → 재로그인 1회(아이디 탭만) → 실패면 멈춤·보고 | 1 | 아니요 |
| `CLIENT_ERROR_BACK` | 요청 오류(4xx 기타): 크롬 뒤로 1번, 그 건은 retry_list에 넣고 다음 건, 연속 2회면 멈춤·보고 | 1 | 예 |
| `PAGE_SKIP` | 없는 페이지(404/410): 뒤로 1번, 그 건 건너뜀, 연속 3회면 멈춤·보고 | 0 | 아니요 |
| `KNOWN_HANDLER` | 이미 있는 처리 코드가 맡음(page_guard는 멈추지 않음) | - | 아니요 |
| `INFO` | 보통 화면에 안 보임·대응 없음(참고) | - | 아니요 |

429(`RATE_LIMIT`) 순서: 마우스 멈춤 30~60초(그 사이 아무 요청 없음) → 감정평가서 뷰어·팝업 창 '닫기'(새 요청 없는 클릭만) → 상단 '로그아웃' 보이면 누르고 확인창 '예'(court_logout_confirm_yes) → 로그아웃 글자가 안 보이면(오류 화면) 새로고침 금지, 크롬 뒤로 1번만 → 그래도 없으면 법원 탭 닫기 → 최소 120분 휴식(BG_RATE_REST_MIN) → 재개 시 물건 사이 간격·시간당 상한을 2배로 줄여(간격×2) 시작 → 같은 날 두 번째 429 → 그날 법원 회차 끝 + 지휘자 보고

## 2. 실제 겪은 오류

| 코드 | 내용 | 대응 | 언제·무엇 | 근거 |
|---|---|---|---|---|
| `HTTP_429` | 429 Too Many Requests — 요청이 너무 많음 | `RATE_LIMIT` | 2026-11-19 12:44~12:47 KST 1호 동부 회차: not_list→closed_popup→no_save_dialog 뒤 사이트가 429 Too Many Requests. 수집기는 감지 못 하고 계속 요청, 한영님이 12:47 직접 로그아웃. 회차 52건 저장, 마지막 2025타경52339 저장 창 안 뜸 | `한영님 보고 t799u/t800u(대화), 노트북 D:\BIDGUARD\local_data\appraisals\2026-11-19\log.jsonl(상자에 없음·미확인)` |
| `HTTP_429` | 429 Too Many Requests — 요청이 너무 많음 | `RATE_LIMIT` | 2026-11-17 09:24 KST 경쟁사 감시: press RSS HTTP 429(법원 아님) | `competitor/automation/METHODS.md:18` |
| `AKAMAI_ACCESS_DENIED` | Akamai Access Denied (Reference #18.…, 보통 HTTP 403) | `BLOCK_STOP_REST` | 2026-11-17 경 뉴스 지도 작업: hankyung Akamai 403(법원 아님) | `map/site-map-shots/METHODS.md:47` |
| `RECAPTCHA_CHECKBOX` | reCAPTCHA 체크칸 '로봇이 아닙니다' | `KNOWN_HANDLER` | 2026-11-19 08:54 자가시험 robot_check_seen | `court/collector_for_2ho/learning_pack_1ho_2026-11-19/collector_knowhow/selftest_20261118/incidents.jsonl:1` |
| `SITE_MAINTENANCE` | 서비스 점검 중 | `MAINTENANCE_WAIT` | 2026-11-19 12:59 네이버 항공권 /flights/city 404 뒤 '일시적으로 서비스를 이용할 수 없습니다' 화면(법원 아님, 다시 시도 안 함) | `map/site-map-shots/flight.naver.com/2026-11-19/MAP_1ho_KO.md:20` |
| `SESSION_EXPIRED` | 세션 만료·자동 로그아웃·로그인 필요 | `SESSION_RELOGIN` | 2026-11-17 ~03:05 I010 세션 만료로 로그아웃(30분 연장 안 함), not_list로 끝 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I010)` |
| `CERT_LOGIN_PAGE` | 인증서 로그인 화면·인증서 선택창 | `STOP_REPORT` | 2026-11-19 로그인 탭 클릭 순간 인증서 설치 안내창이 늦게 떠 클릭을 가로챔 | `map/blockages/collector1_login_cert_late_modal_2026-11-19.md` |
| `SECURITY_PROGRAM` | 보안프로그램 설치 요구(TouchEn nxKey 등) | `STOP_REPORT` | 2026-11-17 03:14~03:16 I011 재로그인 중 설치 안내창 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I011)` |
| `PAGE_NOT_FOUND_KO` | 한글 '페이지를 찾을 수 없습니다' | `PAGE_SKIP` | 2026-11-19 12:59 네이버 항공권 /flights/city/ HTTP 404(법원 아님) | `map/site-map-shots/flight.naver.com/2026-11-19/MAP_1ho_KO.md:20` |
| `COURT_APPRAISAL_NOTICE` | 감정평가서 열람 안내(문서 일시 열람 불가) | `KNOWN_HANDLER` | 2026-11-17 03:02 I009 2025타경51071 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I009)` |
| `COURT_NO_SAVE_DIALOG` | PDF 저장 창 안 뜸(no_save_dialog) | `KNOWN_HANDLER` | 2026-11-17 01:30~01:32 I003 2024타경56862·56954 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I003)` |
| `COURT_NO_SAVE_DIALOG` | PDF 저장 창 안 뜸(no_save_dialog) | `KNOWN_HANDLER` | 2026-11-19 12:44~12:46 429 직전 no_save_dialog(2025타경52339) | `대화 기록(부모 요약)·노트북 log.jsonl` |
| `COURT_NOT_LIST` | 목록 화면 아님(not_list) | `KNOWN_HANDLER` | 2026-11-19 08:48 not_list(펌웨어 키 거절) | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/incidents.jsonl:1` |
| `COURT_NOT_LIST` | 목록 화면 아님(not_list) | `KNOWN_HANDLER` | 2026-11-19 11:07 not_list_recover_failed → Gemini '이전'으로 해결 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/resolved.jsonl:1` |
| `COURT_VIEWER_NOT_OPEN` | 감정평가서 뷰어 안 열림 | `KNOWN_HANDLER` | 2026-11-17 01:40 I005 2024타경64245(관심물건 창이 덜 닫힘) | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I005)` |
| `COURT_HOME_POPUP_STACK` | 홈 공지 팝업 2개 겹침('오늘 하루 열지않음'은 체크만 됨) | `KNOWN_HANDLER` | 2026-11-19 10:35~11:40 2호 menu_bar_not_ready 원인 | `map/blockages/collector2_lg15_morning_zero_2026-11-19.md` |
| `COURT_LOGOUT_CONFIRM` | 로그아웃 확인창(예/아니요) | `KNOWN_HANDLER` | 2026-11-17 04:17 I017 확인창 '예' 필요 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I017)` |
| `COURT_TIMER_UNREADABLE` | 남은시간 못 읽음 | `KNOWN_HANDLER` | 2026-11-17 03:20 I012 '2727'로 읽힘 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I012)` |
| `COURT_VK_LOGIN_FAIL` | 가상키패드 로그인 반복 실패 | `KNOWN_HANDLER` | 2026-11-17 00:40~00:59 I001 키패드 빈칸 무작위 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I001)` |
| `COURT_APPRAISAL_BTN_OFF` | 매각 다음 날 감정평가서 버튼 꺼짐 | `KNOWN_HANDLER` | 2026-11-17 매각결과 화면 버튼 회색 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/장애별_회피방법.md` |
| `COURT_OCR_CASE_MISREAD` | 사건번호 OCR 오독으로 같은 건 재저장 | `KNOWN_HANDLER` | 2026-11-17 02:39~04:15 I008 8건 재저장 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/incidents.jsonl (I008)` |
| `LOCAL_PICO_KEY_REFUSED` | 피코2 펌웨어 키 목록 밖 명령(CTRL+MINUS 등) | `KNOWN_HANDLER` | 2026-11-19 08:48 RefusedAction → NEEDS_1HO | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/incidents.jsonl:1` |
| `LOCAL_NAS_MISSING` | NAS X: 미연결(재부팅 뒤) | `KNOWN_HANDLER` | 2026-11-19 10:58 2호 c2_prep_out FileNotFoundError X: | `map/blockages/collector2_lg15_morning_zero_2026-11-19.md` |
| `LOCAL_CHROME_COURT_NOT_FOUND` | 크롬 법원경매 창 없음/가려짐 | `KNOWN_HANDLER` | 2026-11-19 10:59 2호 CHROME_COURT_NOT_FOUND | `map/blockages/collector2_lg15_morning_zero_2026-11-19.md` |
| `LOCAL_CHROME_COURT_NOT_FOUND` | 크롬 법원경매 창 없음/가려짐 | `KNOWN_HANDLER` | 2026-11-19 10:09 전 Grok Bot 창이 크롬을 가림(MAEGAK_NOT_FOUND) | `map/blockages/collector2_lg15_court_menu_2026-11-19.md` |
| `LOCAL_PICO_PORT_BUSY` | 피코2 연결 안 됨(COM 포트 사용 중) | `KNOWN_HANDLER` | 2026-11-17 U001 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/kb_export/jijuclub5-laptop/ui_failures.jsonl (U001)` |
| `AI_API_MODEL_404` | Gemini 모델 404(gemini-2.5-flash-lite 신규 사용자 중단) | `KNOWN_HANDLER` | 2026-11-18 AI 호출 404 → gemini-3.5-flash-lite로 바꿈 | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/selftest_real_result_20261118.json` |
| `HTTP_403` | 403 Forbidden — 금지됨(권한 없음·차단) | `BLOCK_STOP_REST` | 2026-11-17 경 뉴스: hankyung Akamai 403, zdnet 목록 403, ajunews /economy 403 | `map/site-map-shots/METHODS.md:47` |
| `HTTP_403` | 403 Forbidden — 금지됨(권한 없음·차단) | `BLOCK_STOP_REST` | 2026-11-19 08:0x 경쟁사 지표: MBA 403 | `competitor/automation/METHODS.md:22` |
| `HTTP_404` | 404 Not Found — 없는 페이지 | `PAGE_SKIP` | 2026-11-19 12:59 네이버 항공권 /flights/city/ 404 | `map/site-map-shots/flight.naver.com/2026-11-19/MAP_1ho_KO.md:20` |
| `HTTP_404` | 404 Not Found — 없는 페이지 | `PAGE_SKIP` | 2026-11-18 Gemini API 모델 404(사이트 아님) | `court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/selftest_real_result_20261118.json` |
| `HTTP_500` | 500 Internal Server Error — 서버 내부 오류 | `SERVER_ERROR_WAIT` | 미상 realtor-exam 원본 호스트 HTTP 500 | `realtor-exam/raw/15/SOURCE_UNAVAILABLE.txt(/workspace)` |

## 3. 차단·대기열·한글 문구·법원·노트북 항목

| 코드 | 내용 | 감지 | 사이트 | 대응 | 재시도 | 보고 | 구분 | 출처 |
|---|---|---|---|---|---|---|---|---|
| `CF_1015` | Cloudflare Error 1015 You are being rate limited (HTTP 429) | OCR: 'You are being rate limited'; 정규식: `Error\s*1015`; 함께 보여야: cloudflare, Ray ID, rate limited | all | `RATE_LIMIT` | 1 | 예 | 미발생(예방) | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/#error-1015-you-are-being-rate-limited |
| `CF_1025` | Cloudflare Error 1025 Please check back later (HTTP 429) | 정규식: `Error\s*1025`; 함께 보여야: cloudflare, Ray ID, check back later | all | `RATE_LIMIT` | 1 | 예 | 미발생(예방) | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/#error-1025-please-check-back-later |
| `AKAMAI_ACCESS_DENIED` | Akamai Access Denied (Reference #18.…, 보통 HTTP 403) — Microsoft 문서: 짧은 시간 연결이 많으면 Akamai가 IP를 자동 차단, 보통 일시적. 새로고침 연타 금지. | OCR: 'You don't have permission to access', 'You don’t have permission to access', 'errors.edgesuite.net'; 정규식: `Reference\s*#\s*\d+\.[0-9a-f]+`; 창 제목: Access Denied | all | `BLOCK_STOP_REST` | 0 | 예 | 실제발생 | https://learn.microsoft.com/en-us/troubleshoot/windows-server/networking/access-denied-visit-website-hosted-akamai-cdn / https://techdocs.akamai.com/edge-diagnostics/docs/translate-error-string |
| `WAF_REQUEST_REJECTED` | 방화벽 거부 'The requested URL was rejected'(F5 계열) | OCR: 'The requested URL was rejected', 'Request Rejected', 'Web Page Blocked', '웹 방화벽' | all | `BLOCK_STOP_REST` | 0 | 예 | 미발생(예방) | 내부 규칙: fw102/c3/BIDGUARD_collector_2ho/Collector_S0322/core/prompt_policy.py HUMAN_KEYWORDS (공식 출처 미확인) |
| `CF_BLOCKED` | Cloudflare 접근 거부 1005~1012·1020·1034·1106 'Sorry, you have been blocked' | OCR: 'Sorry, you have been blocked', 'you have been blocked'; 정규식: `Error\s*10(?:0[5-9]|1[0-2]|20|34)\b`, `Error\s*1106\b`; 함께 보여야: cloudflare, Ray ID, blocked, Access denied | all | `BLOCK_STOP_REST` | 0 | 예 | 미발생(예방) | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/ |
| `CF_CHALLENGE` | Cloudflare 봇 확인 화면(Just a moment / Verify you are human) — 확인칸 자동 클릭 금지(우회 금지). | OCR: 'Verify you are human', 'Checking your browser', 'Checking if the site connection is secure', '사람인지 확인', '연결이 안전한지 확인'; 창 제목: Just a moment | all | `STOP_REPORT` | 0 | 예 | 미발생(예방) | https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/ |
| `SITE_ACCESS_BLOCK` | 한글 접속 제한·차단·비정상 접근 문구 — 법원에서는 아직 안 나옴. 기존 guard()의 BAD 목록(자동입력·보안문자·captcha·비정상·접속이 제한·차단)은 그대로 둠. | OCR: '접속이 제한', '접근이 제한', '접근이 거부', '접속이 차단', '접근이 차단', '차단되었습니다' … | all | `BLOCK_STOP_REST` | 0 | 예 | 미발생(예방) | 내부 규칙: court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/known_issues.json(ai_blocked_access_block), _1ho_dl_loop.py BAD 목록 |
| `CAPTCHA_IMAGE` | 그림 캡차·보안문자·자동입력방지 — 실제 그림 캡차 화면 기록은 없음(자가시험 분류만: selftest_real_result_20261118.json). 숫자 캡차는 기존 규칙(t761u: AI 1회 입력, 실패 시 사람에게 사진) 그대로 — page_guard가 대신하지 않음. | OCR: '자동입력방지', '자동입력 방지', '보안문자', '이미지를 모두 선택', 'Select all images', '그림문제' | all | `STOP_REPORT` | 0 | 예 | 미발생(예방) | 내부 규칙: court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/known_issues.json(ai_blocked_image_captcha) |
| `RECAPTCHA_CHECKBOX` | reCAPTCHA 체크칸 '로봇이 아닙니다' — 법원 감정평가서 열람 체크칸은 기존 robot_check()가 처리(t576u·t621u). 그림 문제로 넘어가면 CAPTCHA_IMAGE. | OCR: '로봇이 아닙니다', 'not a robot', '캡차를 체크' | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U006 (/workspace/court/v2/) |
| `NETFUNNEL_QUEUE` | NetFunnel 접속 대기열 '서비스 접속대기 중입니다' — 2호 c2_visit_sites.py BLOCK_WORDS와 같은 낱말. 예약 사이트에서 흔함. | OCR: '서비스 접속 대기 중', '서비스 접속대기 중', '접속대기 중', '접속 대기 중', '대기순번', '대기 순번' … | all,reservation | `QUEUE_WAIT` | 0 | 예 | 미발생(예방) | https://www.stclab.com/en/products/netfunnel / https://www.bai.go.kr/waitingroom/main.html |
| `SITE_MAINTENANCE` | 서비스 점검 중 — 공지 목록 속 '점검 안내(미래 날짜)'는 '중입니다' 꼴이 아니라 대부분 안 잡힘. | OCR: '점검 중입니다', '점검중입니다', '점검 중 입니다', '서비스 점검 중', '시스템 점검 중', '정기점검 중' … | all | `MAINTENANCE_WAIT` | 1 | 예 | 실제발생 | 내부 규칙: core/court_live/collector.py OUTAGE_DEFAULTS maintenance_texts, court/v2 U024 |
| `SESSION_EXPIRED` | 세션 만료·자동 로그아웃·로그인 필요 — 수집기 스스로 로그아웃한 직후에는 ignore=['SESSION_EXPIRED']로 부름. | OCR: '세션이 만료', '세션이 종료', '세션 만료', '로그인 시간이 만료', '로그인이 만료', '로그인 만료' … | court,iros,all | `SESSION_RELOGIN` | 1 | 아니요 | 실제발생 | 내부 규칙: court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/known_issues.json(known_login_expired) |
| `FORCED_LOGOUT` | 다른 곳에서 로그인·중복 로그인 — 2호는 로그인 없이만 수집(11:57 결정)이라 지금은 1호만 법원 아이디 사용. | OCR: '다른 곳에서 로그인', '다른곳에서 로그인', '다른 PC에서 로그인', '다른 기기에서 로그인', '다른 장소에서 로그인', '중복 로그인' … | court,iros,all | `FORCED_LOGOUT` | 0 | 예 | 미발생(예방) | court/v2/장애별_회피방법.md FORCED_LOGOUT (/workspace/court/v2/) |
| `PAY_OR_ISSUE` | 결제·발급·수수료 화면 | OCR: '결제하기', '카드결제', '발급하기', '수수료 결제' | all | `STOP_REPORT` | 0 | 예 | 미발생(예방) | 내부 규칙: court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/known_issues.json(ai_blocked_paid) |
| `CERT_LOGIN_PAGE` | 인증서 로그인 화면·인증서 선택창 — 설치 물음 '아니요'는 기존 known_cert_install_prompt가 처리(멈추지 않음). 인증서 로그인 화면 자체면 멈춤. | OCR: '공동인증서', '금융인증서', '인증서 선택', '인증서 암호' | court,iros | `STOP_REPORT` | 0 | 예 | 실제발생 | 내부 규칙: court/collector_for_2ho/full_copy_1ho_20261119/local_data/rules/never_click_인증서_로그인.json |
| `SECURITY_PROGRAM` | 보안프로그램 설치 요구(TouchEn nxKey 등) | OCR: 'TouchEn', 'nxKey', '키보드보안', '보안프로그램 설치', '보안 프로그램 설치' | all | `STOP_REPORT` | 0 | 예 | 실제발생 | 내부 규칙: court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/known_issues.json(ai_blocked_security_program) |
| `WIN_UAC` | Windows 사용자 계정 컨트롤(UAC) | OCR: '사용자 계정 컨트롤', '디바이스를 변경할 수 있도록'; 창 제목: 사용자 계정 컨트롤 | all | `STOP_REPORT` | 0 | 예 | 미발생(예방) | 내부 규칙: court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/known_issues.json(ai_blocked_uac) |
| `CHROME_KO_CANT_REACH` | 크롬 '사이트에 연결할 수 없음'·'인터넷에 연결되어 있지 않음' — 한글 문구는 크롬 판마다 조금 다를 수 있음(2호 c2_visit_sites.py ERROR_WORDS와 같음). 화면 아래 ERR_* 코드가 확실한 근거. | OCR: '사이트에 연결할 수 없음', '사이트에연결할수없음', '인터넷에 연결되어 있지 않음', '응답하는 데 시간이 너무 오래 걸립니다', '연결이 끊어졌습니다' | all | `NETWORK_WAIT` | 2 | 예 | 미발생(예방) | https://support.google.com/chrome/answer/6098869?hl=ko |
| `CHROME_KO_PRIVACY` | 크롬 '연결이 비공개로 설정되어 있지 않습니다'(인증서 오류) — '고급→계속' 누르기 금지. | OCR: '연결이 비공개로 설정되어 있지 않습니다', '연결이비공개', 'Your connection is not private' | all | `STOP_REPORT` | 0 | 예 | 미발생(예방) | https://support.google.com/chrome/answer/6098869?hl=ko |
| `CHROME_KO_PAGE_BROKEN` | 크롬 '이 페이지가 작동하지 않습니다'(HTTP ERROR 숫자)·'앗, 이런!' — 같은 화면에 'HTTP ERROR 429' 등 숫자가 있으면 그 HTTP 항목이 우선(심각도 높음). | OCR: '이 페이지가 작동하지 않습니다', '페이지가 작동하지 않습니다', '앗, 이런!', '앗, 이런', 'Aw, Snap' | all | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://support.google.com/chrome/answer/6098869?hl=ko |
| `PAGE_NOT_FOUND_KO` | 한글 '페이지를 찾을 수 없습니다' | OCR: '페이지를 찾을 수 없', '요청하신 페이지를 찾을 수 없', '존재하지 않는 페이지' | all | `PAGE_SKIP` | 0 | 아니요 | 실제발생 | 2호 court/collector2_lg15/tools/c2_visit_sites.py ERROR_WORDS |
| `COURT_APPRAISAL_NOTICE` | 감정평가서 열람 안내(문서 일시 열람 불가) — appraisal_notice_title 조각 → retry_list·닫기·다음 건(process_band). | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U009 |
| `COURT_NO_SAVE_DIALOG` | PDF 저장 창 안 뜸(no_save_dialog) — 새 규칙: no_save_dialog가 나면 바로 check_page()로 화면 전체+창 제목을 읽음. 2건 연속이면 속도 제한 의심 → 10분 쉼(INTEGRATION_KO.md). | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U010 |
| `COURT_NOT_LIST` | 목록 화면 아님(not_list) | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U014 |
| `COURT_VIEWER_NOT_OPEN` | 감정평가서 뷰어 안 열림 | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U011 |
| `COURT_HOME_POPUP_STACK` | 홈 공지 팝업 2개 겹침('오늘 하루 열지않음'은 체크만 됨) | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/collector2_lg15/tools/c2_close_popup.py |
| `COURT_LOGOUT_CONFIRM` | 로그아웃 확인창(예/아니요) | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/collector_for_2ho/full_copy_1ho_20261119/local_data/rules/court_logout_confirm_yes.json |
| `COURT_TIMER_UNREADABLE` | 남은시간 못 읽음 | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U013 |
| `COURT_VK_LOGIN_FAIL` | 가상키패드 로그인 반복 실패 | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/collector_for_2ho/full_copy_1ho_20261119/local_data/rules/court_vk_login_success_20261117.json |
| `COURT_APPRAISAL_BTN_OFF` | 매각 다음 날 감정평가서 버튼 꺼짐 | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_kb/장애별_회피방법.md |
| `COURT_OCR_CASE_MISREAD` | 사건번호 OCR 오독으로 같은 건 재저장 | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U017 |
| `LOCAL_PICO_KEY_REFUSED` | 피코2 펌웨어 키 목록 밖 명령(CTRL+MINUS 등) | 로그 이벤트(기존 코드) | all | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/known_issues.json(ai_blocked_firmware_key) |
| `LOCAL_NAS_MISSING` | NAS X: 미연결(재부팅 뒤) | 로그 이벤트(기존 코드) | all | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | map/blockages/collector2_lg15_morning_zero_2026-11-19.md |
| `LOCAL_CHROME_COURT_NOT_FOUND` | 크롬 법원경매 창 없음/가려짐 | 로그 이벤트(기존 코드) | court | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/collector2_lg15/tools/c2_front_chrome.py |
| `LOCAL_PICO_PORT_BUSY` | 피코2 연결 안 됨(COM 포트 사용 중) | 로그 이벤트(기존 코드) | all | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/v2/장애별_회피방법.md U001 |
| `AI_API_MODEL_404` | Gemini 모델 404(gemini-2.5-flash-lite 신규 사용자 중단) | 로그 이벤트(기존 코드) | ai | `KNOWN_HANDLER` | None | 아니요 | 실제발생 | court/collector_for_2ho/full_copy_1ho_20261119/local_data/collector_knowhow/README_ko.md:21 |

## 4. HTTP 상태 코드 (IANA 등록 전부, 3xx는 참고)

| 코드 | 뜻 | 대응 | 재시도 | 보고 | 구분 | 출처 |
|---|---|---|---|---|---|---|
| `HTTP_300` | 300 Multiple Choices — 여러 선택지 | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.1 |
| `HTTP_301` | 301 Moved Permanently — 영구 이동 | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.2 |
| `HTTP_302` | 302 Found — 임시 이동 | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.3 |
| `HTTP_303` | 303 See Other — 다른 주소 보기 | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.4 |
| `HTTP_304` | 304 Not Modified — 바뀐 것 없음(캐시) | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.5 |
| `HTTP_305` | 305 Use Proxy — 프록시 사용(폐기) | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.6 |
| `HTTP_307` | 307 Temporary Redirect — 임시 이동(방식 유지) | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.8 |
| `HTTP_308` | 308 Permanent Redirect — 영구 이동(방식 유지) | `INFO` | - | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.4.9 |
| `HTTP_400` | 400 Bad Request — 잘못된 요청 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.1 |
| `HTTP_401` | 401 Unauthorized — 인증 필요(로그인 필요) | `SESSION_RELOGIN` | 1 | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.2 |
| `HTTP_402` | 402 Payment Required — 결제 필요 | `STOP_REPORT` | 0 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.3 |
| `HTTP_403` | 403 Forbidden — 금지됨(권한 없음·차단) | `BLOCK_STOP_REST` | 0 | 예 | 실제발생 | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.4 |
| `HTTP_404` | 404 Not Found — 없는 페이지 | `PAGE_SKIP` | 0 | 아니요 | 실제발생 | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.5 |
| `HTTP_405` | 405 Method Not Allowed — 허용 안 되는 방식 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.6 |
| `HTTP_406` | 406 Not Acceptable — 받을 수 없는 형식 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.7 |
| `HTTP_407` | 407 Proxy Authentication Required — 프록시 인증 필요 | `STOP_REPORT` | 0 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.8 |
| `HTTP_408` | 408 Request Timeout — 요청 시간 초과 | `NETWORK_WAIT` | 2 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.9 |
| `HTTP_409` | 409 Conflict — 충돌 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.10 |
| `HTTP_410` | 410 Gone — 영구 삭제됨 | `PAGE_SKIP` | 0 | 아니요 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.11 |
| `HTTP_411` | 411 Length Required — 길이 필요 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.12 |
| `HTTP_412` | 412 Precondition Failed — 전제 조건 실패 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.13 |
| `HTTP_413` | 413 Content Too Large — 내용이 너무 큼 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.14 |
| `HTTP_414` | 414 URI Too Long — 주소가 너무 김 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.15 |
| `HTTP_415` | 415 Unsupported Media Type — 지원 안 하는 형식 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.16 |
| `HTTP_416` | 416 Range Not Satisfiable — 범위 오류 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.17 |
| `HTTP_417` | 417 Expectation Failed — 기대 조건 실패 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.18 |
| `HTTP_421` | 421 Misdirected Request — 잘못 보낸 요청 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.20 |
| `HTTP_422` | 422 Unprocessable Content — 처리할 수 없는 내용 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.21 |
| `HTTP_423` | 423 Locked — 잠김 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc4918 |
| `HTTP_424` | 424 Failed Dependency — 의존 요청 실패 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc4918 |
| `HTTP_425` | 425 Too Early — 너무 이름 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc8470 |
| `HTTP_426` | 426 Upgrade Required — 업그레이드 필요 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.5.22 |
| `HTTP_428` | 428 Precondition Required — 전제 조건 필요 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc6585 |
| `HTTP_429` | 429 Too Many Requests — 요청이 너무 많음 | `RATE_LIMIT` | 1 | 예 | 실제발생 | https://www.rfc-editor.org/rfc/rfc6585#section-4 |
| `HTTP_431` | 431 Request Header Fields Too Large — 요청 헤더가 너무 큼 | `CLIENT_ERROR_BACK` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc6585 |
| `HTTP_451` | 451 Unavailable For Legal Reasons — 법적 사유로 제공 불가 | `STOP_REPORT` | 0 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc7725 |
| `HTTP_500` | 500 Internal Server Error — 서버 내부 오류 | `SERVER_ERROR_WAIT` | 1 | 예 | 실제발생 | https://www.rfc-editor.org/rfc/rfc9110#section-15.6.1 |
| `HTTP_501` | 501 Not Implemented — 구현 안 됨 | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.6.2 |
| `HTTP_502` | 502 Bad Gateway — 게이트웨이 오류 | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.6.3 |
| `HTTP_503` | 503 Service Unavailable — 서비스 일시 중단(과부하·점검) | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.6.4 |
| `HTTP_504` | 504 Gateway Timeout — 게이트웨이 시간 초과 | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.6.5 |
| `HTTP_505` | 505 HTTP Version Not Supported — HTTP 버전 미지원 | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc9110#section-15.6.6 |
| `HTTP_506` | 506 Variant Also Negotiates — 협상 오류 | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc2295 |
| `HTTP_507` | 507 Insufficient Storage — 저장 공간 부족 | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc4918 |
| `HTTP_508` | 508 Loop Detected — 반복 감지 | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc5842 |
| `HTTP_510` | 510 Not Extended — 확장 필요(폐기) | `SERVER_ERROR_WAIT` | 1 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc2774 |
| `HTTP_511` | 511 Network Authentication Required — 네트워크 인증 필요(와이파이 로그인 등) | `STOP_REPORT` | 0 | 예 | 미발생(예방) | https://www.rfc-editor.org/rfc/rfc6585 |

감지(공통): 화면이나 창 제목에 `HTTP ERROR 503`, `503 Service Unavailable`, `Error 503`, `오류 503` 꼴. 숫자만 있으면 무시.

## 5. Cloudflare (1xxx는 본문에 'Error 10xx'와 'Ray ID'가 같이 보임)

| 코드 | 뜻 | 대응 | 출처 |
|---|---|---|---|
| `CF_1015` | Cloudflare Error 1015 You are being rate limited (HTTP 429) | `RATE_LIMIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/#error-1015-you-are-being-rate-limited |
| `CF_1025` | Cloudflare Error 1025 Please check back later (HTTP 429) | `RATE_LIMIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/#error-1025-please-check-back-later |
| `CF_BLOCKED` | Cloudflare 접근 거부 1005~1012·1020·1034·1106 'Sorry, you have been blocked' | `BLOCK_STOP_REST` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/ |
| `CF_CHALLENGE` | Cloudflare 봇 확인 화면(Just a moment / Verify you are human) | `STOP_REPORT` | https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/ |
| `CF_1000` | Cloudflare Error 1000: DNS points to prohibited IP (HTTP 403) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1000/ |
| `CF_1001` | Cloudflare Error 1001: DNS resolution error (HTTP 409) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1001/ |
| `CF_1002` | Cloudflare Error 1002: DNS points to Prohibited IP (HTTP 403) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1002/ |
| `CF_1003` | Cloudflare Error 1003: Direct IP Access Not Allowed (HTTP 403) | `STOP_REPORT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1003/ |
| `CF_1004` | Cloudflare Error 1004: Host Not Configured to Serve Web Traffic (HTTP 409) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1004/ |
| `CF_1013` | Cloudflare Error 1013: HTTP hostname and TLS SNI hostname mismatch (HTTP 403) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1013/ |
| `CF_1014` | Cloudflare Error 1014: CNAME Cross-User Banned (HTTP 403) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1014/ |
| `CF_1016` | Cloudflare Error 1016: Origin DNS error (HTTP 530) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1016/ |
| `CF_1018` | Cloudflare Error 1018: Could not find host (HTTP 409) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1018/ |
| `CF_1019` | Cloudflare Error 1019: Compute server error (HTTP 503) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1019/ |
| `CF_1023` | Cloudflare Error 1023: Could not find host (HTTP 503) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1023/ |
| `CF_1033` | Cloudflare Error 1033: Argo Tunnel error (HTTP 530) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/ |
| `CF_1035` | Cloudflare Error 1035: Invalid request rewrite (invalid URI path) (HTTP 500) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1035/ |
| `CF_1036` | Cloudflare Error 1036: Invalid request rewrite (maximum length exceeded) (HTTP 400) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1036/ |
| `CF_1037` | Cloudflare Error 1037: Invalid rewrite rule (failed to evaluate expression) (HTTP 500) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1037/ |
| `CF_1040` | Cloudflare Error 1040: Invalid request rewrite (header modification not allowed) (HTTP 500) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1040/ |
| `CF_1041` | Cloudflare Error 1041: Invalid request rewrite (invalid header value) (HTTP 500) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1041/ |
| `CF_1101` | Cloudflare Error 1101: Rendering error (HTTP 500) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1101/ |
| `CF_1102` | Cloudflare Error 1102: Rendering error (HTTP 503) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1102/ |
| `CF_1200` | Cloudflare Error 1200: Cache connection limit (HTTP 503) | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1200/ |
| `CF_520` | Cloudflare 520 web server returns an unknown error — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |
| `CF_521` | Cloudflare 521 web server is down — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |
| `CF_522` | Cloudflare 522 connection timed out — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |
| `CF_523` | Cloudflare 523 origin is unreachable — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |
| `CF_524` | Cloudflare 524 a timeout occurred — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |
| `CF_525` | Cloudflare 525 SSL handshake failed — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |
| `CF_526` | Cloudflare 526 invalid SSL certificate — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |
| `CF_530` | Cloudflare 530 For a complete description of this error refer to the [Error 530](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-530/) page. — 사이트 서버 문제 | `SERVER_ERROR_WAIT` | https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/ |

## 6. 크롬 ERR_* (주요 코드만 여기, 전체 266개는 error_codes.json)

크롬 오류 화면 아래에 `ERR_…` 글자가 그대로 보이므로 OCR로 잡습니다. 범위별 기본 대응: 100번대 연결·800번대 DNS → `NETWORK_WAIT`, 200·700번대 인증서 → `STOP_REPORT`, 300번대 HTTP → `SERVER_ERROR_WAIT`.

| 코드 | 번호 | 원문 설명(chromium) | 대응 |
|---|---|---|---|
| `ERR_FAILED` | -2 | A generic failure occurred. | `NETWORK_WAIT` |
| `ERR_FILE_NOT_FOUND` | -6 | The file or directory cannot be found. | `PAGE_SKIP` |
| `ERR_TIMED_OUT` | -7 | An operation timed out. | `NETWORK_WAIT` |
| `ERR_ACCESS_DENIED` | -10 | Permission to access a resource, other than the network, was denied. | `STOP_REPORT` |
| `ERR_INSUFFICIENT_RESOURCES` | -12 | There were not enough resources to complete the operation. | `NETWORK_WAIT` |
| `ERR_FILE_NO_SPACE` | -18 | Not enough room left on the disk. | `STOP_REPORT` |
| `ERR_BLOCKED_BY_CLIENT` | -20 | The client chose to block the request. | `STOP_REPORT` |
| `ERR_NETWORK_CHANGED` | -21 | The network changed. | `NETWORK_WAIT` |
| `ERR_BLOCKED_BY_ADMINISTRATOR` | -22 | The request was blocked by the URL block list configured by the domain administrator. | `STOP_REPORT` |
| `ERR_BLOCKED_BY_RESPONSE` | -27 | The request failed because the response was delivered along with requirements which are not met ('X-Frame-Options' and 'Content-Security-Pol | `BLOCK_STOP_REST` |
| `ERR_CONNECTION_CLOSED` | -100 | A connection was closed (corresponding to a TCP FIN). | `NETWORK_WAIT` |
| `ERR_CONNECTION_RESET` | -101 | A connection was reset (corresponding to a TCP RST). | `NETWORK_WAIT` |
| `ERR_CONNECTION_REFUSED` | -102 | A connection attempt was refused. | `NETWORK_WAIT` |
| `ERR_CONNECTION_ABORTED` | -103 | A connection timed out as a result of not receiving an ACK for data sent. This can include a FIN packet that did not get ACK'd. | `NETWORK_WAIT` |
| `ERR_CONNECTION_FAILED` | -104 | A connection attempt failed. | `NETWORK_WAIT` |
| `ERR_NAME_NOT_RESOLVED` | -105 | The host name could not be resolved. | `NETWORK_WAIT` |
| `ERR_INTERNET_DISCONNECTED` | -106 | The Internet connection has been lost. | `NETWORK_WAIT` |
| `ERR_SSL_PROTOCOL_ERROR` | -107 | An SSL protocol error occurred. | `STOP_REPORT` |
| `ERR_ADDRESS_UNREACHABLE` | -109 | The IP address is unreachable.  This usually means that there is no route to the specified host or network. | `NETWORK_WAIT` |
| `ERR_TUNNEL_CONNECTION_FAILED` | -111 | A tunnel connection through the proxy could not be established. For more info see the comment on PROXY_UNABLE_TO_CONNECT_TO_DESTINATION. | `NETWORK_WAIT` |
| `ERR_SSL_VERSION_OR_CIPHER_MISMATCH` | -113 | The client and server don't support a common SSL protocol version or cipher suite. | `STOP_REPORT` |
| `ERR_CONNECTION_TIMED_OUT` | -118 | A connection attempt timed out. | `NETWORK_WAIT` |
| `ERR_PROXY_CONNECTION_FAILED` | -130 | Could not create a connection to the proxy server. An error occurred either in resolving its name, or in connecting a socket to it. Note tha | `NETWORK_WAIT` |
| `ERR_CERT_COMMON_NAME_INVALID` | -200 | The server responded with a certificate whose common name did not match the host name.  This could mean:  1. An attacker has redirected our  | `STOP_REPORT` |
| `ERR_CERT_DATE_INVALID` | -201 | The server responded with a certificate that, by our clock, appears to either not yet be valid or to have expired.  This could mean:  1. An  | `STOP_REPORT` |
| `ERR_CERT_AUTHORITY_INVALID` | -202 | The server responded with a certificate that is signed by an authority we don't trust.  The could mean:  1. An attacker has substituted the  | `STOP_REPORT` |
| `ERR_CERT_REVOKED` | -206 | The server responded with a certificate has been revoked. We have the capability to ignore this error, but it is probably not the thing to d | `STOP_REPORT` |
| `ERR_CERT_INVALID` | -207 | The server responded with a certificate that is invalid. This error is not recoverable.  MSDN describes this error as follows: "The SSL cert | `STOP_REPORT` |
| `ERR_INVALID_URL` | -300 | The URL is invalid. | `CLIENT_ERROR_BACK` |
| `ERR_TOO_MANY_REDIRECTS` | -310 | Attempting to load an URL resulted in too many redirects. | `SESSION_RELOGIN` |
| `ERR_UNSAFE_REDIRECT` | -311 | Attempting to load an URL resulted in an unsafe redirect (e.g., a redirect to file:// is considered unsafe). | `STOP_REPORT` |
| `ERR_INVALID_RESPONSE` | -320 | The server's response was invalid. | `SERVER_ERROR_WAIT` |
| `ERR_EMPTY_RESPONSE` | -324 | The server closed the connection without sending any data. | `SERVER_ERROR_WAIT` |
| `ERR_RESPONSE_HEADERS_TOO_BIG` | -325 | The headers section of the response is too large. | `SERVER_ERROR_WAIT` |
| `ERR_CONTENT_DECODING_FAILED` | -330 | Content decoding of the response body failed. | `SERVER_ERROR_WAIT` |
| `ERR_NETWORK_IO_SUSPENDED` | -331 | An operation could not be completed because all network IO is suspended. | `SERVER_ERROR_WAIT` |
| `ERR_HTTP2_PROTOCOL_ERROR` | -337 | There is an HTTP/2 protocol error. | `SERVER_ERROR_WAIT` |
| `ERR_INCOMPLETE_CHUNKED_ENCODING` | -355 | The HTTP response body is transferred with Chunked-Encoding, but the terminating zero-length chunk was never sent when the connection is clo | `SERVER_ERROR_WAIT` |
| `ERR_QUIC_PROTOCOL_ERROR` | -356 | There is a QUIC protocol error. | `SERVER_ERROR_WAIT` |
| `ERR_INVALID_HTTP_RESPONSE` | -370 | The server was expected to return an HTTP/1.x response, but did not. Rather than treat it as HTTP/0.9, this error is returned. | `SERVER_ERROR_WAIT` |
| `ERR_HTTP_RESPONSE_CODE_FAILURE` | -379 | The server returned a non-2xx HTTP response code.  Note that this error is only used by certain APIs that interpret the HTTP response itself | `SERVER_ERROR_WAIT` |
| `ERR_CACHE_MISS` | -400 | The cache does not have the requested entry. | `NETWORK_WAIT` |
| `ERR_DNS_TIMED_OUT` | -803 | DNS transaction timed out. | `NETWORK_WAIT` |

출처: https://chromium.googlesource.com/chromium/src/+/main/net/base/net_error_list.h (2026-11-19 받은 사본 `sources/chromium_net_error_list_20261119.h`)

## 7. 출처 원본 사본 (sources/)

- `sources/chromium_net_error_list_20261119.h` sha256 `20dfbf3ae93c4425…`
- `sources/cloudflare_1xxx_20261119.md` sha256 `4602c90305d6f323…`
- `sources/cloudflare_5xx_20261119.md` sha256 `7f005019fde82e3d…`
- `sources/iana_http_status_codes_20261119.csv` sha256 `6098f56a5f25ca05…`

다시 만들기: `python3 build_error_codes.py` (공식 원본을 새로 받아 sources/에 넣은 뒤).
