# -*- coding: utf-8 -*-
"""
G v1.2.4 (2026-11-23 09:10 KST, 수집기3호, 한영님 08:51 지시·1호 P_ROBOT/R002/R003): 계획 "recaptcha_checkbox": true 이면
  reCAPTCHA 체크칸만 피코2로 천천히 1회 클릭(10~16초 간격, 위치는 OCR '로봇' 글자+체크칸 테두리 실측), 2~5초 뒤 체크 확인 → '열람하기'.
  그림 문제 글자가 보이면 풀지 않고 EXIT 12. 같은 창 2회 체크해도 안 풀리면 EXIT 13. 팝업은 닫지 않음. 기록 C:\\BIDGUARD\\collector3_out\\recaptcha_log.jsonl.
  open_url 단계 "keep_old_window": true 면 앞 창을 닫지 않음(06:22 2026타경278 창 보존). v1.2.3 그대로 둠.
G v1.2.3 (2026-11-23 05:25 KST, 수집기3호, 대표 pt60u): 피코2 동작(클릭·키·휠·입력) 하나하나 앞에서 C:\\BIDGUARD\\collector3\\remote_guard\\pause.flag 확인.
  깃발 있으면 입력 없이 기다리며 기록(pause_wait/pause_end), 깃발이 사라지면 앞창 확인 후 계속. --pause-max(분, 기본 30) 넘으면 입력 없이 끝(EXIT 11).
  KEYS_OK 에 ALT+LEFT(브라우저 뒤로 1번) 추가. v1.2.2 그대로 둠.
G v1.2.2 (2026-11-22 22:20 KST, 수집기3호): --no-beep(또는 계획 "beep": false)로 시작 삐 소리 끔. 키·입력 전 앞창 확인(대화상자면 거부). v1.2.1 그대로 둠.
[G v1.2.1 수집기3호, 2026-11-22, 대표 pt46u '법원 수집 업무를 시작해라. 아주. 천천히.']
  * 원본 c2_step_click.py v1.2 는 그대로. 이 판은 DESKTOP-5R67H71(1920x1080·125%)용:
    크롬 별도 프로필 C:\\BIDGUARD\\collector3\\chrome_c3 + --force-device-scale-factor=1 (lg15 좌표와 같게).
  * 아주 천천히(대표 11/22 12:36~11/27 06:12): 피코2 동작(클릭·키·휠·입력) 사이 10~16초 무작위, 실행을 넘어 state 로 지킴.
  * type {text, near_label?}: 숫자·영문만 한 글자씩(가드: 비밀번호·아이디 칸 근처면 거부). 비밀번호·아이디는 절대 안 침.
  * 끊김(크롬 오류)·429 글자도 차단으로 봄(EXIT 10). 로그인 판정은 홈 표지가 보이면 아님(로그인 폼 글자 필요).
  * 창 닫기: --keep 아니면 이 실행 창(법원경매정보 제목)만.
2호: 화면 글자 찾아 한 번 누르기 단계 실행기 c2_step_click.py v1.2(13:50 runHHMMSS·zip_png·wheel 누적 기록; v1.1 09:32 allow_pay 계획별·실패 시 --keep 유지·stop_unless_any) (2026-11-20 08:20 KST, 대표 pt500u '코레일 예매 전단계까지').
피코2 입력만, AI 없음, 비밀번호 입력 없음. 사용(작업폴더 R):
  python -u tools\\c2_step_click.py <plan.json> [--end 09:03] [--keep]
plan: {"out_root": "C:\\BIDGUARD\\collector2_out\\rail", "attach_title": ["코레일","KORAIL"], "steps": [ ... ]}
단계 종류:
  open_url {url}            : 크롬 새 창(이 실행이 연 창) -> 주소창 1클릭 -> TYPE 한 글자씩 -> DELETE -> 유사도 확인 -> ENTER 1회
  attach                    : 앞 실행이 남긴 창(state.json hwnd, 제목 확인)에 붙음. 없으면 중단.
  click_text {text|alts, near_label, dy, dx, region, avoid_label, pick, optional, allow_yemae}
                            : 캡처 -> OCR -> 글자 찾음 -> 한 번 이동·한 번 클릭 -> 8~15초 -> 캡처
  find_text {...}           : 좌표만 기록(누르지 않음)
  key {combo}               : DOWN/UP/ENTER/ESC/TAB/PAGEDOWN/HOME/END 만
  wheel {ticks, x, y}       : 한 번 휠
  capture {name}
안전: 같은 자리 다시 누르지 않음(반응 없으면 그대로 다음 판단/중단), 새로고침 없음, CTRL 조합 없음.
  가드: 결제·발권·인증서·발급·설치·카드·비밀번호·로그인 제출 글자는 누르지 않음. '예매'는 allow_yemae 단계에서
  하루 한 번만(state.json yemae_used) — 대표 2026-11-20 직접 허락(11시 이후 첫 무궁화 예매 버튼 1회).
  차단어(접속대기·대기순번·NetFunnel·캡차·로봇·403·429) 보이면 즉시 중단(EXIT 10).
  로그인 화면(로그인+비밀번호/카카오) 보이면 캡처하고 멈춤(EXIT 20, 창은 --keep 이면 남김).
  시작 부저 삐익… 삐익. 시험 키 없음(KEY-UPGRADE-AT-2026-12-01T00:00+09:00 표시 대상 없음).
끝: --keep 아니면 이 실행(또는 state 의 창)만 닫음(WM_CLOSE). 법원 크롬 창은 건드리지 않음.
"""
import sys, os, json, time, random, datetime as dt, subprocess, ctypes, argparse, difflib, zipfile
AF = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); ROOT = os.path.dirname(AF); sys.path.insert(0, AF)
ADDR = (360, 520, 57)
u = ctypes.windll.user32
PROTO = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
BLOCK = ("접속대기", "대기순번", "예상대기", "NetFunnel", "netfunnel", "로봇이아닙니다", "CAPTCHA", "captcha", "자동입력방지",
         "보안문자", "비정상적인접근", "접근이차단", "AccessDenied", "403Forbidden", "TooManyRequests", "429TooMany",
         "인터넷에연결되어있지않음", "ERR_INTERNET_DISCONNECTED", "ERR_CONNECTION", "ERR_TIMED_OUT", "사이트에연결할수없음",
         "응답하는데너무오래")  # G v1.2.1
HOME_MARKS = ("빠른물건검색", "금주의경매일정", "투명한경매", "매각가율", "공고중인지역만")
LOGIN_FORM_MARKS = ("아이디를입력", "비밀번호를입력", "아이디입력", "비밀번호입력", "아이디저장", "공동인증서로그인",
                    "금융인증서로그인", "간편인증로그인", "회원로그인", "로그인유지")
C3_CHROME_DIR = r"C:\BIDGUARD\collector3\chrome_c3"
PAUSE_FLAG = r"C:\BIDGUARD\collector3\remote_guard\pause.flag"  # G v1.2.3
CAP_LOG = r"C:\BIDGUARD\collector3_out\recaptcha_log.jsonl"  # G v1.2.4
CAP_WORDS = ("로봇이아닙니다", "CAPTCHA", "captcha")
IMG_CHAL = ("이미지", "선택하세요", "모두선택", "Selectallimages", "selectallimages", "그림문제", "보안문자", "자동입력방지", "건너뛰기", "Skip", "SKIP", "VERIFY", "Verify", "타일")  # 1호 보완 6)
NOTICE_WORDS = ("열람안내", "열람이제한", "열람할수없")  # 1호 보완 3) P_NOTICE
GAP = (16.0, 24.0)  # 2026-11-23 15:22 지휘자 t325u: 클릭 사이 16~24초 (이전 대표 11/22 12:36 10~16초)
GUARD = ("결제", "발권", "인증서", "공동인증", "금융인증", "간편인증", "발급", "설치", "카드", "비밀번호", "로그인하기", "구매", "확정")
KEYS_OK = ("DOWN", "UP", "ENTER", "ESC", "TAB", "PAGEDOWN", "PAGEUP", "HOME", "END", "LEFT", "RIGHT", "ALT+LEFT")  # G v1.2.3: ALT+LEFT


def windows():
    out = []
    def cb(h, l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(h, b, n + 1); out.append((h, b.value))
        return True
    u.EnumWindows(PROTO(cb), 0); return out
def title(h):
    n = u.GetWindowTextLengthW(h); b = ctypes.create_unicode_buffer(n + 1); u.GetWindowTextW(h, b, n + 1); return b.value
NOMAX = set()
def front(h):
    u.ShowWindow(h, 5 if h in NOMAX else 3); u.keybd_event(0x12, 0, 0, 0); u.SetForegroundWindow(h); u.keybd_event(0x12, 0, 2, 0); time.sleep(0.8)
    return u.GetForegroundWindow() == h
def compact(s): return "".join(str(s or "").split())
def norm(t): return compact(t).lower().translate(str.maketrans({"0": "o", "1": "i", "l": "i", "|": "i", "!": "i"}))
def beep():
    try:
        import winsound; winsound.Beep(1000, 700); time.sleep(0.3); winsound.Beep(1000, 700); return "winsound"
    except Exception:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", "[console]::beep(1000,700); Start-Sleep -Milliseconds 300; [console]::beep(1000,700)"], timeout=10); return "console"
        except Exception: return "fail"
def ime_english(h):
    try:
        imm = ctypes.windll.imm32; w = imm.ImmGetDefaultIMEWnd(ctypes.c_void_p(h)); u.SendMessageW(w, 0x283, 2, 0); return True
    except Exception: return False


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("plan"); ap.add_argument("--end", default="09:03"); ap.add_argument("--keep", action="store_true"); ap.add_argument("--no-beep", action="store_true"); ap.add_argument("--pause-max", type=float, default=30.0)  # G v1.2.2 / v1.2.3
    ns = ap.parse_args()
    plan = json.load(open(ns.plan, encoding="utf-8"))
    now = dt.datetime.now(); day = now.strftime("%Y-%m-%d")
    end_ts = dt.datetime.combine(now.date(), dt.datetime.strptime(ns.end, "%H:%M").time()).timestamp()
    base = os.path.join(plan.get("out_root") or r"C:\BIDGUARD\collector3_out\steps", day); os.makedirs(base, exist_ok=True)
    out = os.path.join(base, "run" + now.strftime("%H%M%S")); os.makedirs(out, exist_ok=True)  # v1.2: 초까지(같은 분 실행 덮어쓰기 방지)
    stp = os.path.join(base, "state.json")
    state = json.load(open(stp, encoding="utf-8")) if os.path.exists(stp) else {}
    def save_state(): json.dump(state, open(stp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    def log(ev, **kw):
        rec = dict(t=dt.datetime.now().strftime("%H:%M:%S"), ev=ev, **kw)
        open(os.path.join(out, "run_log.jsonl"), "a", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        print(json.dumps(rec, ensure_ascii=False, default=str), flush=True)
    def act_log(rec): open(os.path.join(out, "pico_actions.jsonl"), "a", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    for d in (AF, ROOT):
        for n in ("STOP", "PAUSE"):
            if os.path.exists(os.path.join(d, n)): log("stop_file", f=n); return 0
    if ns.no_beep or plan.get("beep") is False: log("buzzer_off")  # G v1.2.2: 대표 22:15 삐 소리 신고 → 끌 수 있게(기본은 그대로 울림)
    else: log("buzzer", how=beep())
    from PIL import ImageGrab
    import winocr
    from af.actuator import PicoActuator, RefusedAction

    def layout(im):
        W, H = im.size; S = 2; lines = []
        for band in ((0, 0, W, 100), (0, 100, W, H // 2), (0, H // 2, W, H)):
            c = im.crop(band).resize(((band[2]-band[0])*S, (band[3]-band[1])*S)); r = winocr.recognize_pil_sync(c, "ko")
            for ln in r.get("lines", []):
                ws = []
                for w in ln.get("words", []):
                    b = w["bounding_rect"]
                    ws.append({"text": w.get("text", ""), "box": [round(band[0]+b["x"]/S), round(band[1]+b["y"]/S), round(band[0]+(b["x"]+b["width"])/S), round(band[1]+(b["y"]+b["height"])/S)]})
                if not ws: continue
                box = [min(w["box"][0] for w in ws), min(w["box"][1] for w in ws), max(w["box"][2] for w in ws), max(w["box"][3] for w in ws)]
                lines.append({"text": ln.get("text", ""), "box": box, "center": [(box[0]+box[2])//2, (box[1]+box[3])//2], "words": ws})
        return lines
    seq = [0]; wsum = [0]
    def shot(name):
        seq[0] += 1; im = ImageGrab.grab(); tag = "%02d_%s" % (seq[0], name)
        im.save(os.path.join(out, tag + ".png"))
        sm = im.convert("RGB"); sm.thumbnail((1440, 810)); q = 70
        sm.save(os.path.join(out, tag + ".jpg"), quality=q)
        while os.path.getsize(os.path.join(out, tag + ".jpg")) > 400_000 and q > 30:
            q -= 10; sm.save(os.path.join(out, tag + ".jpg"), quality=q)
        L = layout(im)
        json.dump({"image": tag + ".png", "size": list(im.size), "wheel_sum": wsum[0], "at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "lines": L},
                  open(os.path.join(out, tag + "_layout.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
        alltxt = compact(" ".join(l["text"] for l in L if l["box"][1] > 95))
        return tag, L, alltxt
    def check(tag, txt):
        hit = [w for w in BLOCK if compact(w) in txt]
        if plan.get("notice_close") and any(compact(w) in txt for w in NOTICE_WORDS) and not hit:  # P_NOTICE
            return notice(tag)
        if hit and plan.get("recaptcha_checkbox") and ("로봇이아닙니다" in txt) and all(w in CAP_WORDS + IMG_CHAL for w in hit):  # G v1.2.4
            r_ = solve_captcha(tag)
            if r_: return r_
            tag, _, txt = shot("after_captcha"); hit = [w for w in BLOCK if compact(w) in txt]
        if hit: log("BLOCKED", shot=tag, words=hit); return 10
        home = any(compact(w) in txt for w in HOME_MARKS); form = any(compact(w) in txt for w in LOGIN_FORM_MARKS)  # G v1.2.1
        if (form or not home) and "로그인" in txt and ("비밀번호" in txt or "카카오" in txt or "회원번호" in txt):
            log("LOGIN_SCREEN", shot=tag); state["login_shot"] = os.path.join(out, tag + ".png"); save_state(); return 20
        return 0
    def cap_rec(**kw):
        rec = dict(t=dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S+09:00"), type="recaptcha_checkbox", case=plan.get("case_label"), run=os.path.basename(out), hwnd=hwnd, **kw)
        open(CAP_LOG, "a", encoding="utf-8").write(json.dumps(rec, ensure_ascii=False, default=str) + "\n"); log("recaptcha", **kw)
    def checkbox_pos(im, L):
        ws = [w for l in L for w in l["words"] if "로봇" in compact(w["text"]) and 500 <= w["box"][0] <= 1420 and 250 <= w["box"][1] <= 700]  # 1호 영역
        ws = ws or [w for l in L for w in l["words"] if "로봇" in compact(w["text"]) and 270 <= w["box"][0] <= 1500 and 120 <= w["box"][1] <= 980]  # 감정평가서 창 안 넓게
        if not ws: return None, None
        b = ws[0]["box"]; cy = (b[1] + b[3]) // 2; px = im.convert("RGB").load()
        xs = [x for x in range(b[0] - 6, max(0, b[0] - 80), -1) if sum(px[x, cy]) < 300]
        if xs:
            lf = [x for x in xs if 18 <= xs[0] - x <= 40]
            if lf: return ((xs[0] + lf[0]) // 2, cy), b
        return None, b  # 테두리 못 찾으면 누르지 않음(1호 x-95는 이 PC 1920x1080 배율1에서 칸 밖: 실측 칸 중심=글자 왼쪽-26px)
    def is_checked(im, pos):
        px = im.convert("RGB").load(); n = 0
        for yy in range(pos[1] - 14, pos[1] + 15):
            for xx in range(pos[0] - 14, pos[0] + 15):
                r_, g_, b_ = px[xx, yy]
                if g_ > 120 and r_ < 90 and b_ < 140: n += 1
        return n >= 12, n
    def human_wait(ch, n_):  # 1호 보완 6): 그림 문제는 풀지 않음. 입력 멈춤, 사진, 최대 180초 사람 대기(입력 없음)
        t_, _, _ = shot("captcha_image_challenge"); cap_rec(result="image_challenge", words=ch, shot=t_, attempt=n_, action="PAUSE_no_input_wait_180s")
        open(os.path.join(out, "HUMAN_NEEDED.txt"), "w", encoding="utf-8").write("image challenge " + dt.datetime.now().strftime("%H:%M:%S"))
        t0 = time.time()
        while time.time() - t0 < 180:
            time.sleep(15); im_ = ImageGrab.grab(); L_ = layout(im_); tx_ = compact(" ".join(l["text"] for l in L_ if l["box"][1] > 95))
            if "로봇이아닙니다" not in tx_ and not any(compact(w) in tx_ for w in IMG_CHAL):
                t4, _, _ = shot("captcha_human_solved"); cap_rec(result="human_solved", shot=t4, waited_s=int(time.time() - t0)); return 0
        t5, _, _ = shot("captcha_human_timeout"); cap_rec(result="image_challenge_timeout", shot=t5); return 12
    def notice(tag):  # P_NOTICE: 기록·사진 → 닫기 → EXIT 23(다음 건, retry_list)
        im = ImageGrab.grab(); L = layout(im); lines_ = [l["text"] for l in L if 200 < l["box"][1] < 1000][:40]
        log("P_NOTICE", shot=tag, lines=lines_)
        vs = cands(L, {"text": "닫기", "alts": ["확인"]}); vs = [v for v in vs if 400 < v["c"][0] < 1500 and 300 < v["c"][1] < 1000]
        if vs:
            a.click(vs[-1]["c"][0], vs[-1]["c"][1], hover_ms=random.randint(220, 420), note="notice_close", safe=False); time.sleep(random.uniform(3, 5)); shot("after_notice_close")
        return 23
    def solve_captcha(tag0):
        key = str(hwnd); tries = state.setdefault("cap_tries", {})
        while tries.get(key, 0) < 2:
            im = ImageGrab.grab(); L = layout(im); txt = compact(" ".join(l["text"] for l in L if l["box"][1] > 95))
            ch = [w for w in IMG_CHAL if compact(w) in txt]
            if ch: return human_wait(ch, None)
            pos, wb = checkbox_pos(im, L)
            if not pos: cap_rec(result="checkbox_not_found", word_box=wb, shot=tag0); return 13
            tries[key] = tries.get(key, 0) + 1; save_state(); n_ = tries[key]
            a.click(pos[0] + random.randint(-3, 3), pos[1] + random.randint(-3, 3), hover_ms=random.randint(260, 480), note="recaptcha_checkbox", safe=False)
            log("captcha_clicked", at=pos, word_box=wb, attempt=n_)
            time.sleep(random.uniform(2, 5))
            t2, L2, txt2 = shot("captcha_after_check"); im2 = ImageGrab.grab()
            ch = [w for w in IMG_CHAL if compact(w) in txt2]
            if ch: return human_wait(ch, n_)
            ok, ng = is_checked(im2, pos)
            if not ok: cap_rec(result="not_checked", green=ng, shot=t2, attempt=n_, at=pos); continue
            vs = cands(L2, {"text": "열람하기"})
            if not vs: cap_rec(result="checked_no_view_btn", shot=t2, attempt=n_); return 13
            a.click(vs[0]["c"][0], vs[0]["c"][1], hover_ms=random.randint(220, 420), note="recaptcha_view", safe=False)
            time.sleep(random.uniform(6, 9))
            t3, L3, txt3 = shot("captcha_after_view")
            if "로봇이아닙니다" in txt3: cap_rec(result="still_robot", shot=t3, attempt=n_); continue
            cap_rec(result="passed", shot=t3, attempt=n_, at=pos); state["cap_passed_at"] = time.time(); save_state(); return 0
        cap_rec(result="two_tries_failed"); return 13
    def cands(L, s):
        alts = [compact(a) for a in ([s["text"]] if "text" in s else []) + s.get("alts", [])]
        res = []
        for l in L:
            for w in l["words"] + [dict(text=l["text"], box=l["box"])]:
                t = compact(w["text"])
                if (t in alts) or (s.get("contains") and any(a in t for a in alts)):
                    b = w["box"]; res.append(dict(text=w["text"], box=b, c=[(b[0]+b[2])//2, (b[1]+b[3])//2], line=l["text"]))
        uniq = []
        for r in res:
            if not any(abs(r["c"][0]-q["c"][0]) < 8 and abs(r["c"][1]-q["c"][1]) < 8 for q in uniq): uniq.append(r)
        res = uniq
        if "region" in s:
            x0, y0, x1, y1 = s["region"]; res = [r for r in res if x0 <= r["c"][0] <= x1 and y0 <= r["c"][1] <= y1]
        for xr in s.get("exclude_regions", []):
            res = [r for r in res if not (xr[0] <= r["c"][0] <= xr[2] and xr[1] <= r["c"][1] <= xr[3])]
        if "near_label" in s:
            labs = cands(L, {"text": s["near_label"], "alts": s.get("label_alts", [])})
            dy = s.get("dy", [5, 90]); dx = s.get("dx", 150)
            res = [r for r in res if any(dy[0] <= r["c"][1]-lb["c"][1] <= dy[1] and abs(r["c"][0]-lb["c"][0]) <= dx for lb in labs)]
        if "avoid_label" in s:
            labs = cands(L, {"text": s["avoid_label"], "alts": s.get("avoid_alts", [])})
            res = [r for r in res if not any(-20 <= r["c"][1]-lb["c"][1] <= 90 for lb in labs)]
        if "right_of" in s:
            labs = cands(L, {"text": s["right_of"], "alts": s.get("right_alts", []), "contains": True})
            res = [r for r in res if any(abs(r["c"][1]-lb["c"][1]) <= s.get("row_tol", 25) and r["c"][0] > lb["c"][0] for lb in labs)]
        pick = s.get("pick", "top")
        res.sort(key=(lambda r: (r["c"][1], r["c"][0])) if pick == "top" else (lambda r: (r["c"][0], r["c"][1])))
        return res
    def guard_ok(s, r, L):
        near = [l["text"] for l in L if abs(l["center"][1]-r["c"][1]) <= 22 and abs(l["center"][0]-r["c"][0]) <= 140]
        G = [g for g in GUARD if not (plan.get("allow_pay") and g in ("결제", "구매", "확정", "카드")) and not (plan.get("allow_issue") and g == "발권")]  # 10:30 대표 10:09 승인: 결제/발권 버튼 1회(계획별 allow_issue)  # 대표 t510u/t511u/t524u: 개인 기차표 카카오페이 결제 예외(계획별)
        bad = [g for g in G if g in compact(r["text"]) or any(g in compact(t) for t in near)]
        if "예매" in compact(r["text"]) and not s.get("menu_ok"):   # menu_ok: '승차권 예매' 메뉴(예매 확정 아님)
            if not s.get("allow_yemae"): bad.append("예매(허락 단계 아님)")
            elif state.get("yemae_used"): bad.append("예매(오늘 이미 1회)")
        return bad

    hwnd = None; a = None; rc = 0; results = []
    try:
        a = PicoActuator(ROOT, log=act_log); log("pico", fw=getattr(a, "fw_version", None))
        # G v1.2.1: 모든 피코 동작(클릭·키·휠·입력) 앞에서 10~16초 간격을 지킴(실행을 넘어 state.last_act_ts).
        def gate(kind):
            if kind == "url char":
                return
            g = random.uniform(*GAP); w_ = float(state.get("last_act_ts") or 0) + g - time.time()
            if w_ > 0:
                time.sleep(w_)
        def focus_ok(kind):  # G v1.2.2: 키·입력 앞에서 앞창이 붙은 Chrome인지 확인(대화상자·다른 창이면 다시 앞으로, 그래도 아니면 거부)
            if kind not in ("key", "type") or not hwnd:
                return True
            fg = u.GetForegroundWindow()
            if fg == hwnd:
                return True
            cb = ctypes.create_unicode_buffer(64); u.GetClassNameW(fg, cb, 64)
            log("focus_wrong", fg_title=title(fg), fg_class=cb.value, kind=kind)
            if cb.value == "#32770":
                return False
            return front(hwnd)
        def pause_ok(kind, note):  # G v1.2.3: 원격 접속(pause.flag) 중에는 입력하지 않고 기다림
            if not os.path.exists(PAUSE_FLAG):
                return True
            t0 = time.time(); log("pause_wait", kind=kind, note=note, flag=PAUSE_FLAG)
            n = 0
            while os.path.exists(PAUSE_FLAG):
                if time.time() - t0 > ns.pause_max * 60:
                    log("pause_timeout", waited_s=int(time.time() - t0)); return False
                time.sleep(10); n += 1
                if n % 30 == 0: log("pause_still", waited_s=int(time.time() - t0))
            log("pause_end", waited_s=int(time.time() - t0)); time.sleep(random.uniform(10, 16))
            if hwnd: front(hwnd)
            return True
        def wrap(fn, kind):
            def inner(*args, **kw):
                note = kw.get("note", "")
                gate("url char" if note in ("url char", "type char") else kind)
                if not pause_ok(kind, note):
                    raise RuntimeError("pause_timeout")
                if not focus_ok(kind):
                    log("focus_refused", kind=kind); raise RuntimeError("focus_refused")
                r_ = fn(*args, **kw)
                if note not in ("url char", "type char"):
                    state["last_act_ts"] = time.time(); save_state()
                return r_
            return inner
        a.click = wrap(a.click, "click"); a.key = wrap(a.key, "key"); a.wheel = wrap(a.wheel, "wheel")
        a.type_text = wrap(a.type_text, "type")
        a.drag_or_skip = wrap(a.drag_or_skip, "drag")  # G v1.2.4
        for i, s in enumerate(plan["steps"]):
            if time.time() > end_ts: log("time_up", step=i); rc = 30; break
            k = s["do"]; name = s.get("name", k)
            if s.get("only_if_missing") and any(r_.get("step") == s["only_if_missing"] and r_.get("clicked") for r_ in results):
                log("skip_done", step=name); continue
            if k == "attach" and s.get("find_title"):   # 팝업(카카오계정) 창: 크기 그대로 앞으로만
                hs = [h for h, t in windows() if s["find_title"] in t]
                if not hs: log("attach_fail", want=s["find_title"]); rc = 3; break
                hwnd = hs[0]; NOMAX.add(hwnd)
                if u.GetForegroundWindow() != hwnd: front(hwnd)
                log("attach_popup", title=title(hwnd)); continue
            if k == "attach":
                h = state.get("hwnd")
                if h and u.IsWindow(h) and any(t in title(h) for t in plan.get("attach_title", ["코레일", "KORAIL", "Chrome"])):
                    hwnd = h; front(hwnd); log("attach", title=title(hwnd))
                else: log("attach_fail", h=h, title=title(h) if h else None); rc = 3; break
                continue
            if k == "open_url":
                oh = state.get("hwnd")
                if not s.get("keep_old_window") and oh and u.IsWindow(oh) and "Chrome" in title(oh) and any(t in title(oh) for t in ("코레일", "KORAIL", "카카오", "법원경매")): u.PostMessageW(oh, 0x0010, 0, 0); log("old_window_closed", title=title(oh)); time.sleep(1.0)  # v1.2: 코레일 제목일 때만 닫음(13:50 대표 유튜브 창 닫힘 사고 후)
                before = {h for h, _ in windows()}
                try:
                    ls = json.load(open(os.path.join(os.environ["LOCALAPPDATA"], r"Google\Chrome\User Data\Local State"), encoding="utf-8"))
                    prof = (ls.get("profile") or {}).get("last_used") or "Default"
                except Exception: prof = "Default"
                os.makedirs(C3_CHROME_DIR, exist_ok=True)  # G v1.2.1: 별도 프로필 + 배율 1
                subprocess.Popen([r"C:\Program Files\Google\Chrome\Application\chrome.exe", "--user-data-dir=" + C3_CHROME_DIR,
                                  "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check", "--start-maximized", "--new-window"])
                for _ in range(20):
                    time.sleep(0.6)
                    new = [h for h, t in windows() if h not in before and t.endswith("Chrome")]
                    if new: hwnd = new[0]; break
                if not hwnd: log("no_window"); rc = 2; break
                time.sleep(1.5); front(hwnd); ime_english(hwnd); state["hwnd"] = hwnd; save_state()
                a.click(random.randint(ADDR[0], ADDR[1]), ADDR[2], hover_ms=random.randint(200, 320), note="address bar", safe=False)
                time.sleep(random.uniform(0.6, 1.0))
                url = s["url"]
                for ch in url:
                    a.type_text(ch, note="url char"); time.sleep(random.uniform(0.08, 0.22))
                time.sleep(0.4)
                try: a.key("DELETE", note="drop autocomplete tail")
                except Exception: pass
                im = ImageGrab.grab(); c = im.crop((100, 40, 1500, 85)).resize((2800, 90)); r = winocr.recognize_pil_sync(c, "ko")
                band = norm(" ".join(l.get("text", "") for l in r.get("lines", [])))
                if band.endswith("i") and not norm(url).endswith("i"): band = band[:-1]
                sim = difflib.SequenceMatcher(None, band, norm(url)).ratio(); log("addr_check", sim=round(sim, 3))
                if sim < 0.85: im.save(os.path.join(out, "addr_fail.png")); a.key("ESC", note="cancel"); rc = 4; break
                a.key("ENTER", note="go"); time.sleep(random.uniform(5, 8))
                a._do("move", random.randint(1500, 1800), random.randint(700, 760), note="rest", safe=False)
                time.sleep(random.uniform(1.5, 3))
                tag, L, txt = shot(name); log("page", shot=tag, title=title(hwnd)); results.append(dict(step=name, shot=tag))
                rc = check(tag, txt)
                if rc == 20 and s.get("login_continue"): rc = 0
                if rc: break
                continue
            if hwnd is None: log("no_window_for_step", step=name); rc = 3; break
            if u.GetForegroundWindow() != hwnd and not front(hwnd): log("front_fail"); rc = 3; break
            if k == "drag_text":   # G v1.2.4 (1호 R003): 감정평가서 창 제목줄을 한 번 조금 옮김(피코2 DRAG, 펌웨어에 없으면 건너뜀)
                im = ImageGrab.grab(); L = layout(im); rs = cands(L, s)
                if not rs: log("drag_text_notfound", step=name); continue
                x1, y1 = rs[0]["c"]; dx_ = random.randint(*s.get("dx", [12, 25])); dy_ = random.randint(*s.get("dy", [5, 10]))
                ok_ = a.drag_or_skip(x1, y1, x1 + dx_, y1 + dy_, note=name)
                log("dragged" if ok_ is not False else "drag_skipped", step=name, frm=[x1, y1], d=[dx_, dy_])
                time.sleep(random.uniform(2, 4)); continue
            if k == "wait_text":   # G v1.2.4: 입력 없이 글자가 보일 때까지 기다림(느린 화면)
                t0_ = time.time(); found_ = False
                while time.time() - t0_ < s.get("max_s", 45):
                    im = ImageGrab.grab(); L = layout(im)
                    if cands(L, s): found_ = True; break
                    time.sleep(3)
                log("wait_text", step=name, found=found_, waited_s=int(time.time() - t0_))
                if not found_ and not s.get("optional"):
                    tag, L2, txt = shot(name + "_timeout"); rc = check(tag, txt) or 5; break
                continue
            if k == "capture":
                tag, L, txt = shot(name); results.append(dict(step=name, shot=tag)); rc = check(tag, txt)
                if rc == 20 and s.get("login_continue"): rc = 0
                if s.get("stop_unless_any") and not any(w in txt for w in s["stop_unless_any"]) and not rc and s.get("fuzzy_case"):  # G v1.2.4: 작은 글씨 OCR 3↔2 혼동, 같은 연도·길이·숫자 1개 차이만 허용
                    import re as _re
                    fy, fn = s["fuzzy_case"].split("타경")
                    near_ = [m for m in _re.findall(r"(20\d\d)타경(\d+)", txt) if m[0] == fy and len(m[1]) == len(fn) and sum(1 for p_, q_ in zip(m[1], fn) if p_ != q_) <= 1]
                    if not near_:  # 확대 OCR(4배)로 사건번호 줄만 다시 읽어 정확히 같으면 통과
                        try:
                            im_ = ImageGrab.grab(); zz = []
                            for l_ in L:
                                if "타경" in compact(l_["text"]) and 150 < l_["box"][1] < 320:
                                    b_ = l_["box"]; c_ = im_.crop((b_[0] - 6, b_[1] - 6, b_[2] + 6, b_[3] + 6)); c_ = c_.resize((c_.width * 4, c_.height * 4))
                                    zz.append(compact(" ".join(x_.get("text", "") for x_ in winocr.recognize_pil_sync(c_, "ko").get("lines", []))))
                            log("zoom_ocr", want=s["fuzzy_case"], seen=zz)
                            if any(s["fuzzy_case"] in z_ for z_ in zz): near_ = [(fy, fn)]
                        except Exception as e_: log("zoom_ocr_fail", err=str(e_)[:100])
                    if near_: log("fuzzy_case_ok", want=s["fuzzy_case"], seen=["%s타경%s" % m for m in near_], shot=tag); s = dict(s); s.pop("stop_unless_any")
                if s.get("stop_unless_any") and not any(w in txt for w in s["stop_unless_any"]) and not rc:
                    log("stop_unless_any", want=s["stop_unless_any"], shot=tag); rc = 22
                hit_ = [w for w in s.get("stop_if_any", []) if w in txt]
                if hit_ and not rc: log("stop_if_any", words=hit_, shot=tag); rc = 21
                if rc: break
                continue
            if k == "type":   # G v1.2.1: 숫자·영문만. 비밀번호·아이디 칸 근처면 거부
                t_ = str(s["text"])
                if not all(ch.isascii() and (ch.isalnum() or ch in "-.") for ch in t_):
                    log("type_refused", why="ascii_alnum_only"); rc = 6; break
                im = ImageGrab.grab(); L = layout(im); cx_, cy_ = a.cursor()
                near_ = [l["text"] for l in L if abs(l["center"][1] - cy_) <= 30 and abs(l["center"][0] - cx_) <= 260]
                if any(g in compact(x_) for x_ in near_ for g in ("비밀번호", "아이디", "인증서", "카드")):
                    log("type_refused", why="guard_near", near=near_[:6]); rc = 7; break
                a.type_text(t_[0], note="type first")
                for ch in t_[1:]:
                    time.sleep(random.uniform(0.15, 0.35)); a.type_text(ch, note="type char")
                log("typed", step=name, n=len(t_)); time.sleep(random.uniform(2, 4)); continue
            if k == "key":
                if s["combo"] not in KEYS_OK: log("key_refused", combo=s["combo"]); rc = 6; break
                a.key(s["combo"], note=name); time.sleep(random.uniform(s.get("wait_min", 2), s.get("wait_max", 4))); continue
            if k == "wheel":
                a.wheel(s.get("x", 960), s.get("y", 600), int(s["ticks"]), note=name); wsum[0] += int(s["ticks"]); log("wheel", step=name, ticks=int(s["ticks"]), wheel_sum=wsum[0]); time.sleep(random.uniform(2.5, 4.5)); continue
            if k == "find_dark_icon":   # 글자 없는 검은 원 아이콘(카카오 말풍선): 24x24 안 어두운 점 비율 0.6 이상인 곳(글자는 성김)
                im = ImageGrab.grab().convert("RGB"); x0, y0, x1, y1 = s["region"]; px = im.load(); best = None
                def dark(xx, yy):
                    r_, g_, b_ = px[xx, yy]; return r_ < 80 and g_ < 80 and b_ < 80
                for yy in range(y0, y1, 4):
                    for xx in range(x0, x1, 4):
                        n_ = sum(1 for dy_ in range(-12, 12, 3) for dx_ in range(-12, 12, 3) if dark(xx + dx_, yy + dy_))
                        if n_ / 64.0 >= 0.6:
                            best = (xx, yy); break
                    if best: break
                if not best: tag, _, _ = shot(name + "_notfound"); log("icon_not_found", shot=tag); rc = 5; break
                cx, cy = best[0], best[1] + 8
                log("icon_found", at=[cx, cy])
                for nxt in plan["steps"][i + 1:]:
                    if nxt.get("do") == "click_xy" and nxt.get("from_icon"): nxt["xy"] = [cx, cy]; break
                continue
            if k == "click_xy":   # 글자 없는 단추(아이콘): 앞 캡처에서 실측한 좌표, 주변 OCR 가드 그대로
                im = ImageGrab.grab(); L = layout(im); x, y = s["xy"]
                r = dict(text=s.get("label", ""), c=[x, y], box=[x, y, x, y], line="")
                bad = guard_ok(s, r, L)
                if bad: log("guard_refused", step=name, at=[x, y], why=bad); rc = 7; break
                a.click(x, y, hover_ms=random.randint(220, 420), note=name, safe=False); log("clicked_xy", step=name, at=[x, y])
                time.sleep(random.uniform(s.get("wait_min", 3), s.get("wait_max", 5)))
                tag, L2, txt = shot(name); results.append(dict(step=name, clicked=True, at=[x, y], shot=tag)); rc = check(tag, txt)
                if rc == 20 and s.get("login_continue"): rc = 0
                if rc: break
                continue
            if k in ("click_text", "find_text"):
                im = ImageGrab.grab(); L = layout(im)
                rs = cands(L, s)
                if not rs:
                    tag, L2, txt = shot(name + "_notfound"); log("not_found", step=name, want=s.get("text"), shot=tag)
                    results.append(dict(step=name, found=False, shot=tag))
                    if s.get("optional"): continue
                    rc = 5; break
                r = rs[s.get("index", 0)] if len(rs) > s.get("index", 0) else rs[0]
                x = r["c"][0] + s.get("off", [0, 0])[0]; y = r["c"][1] + s.get("off", [0, 0])[1]
                if k == "find_text":
                    log("found_only", step=name, text=r["text"], box=r["box"], center=[x, y], line=r["line"], n=len(rs))
                    results.append(dict(step=name, found=True, clicked=False, box=r["box"], center=[x, y], line=r["line"])); continue
                bad = guard_ok(s, r, L)
                if bad:
                    log("guard_refused", step=name, text=r["text"], at=[x, y], why=bad); results.append(dict(step=name, refused=bad, center=[x, y])); rc = 7; break
                if "예매" in compact(r["text"]) and not s.get("menu_ok"): state["yemae_used"] = dt.datetime.now().strftime("%H:%M:%S"); save_state()
                a.click(x, y, hover_ms=random.randint(220, 420), note=name, safe=False)
                log("clicked", step=name, text=r["text"], box=r["box"], at=[x, y], line=r["line"], n=len(rs))
                time.sleep(random.uniform(s.get("wait_min", 3), s.get("wait_max", 5)))
                if s.get("shot", True):
                    tag, L2, txt = shot(name); results.append(dict(step=name, clicked=True, at=[x, y], box=r["box"], text=r["text"], line=r["line"], shot=tag))
                    rc = check(tag, txt)
                    if rc == 20 and s.get("login_continue"): rc = 0   # 대표 지시: 로그인 화면에서 카카오 버튼까지 1회
                    if rc: break
                else: results.append(dict(step=name, clicked=True, at=[x, y], box=r["box"], text=r["text"]))
                continue
            log("unknown_step", step=s); rc = 6; break
    except Exception as e:
        log("error", err="%s: %s" % (type(e).__name__, str(e)[:240])); rc = 11 if str(e) == "pause_timeout" else 9  # G v1.2.3
        try: shot("error")
        except Exception: pass
    finally:
        if plan.get("allow_pay"):   # 결제 예외는 이 실행 한정: 끝나면 가드 원상 복구
            try:
                r_ = subprocess.run([sys.executable, os.path.join(AF, "tools", "c2_guard_paypatch.py"), "restore"], capture_output=True, text=True, timeout=30)
                log("paypatch_restore", out=(r_.stdout or r_.stderr).strip()[:200])
            except Exception as e_: log("paypatch_restore_fail", err=str(e_)[:120])
        if a is not None:
            try: a.park()
            except Exception: pass
            a.close()
        keep = ns.keep  # v1.1: 실패해도 --keep 이면 창 유지
        h = hwnd or state.get("hwnd")
        if h and not keep and u.IsWindow(h) and any(t in title(h) for t in ("코레일", "KORAIL", "카카오", "법원경매")):  # v1.2: 코레일 창만 닫음 / G: 법원경매 창도
            u.PostMessageW(h, 0x0010, 0, 0); state["closed"] = dt.datetime.now().strftime("%H:%M:%S"); state.pop("hwnd", None); save_state(); log("window_closed")
        json.dump(dict(rc=rc, kept=keep, results=results), open(os.path.join(out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        z = out + ".zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in os.listdir(out):
                if not f.endswith(".png") or plan.get("zip_png"): zf.write(os.path.join(out, f), f)  # v1.2 zip_png: 쪽그림 원본용
        try:
            import shutil; shutil.copy(z, os.path.join(os.environ.get("USERPROFILE", r"C:\Users\hmiller"), plan.get("zip_prefix", "c3steps_") + os.path.basename(z)))  # CopyToBox 허용 폴더
        except Exception: pass
        log("end", rc=rc, kept=keep, zip=z)
    return rc
if __name__ == "__main__":
    sys.exit(main())
