# -*- coding: utf-8 -*-
"""오프라인 시험: 가짜 OCR 글자로 page_guard 확인 (사이트 접속 없음). python3 -m pytest -q tests/ 또는 python3 tests/test_page_guard.py"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "standalone"))
import page_guard as pg

G = pg.PageGuard()
class Im:  # PIL 대신 크기만 있는 가짜 화면
    size = (1920, 1080)
def ocr_fn(text_top="", text_mid=""):
    def f(im, box):
        return text_top if box[1] == 0 else text_mid
    return f
def chk(mid, top="", title=None, site="court", ignore=()):
    return pg.check_page(Im(), ocr_fn(top, mid), window_title=title, site=site, ignore=ignore, guard=G)

NORMAL_LIST = ("매각예정물건 사건번호 2025타경52429 물건번호 1 서울동부지방법원 감정평가액 429,000,000원 최저매각가격 343,200,000원 "
               "매각기일 2026.11.27 10:00 경매4계 02-2204-2429 아파트 84.97㎡ 429호 이전 다음 1 2 3 감정평가서 관심물건")

def test_429_page():                assert chk("429 Too Many Requests nginx") == ("HTTP_429", "RATE_LIMIT")
def test_429_title_only():          assert chk("", title="429 Too Many Requests - Chrome") == ("HTTP_429", "RATE_LIMIT")
def test_429_chrome_http_error():   assert chk("이 페이지가 작동하지 않습니다 www.courtauction.go.kr HTTP ERROR 429") == ("HTTP_429", "RATE_LIMIT")
def test_429_korean():              assert chk("요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.")[0] == "HTTP_429"
def test_429_ocr_spacing():         assert chk("TooMany Requests")[0] == "HTTP_429"
def test_403():                     assert chk("403 Forbidden") == ("HTTP_403", "BLOCK_STOP_REST")
def test_akamai():                  assert chk("Access Denied You don't have permission to access http://x on this server. Reference #18.85c5d617.1549635923.277a7a1")[1] == "BLOCK_STOP_REST"
def test_block_ko():                assert chk("비정상적인 접근이 감지되어 접속이 제한되었습니다")[1] == "BLOCK_STOP_REST"
def test_maintenance():             assert chk("현재 시스템 점검 중입니다. 이용에 불편을 드려 죄송합니다.") == ("SITE_MAINTENANCE", "MAINTENANCE_WAIT")
def test_503():                     assert chk("503 Service Unavailable") == ("HTTP_503", "SERVER_ERROR_WAIT")
def test_502_phrase_alone():        assert chk("Bad Gateway") == ("HTTP_502", "SERVER_ERROR_WAIT")
def test_session_expired():         assert chk("세션이 만료되었습니다. 다시 로그인해 주세요.") == ("SESSION_EXPIRED", "SESSION_RELOGIN")
def test_forced_logout():           assert chk("다른 곳에서 로그인하여 로그아웃 되었습니다")[1] == "FORCED_LOGOUT"
def test_forced_beats_session():    assert chk("다른 곳에서 로그인하여 로그아웃 되었습니다")[0] == "FORCED_LOGOUT"
def test_captcha():                 assert chk("자동입력방지 문자를 입력하세요") == ("CAPTCHA_IMAGE", "STOP_REPORT")
def test_chrome_err():              assert chk("사이트에 연결할 수 없음 ERR_CONNECTION_TIMED_OUT") == ("CHROME_ERR_CONNECTION_TIMED_OUT", "NETWORK_WAIT")
def test_chrome_err_ocr_split():    assert chk("ERR_NAME_ NOT_RESOLVED")[0] == "CHROME_ERR_NAME_NOT_RESOLVED"
def test_chrome_cert():             assert chk("연결이 비공개로 설정되어 있지 않습니다 NET::ERR_CERT_DATE_INVALID")[1] == "STOP_REPORT"
def test_chrome_unknown_err():      assert chk("ERR_SOMETHING_NEW")[0] == "CHROME_ERR_UNKNOWN"
def test_netfunnel():               assert chk("서비스 접속대기 중입니다. 고객님 앞에 448명 예상대기시간 3분", site="reservation") == ("NETFUNNEL_QUEUE", "QUEUE_WAIT")
def test_cf_1015():                 assert chk("Error 1015 Ray ID: 8a1b You are being rate limited")[0] == "CF_1015"
def test_cf_challenge():            assert chk("", title="Just a moment...")[0] == "CF_CHALLENGE"
def test_404_skip():                assert chk("404 Not Found") == ("HTTP_404", "PAGE_SKIP")
def test_pay_stop():                assert chk("발급하기 수수료 1,000원")[1] == "STOP_REPORT"

# --- 오탐 없음
def test_normal_list_none():        assert chk(NORMAL_LIST, top="법원경매정보 courtauction.go.kr 로그아웃 남은시간 29:10") is None
def test_case_number_429_none():    assert chk("2025타경429 물건번호1 감정평가액 429,000,000원") is None
def test_money_503_none():          assert chk("최저가 503,000,000원 503호 2026.05.03") is None
def test_plain_numbers_none():      assert chk("429 503 404 403") is None
def test_cf_needs_context():        assert chk("Error 1015") is None   # Cloudflare·Ray ID 없이는 안 잡음
def test_recaptcha_checkbox_left_to_old_code(): assert chk("로봇이 아닙니다 reCAPTCHA") is None   # 기존 robot_check()가 처리
def test_appraisal_notice_none():   assert chk("감정평가서 열람 안내 요청하신 감정평가서 문서를 찾을 수 없거나 일시적으로 열람할 수 없습니다") is None
def test_ignore_session_after_own_logout(): assert chk("로그아웃 되었습니다", ignore=("SESSION_EXPIRED",)) is None
def test_title_normal_none():       assert chk("", title="법원경매정보 - Chrome") is None

# --- guarded / 로그
def test_guarded_blocks_click_and_logs():
    clicks, logs = [], []
    try:
        pg.guarded(lambda *a: clicks.append(a), 905, 400, grab_fn=Im, ocr_text_fn=ocr_fn("", "429 Too Many Requests"),
                   log_fn=lambda **k: logs.append(k), where="list", guard=G)
        assert False, "PageError 가 나야 함"
    except pg.PageError as e:
        assert e.code == "HTTP_429" and e.action == "RATE_LIMIT"
    assert clicks == [] and logs[0]["ev"] == "page_error" and logs[0]["code"] == "HTTP_429" and logs[0]["action"] == "RATE_LIMIT"
def test_guarded_passes_when_ok():
    clicks = []
    r = pg.guarded(lambda *a: clicks.append(a) or "ok", 1, 2, grab_fn=Im, ocr_text_fn=ocr_fn("", NORMAL_LIST), guard=G)
    assert r == "ok" and clicks == [(1, 2)]
def test_guarded_on_error_callback():
    r = pg.guarded(lambda: "clicked", grab_fn=Im, ocr_text_fn=ocr_fn("", "503 Service Unavailable"), on_error=lambda c, a: (c, a), guard=G)
    assert r == ("HTTP_503", "SERVER_ERROR_WAIT")

# --- 요청 간격
def test_rategate_gap_and_hour_cap_and_429_mult():
    t = [0.0]; slept = []
    def sleep(s): slept.append(s); t[0] += s
    rg = pg.RateGate(gap_sec=(60, 60), per_hour=3, per_run=0, now_fn=lambda: t[0], sleep_fn=sleep, rand_fn=lambda a, b: a)
    assert rg.wait_before_item() == 0
    assert rg.wait_before_item() == 60
    assert rg.wait_before_item() == 60
    w = rg.wait_before_item()           # 시간당 3건 상한 → 1시간 찰 때까지 기다림
    assert w >= 3600 - 120
    rg.on_rate_limit()                   # 429 뒤 간격 2배, 시간당 상한 절반
    (lo, hi), ph = rg.limits()
    assert lo == 120 and ph == 1
def test_rategate_run_cap():
    rg = pg.RateGate(gap_sec=(0, 0), per_hour=100, per_run=2, now_fn=lambda: 0, sleep_fn=lambda s: None)
    assert rg.wait_before_item() == 0 and rg.wait_before_item() == 0 and rg.wait_before_item() is None

def test_json_shape():
    d = G.doc
    for e in d["entries"]:
        assert e["action"] in d["actions"], e["code"]
        assert e["status"] in ("실제발생", "미발생(예방)"), e["code"]
        assert e.get("source_url"), e["code"]
        if e["status"] == "실제발생": assert e["history"], e["code"]

if __name__ == "__main__":
    fs = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    bad = 0
    for n, f in fs:
        try: f(); print("PASS", n)
        except Exception as ex: bad += 1; print("FAIL", n, repr(ex), G.last and G.last.get("hits"))
    print(f"{len(fs) - bad}/{len(fs)} pass"); sys.exit(1 if bad else 0)
