# 바뀐 점 — 지금 수집기 문제 1~13을 어떻게 고쳤나 (단독 수집기 v1)

기준: 1호 `_1ho_dl_loop.py`(C5 addendum), C6·C7 메뉴 패치, 오류 대응표(page_guard·error_codes.json 377개), 2호 대전 수정(c2_close_popup.py, subst, 홈 주소 열기), 지도 방문 도구(_1ho_map_visit.py).
예전 파일은 고치지 않았어요. 필요한 것만 이 폴더로 복사했고 출처·SHA는 `reports/INPUT_SOURCES.json` 에 있어요.

| # | 문제 | 고친 방법 | 파일 | 시험 |
|---|---|---|---|---|
| 1 | 12:46 법원 429를 못 잡고 계속 요청, 자동 로그아웃 없음 | 피코2 입력(클릭·키·글자 한 자씩) **직전마다** 화면 오류 확인(`input_check` → `page_check`). 429면 바로 429 기록(쉬는 시간·간격 2배 먼저 저장) → 30~60초 손 멈춤 → 로그아웃 → **로그인 글자가 보이고 로그아웃 글자가 없어야 성공**, 안 되면 뒤로 1번, 그래도 안 되면 탭 닫기 → 로그아웃 시점부터 120분 쉼. 같은 날 2번째면 그날 끝. 상태는 파일에 남아 다시 켜도 이어짐 | runner.py, pacing.py, screen_guard.py | T03, T09 429·입력 직전 |
| 2 | 저장 창 안 뜸(no_save_dialog) 연속 실패 | 저장 창이 안 뜨면 먼저 오류 화면인지 확인. 1번째 건너뜀, 2번 연속 10분 쉼(rate_suspect), 3번째도 안 뜨면 429와 똑같이 처리 | screen_guard.on_save_result, runner.run_item_step | T04, T09 저장창 3번 |
| 3 | 요청 간격 | 물건 사이 60~120초, 시간당 20건, 회차당 40건, 50~55분 일하면 5~10분 쉼. 429 뒤엔 간격×2·시간당÷2 | pacing.Pacer(+RateGate) | T03 |
| 4 | 메뉴 못 찾음 | C7 방식: 넓은 위쪽 띠에서 조각그림(1/4 축소 훑기→1픽셀 확인) → 화면글자(단어 좌표). 메뉴 hover 는 커서를 한 번 뺐다가 올림 | snippets.py, locator.py, runner.do_hover | T05 |
| 5 | 홈 팝업 | 2호 방식: 팝업마다 '오늘 하루 열지 않음' 체크 → 같은 줄(세로 80px 안) '닫기', 2개면 2번, 금지 글자 줄은 안 누름 | popup.py | T06 팝업 2개 |
| 6 | 캡차 | **변경(안전)**: 숫자 캡차·로봇 확인·그림 고르기 모두 자동으로 풀지 않아요(AI 판독·자동 입력·자동 체크 없음). 그림과 "xxxx가 맞나요?" 확인 요청을 `state/captcha_ask/pending.json` 에 남기고 STOP 파일 후 멈춤. 사람이 브라우저에서 직접 풀고 STOP을 지우면 재개 | captcha.py, screen_guard.py | T04, T06, T09 캡차 입력 0번 |
| 7 | 인증서·결제·발급 화면 | 표(STOP_REPORT)와 별도로 위험 글자 목록을 한 번 더 봄 → 아무것도 안 누르고 STOP_<작업> 후 멈춤. 클릭마다 ClickGuard(C5)가 둘레 글자에 인증서·결제·발급이 있으면 막음 | screen_guard.DANGER_WORDS, click_guard.py, hw.click | T04, T09 위험·인증서 버튼 |
| 8 | STOP 파일이 지도까지 막음 | 작업별 STOP: `STOP_<작업>`, 묶음 `STOP_court`, 전체 `STOP_ALL`. 예전 `STOP` 은 법원만 | stops.py | T02, T09 지도 |
| 9 | 찾기 순서·순위 | ① 조각그림(성공률·최근 성공 순 자동 순위, snippet_rank.json) ② 화면 글자 ③ 그다음에만 AI 딥시크→제미나이, 한 막힘에 최대 2라운드. ②③으로 찾으면 조각그림을 배워 다음엔 ①로 | snippets.py, locator.py, ai_helper.py | T05 |
| 10 | 다른 사이트도 | 사이트별 단계 JSON(goto·click·hover·open_close·popups·login·items_loop·logout…). 지도 2개(naver 항공·야놀자)는 1호 실측 JSON을 옮김 | runner.py, collector_kb/sites/ | T09 지도 |
| 11 | 저장 순서 | NAS 먼저 → 안 되면 D: → C:, `pending_to_nas.jsonl` 에 적고 회차 끝에 NAS로 옮김(SHA 같아야 원본 지움). X: 없으면 subst 1번 | storage.py | T07 |
| 12 | 배터리·충전기 | 20% 미만·충전기 빠짐 경고(상태 바뀔 때만), 10% 미만+충전기 빠짐이면 멈춤 | power.py | T07 |
| 13 | 로그인 화면 | 로그인 단계에서는 아이디/비밀번호 탭·칸·Enter만. AI 안 씀. 인증서 탭·버튼은 ClickGuard 가 막음. 탭 글자만으로는 멈추지 않지만 인증서 선택·암호 창은 멈춤. 로그인 화면은 사진을 안 찍음 | runner._login, screen_guard.LOGIN_DANGER | T04, T09 로그인 |

## 그 밖에 새로 넣은 것
- **키·아이디 수작업 입력**: 실행 때 사람이 입력(화면에 안 보임). 기본은 메모리만, 선택으로 Windows DPAPI 암호화 저장. 평문 파일 없음. 설정 파일에 비밀 칸이 있으면 거부. 기록은 비밀값을 ***로 바꿈. 피코2 장치 기록에서도 KEY 응답은 [redacted].
- **최신 DB 받기**: 회차 시작 때 1번, 버전·SHA 비교 후 바뀐 것만. 안 되면 로컬 DB로. 프로그램 코드는 DB로 안 받음. 규격 `SERVER_API_KO.md`.
- **관제기 연결 코드 없음**(node/status·commands 없음).

## 한영님 지시와 다르게 한 것 (검수 때 판단 부탁)
- 6번 "숫자 캡차는 AI 1번 후 사람": 캡차·로봇 확인은 보안 장벽이라 프로그램이 AI로 읽거나 자동 입력하지 않게 했어요(BIDGUARD 원칙 10 "로봇 확인 우회 금지"). 사람에게 그림과 확인 질문은 그대로 준비해요.
