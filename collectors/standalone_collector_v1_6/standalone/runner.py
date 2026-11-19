# -*- coding: utf-8 -*-
"""사이트별 단계 JSON 을 그대로 실행하는 단독 수집기 본체 (문제 1~13).

순서(모든 단계 공통):
  STOP 파일(작업별) → 전원 → [동작] → 화면 확인(page_check: 오류표·위험화면·캡차) → 다음 단계
관제기 연결 코드 없음. 입력은 hw(피코2)만.
단계 종류: goto, shot, wait, click, hover, open_close, popups, login, logout, items_loop, note
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import random
import re
import time

from . import popup as popup_mod
from .locator import Locator, rbox, FORBID_DEFAULT
from .click_guard import CertClickBlocked
from .isleep import StopRequested
from . import vk_login
from .config import _truthy
from .refusal_log import RefusalLog, is_banned, host_of


class RunEnd(Exception):
    def __init__(self, why, detail=""):
        super().__init__(why)
        self.why, self.detail = why, detail


def _ns(s):
    return "".join(str(s or "").split())


class Runner:
    def __init__(self, job, spec, hw, cfg, *, log, stops, guard, pacer, rate_state, bank, storage=None,
                 power=None, ai=None, secrets=None, sleep=time.sleep, now=time.time, shots_dir=None,
                 console=None, **kwargs):
        self.job, self.spec, self.hw, self.cfg = job, spec, hw, cfg
        self.log, self.stops, self.guard, self.pacer, self.rs = log, stops, guard, pacer, rate_state
        self.storage, self.power, self.ai, self.secrets = storage, power, ai, secrets
        self.sleep, self.now, self.console = sleep, now, console
        self.account_lock = kwargs.get("account_lock")
        self.collector_id = cfg.get("collector_id", "")
        self.family = spec.get("family") or job.split("_")[0]
        self.site = spec.get("guard_site", "court" if self.family == "court" else "reservation")
        # v1.6(t845u): 판단 담당표·시간 예산·적응 대기(표는 관제기 참고자료에서 받아 메모리에서만 갱신)
        from .decision_routing import DecisionRouter, LADDER_STANDALONE
        from .time_budget import TimeBudget
        from .adaptive_wait import ReactionStats, AdaptiveWait
        self.refpack = kwargs.get("refpack")
        self.site_key = spec.get("site") or job
        rp = cfg.get("routing") or {}
        self.router = DecisionRouter(self._pack_json("decision_routing.json"), kwargs.get("ladder", LADDER_STANDALONE),
                                     rp.get("promote_after", 2), rp.get("demote_after", 3), log)
        self.budget = TimeBudget(now, (cfg.get("time_budget") or {}).get("deficit_sec", 20.0),
                                 (cfg.get("time_budget") or {}).get("slack_factor", 0.6))
        self.react = ReactionStats(self._pack_json("timing.json"), cfg.get("adaptive_wait"))
        gm = rate_state.effective_mult() if rate_state is not None and hasattr(rate_state, "effective_mult") else 1.0
        self.await_ = AdaptiveWait(self.react, self.family, gm, cfg.get("adaptive_wait"))
        self.locator = Locator(bank, hw.ocr_words, ai, log, router=self.router, budget=self.budget, now=now)
        self.shots = shots_dir or os.path.join(cfg["log_dir"], "shots", job)
        os.makedirs(self.shots, exist_ok=True)
        self.forbid = tuple(spec.get("forbid_words", [])) + FORBID_DEFAULT
        self.logged_in = False
        self.in_login = False
        self.recovery_input = False
        self.human_challenge = False
        self.solving_captcha = False
        from .screen_state import ScreenState, merge_caps
        self.screen_caps = merge_caps(spec.get("screen"))
        if self.family == "court" and "screen" not in spec:
            self.screen_caps["load_max_sec"] = max(self.screen_caps["load_max_sec"], 60.0)   # 법원 기본 60초
        self.screen = ScreenState(hw.grab, hw.ocr_text, now, self.sleep, log,
                                  guard_check=lambda im: guard.check(im, hw.ocr_text, hw.title()),
                                  bank_find=(lambda im, el: bool(bank.find(im, el, None))) if bank else None,
                                  timing_path=os.path.join(cfg["kb_dir"], "knowhow", "screen_timing_local.json"))
        hw.before_input = self.input_check
        self.done_path = os.path.join(cfg["state_dir"], "done_%s.json" % job)
        try:
            self.done = set(json.load(open(self.done_path, encoding="utf-8")))
        except Exception:  # noqa: BLE001
            self.done = set()
        self.t_start = now()
        self._last_ntp = now()          # 첫 NTP 배경확인은 cycle_min 뒤부터(R5)
        self.stats = {"items": 0, "saved": 0, "page_errors": 0, "ai_calls": 0}
        # v1.5: 사이트 거부 기록(JSONL, 덧붙이기만)
        self.refusals = kwargs.get("refusals") or RefusalLog(os.path.join(cfg["log_dir"], "refusals.jsonl"), now=now)
        # v1.5(W1): 정확한 입력칸 위치(로그인·캡차에서 찾은 칸). 조각그림·큰 그림 저장 때 가림.
        self.input_boxes = {}
        self._input_screens = {}
        # v1.6: 관제기에서 받은 사이트 참고자료(MemoryPack). 실기기(피코)는 반드시 있어야 시작.
        self.require_refpack = bool(kwargs.get("require_refpack", getattr(hw, "is_real", False)))
        if bank is not None and getattr(bank, "mask_fn", "x") is None:
            bank.mask_fn = self._mask_boxes
        # H5 보완: 긴 휴식 중에도 잠금 하트비트(60초 단위로 나눠 잠)
        if pacer is not None and hasattr(pacer, "sleep") and not getattr(pacer, "_hb_wrapped", False):
            inner = pacer.sleep

            def _sleep_hb(sec, _inner=inner):
                left = float(sec)
                while left > 0:
                    step = min(60.0, left)
                    _inner(step)
                    left -= step
                    self._last_hb = 0.0
                    self._heartbeat()
            pacer.sleep = _sleep_hb
            pacer._hb_wrapped = True

    # ---------------- v1.6 참고자료 표·적응 대기 ----------------
    def _pack_json(self, name):
        pk = getattr(self, "refpack", None)
        if pk is None:
            return {}
        try:
            from .site_pack import sp
            b = pk.get(sp(self.site_key, name))
            return json.loads(b.decode("utf-8")) if b else {}
        except Exception:  # noqa: BLE001
            return {}

    def _complexity(self, im):
        from .adaptive_wait import complexity, count_images
        try:
            words = len((self.hw.ocr_text(im, (0, 0) + im.size) or "").split())
        except Exception:  # noqa: BLE001
            words = 0
        try:
            rows = len(self.rows(im, self.spec["rows"])) if self.spec.get("rows") else 0
        except Exception:  # noqa: BLE001
            rows = 0
        try:
            imgs = count_images(im)
        except Exception:  # noqa: BLE001
            imgs = 0
        return complexity(words, rows, imgs)

    def wait_after(self, key, lo, hi=None, im=None):
        """클릭 뒤 첫 확인까지 쉼(적응 대기). 안전 간격(RateGate·429·휴식·계정 교대)은 여기 안 거침."""
        score = self._complexity(im) if im is not None else 0.0
        sec, info = self.await_.plan(key, lo, hi, score)
        sec2 = self.budget.slack_wait(sec, self.await_.floor)
        self.log("adaptive_wait", key=key, sec=round(sec2, 2), planned=round(sec, 2), **{k: info[k] for k in ("adj", "n", "score", "conservative")})
        self.sleep(sec2)
        return sec2

    def time_report(self):
        rep = self.budget.report()
        rep["routing_counts"] = self.router.counts
        return rep

    def _save_tables(self):
        """회차 끝: 판단 담당표·반응시간을 메모리 참고자료에 넣어 관제기로 올림(비밀값 없음)."""
        pk = getattr(self, "refpack", None)
        if pk is None or getattr(pk, "released", False):
            return
        from .site_pack import sp
        try:
            pk.put(sp(self.site_key, "decision_routing.json"), json.dumps(self.router.export(), ensure_ascii=False).encode("utf-8"),
                   kind="routing", representative=True)
            pk.put(sp(self.site_key, "timing.json"), json.dumps(self.react.t, ensure_ascii=False).encode("utf-8"),
                   kind="timing", representative=True)
        except Exception as e:  # noqa: BLE001
            self.log("tables_save_err", err=type(e).__name__)

    def _key_ok(self):
        """t851u: 2026-12-01 00:00 KST 뒤에도 자료 키가 시험 키면 경고하고 멈춘다."""
        from .crypto import key_from_config, KeyUpgradeRequired, KEY_MARK
        try:
            key_from_config(self.cfg, self.secrets).check_upgrade(self.now())
            return True, ""
        except KeyUpgradeRequired:
            return False, "key_upgrade_required " + KEY_MARK
        except Exception as e:  # noqa: BLE001
            return False, "data_key_missing " + type(e).__name__

    # ---------------- 공통 ----------------
    def sl(self, lo, hi=None):
        self.sleep(random.uniform(lo, hi if hi is not None else lo))

    def shot(self, name, im=None):
        # 로그인 화면 전체·실패 화면은 자격정보가 보일 수 있어 영구 사진 기록 금지.
        if self.in_login:
            return None
        im = im or self.hw.grab()
        txt = _ns(self.hw.ocr_text(im, (0, 0) + im.size))
        if "비밀번호" in txt or "통합로그인" in txt:
            return None
        p = os.path.join(self.shots, "%s_%s.png" % (dt.datetime.fromtimestamp(self.now()).strftime("%H%M%S"), name))
        try:
            im.save(p)
        except Exception:  # noqa: BLE001
            p = None
        return p

    def input_check(self, kind, note):
        self.pre(note)
        im = self.hw.grab()
        if self.recovery_input:
            # 오류 복구는 로그아웃·뒤로·탭 닫기만. 인증서·결제·보안 차단에서는 입력 안 함.
            d = self.guard.check(im, self.hw.ocr_text, self.hw.title(), login_phase=self.in_login)
            if d.kind in ("stop", "captcha_human"):
                raise RunEnd("blocked", d.code)
            if note not in ("logout", "logout yes", "back to find logout", "close tab (logout unavailable)"):
                raise RunEnd("unsafe_recovery_input")
            return
        if self.human_challenge:
            # 다른 봇 확인(체크박스·Cloudflare)은 사람이 직접. 프로그램 입력 금지.
            raise RunEnd("captcha_human_required")
        if self.solving_captcha:
            # 숫자 캡차 입력 중에는 같은 캡차 화면이라 재검사하면 무한반복. 그래도 위험/속도/세션은 본다.
            im = self.hw.grab()
            d = self.guard.check(im, self.hw.ocr_text, self.hw.title())
            if d.kind == "stop":
                raise RunEnd("blocked", d.code)
            if d.kind == "relogin":                       # V3: 캡차 입력 중 세션 만료 → 입력 중단
                self.solving_captcha = False              # 틀릴 수 있는 답을 기억에 저장하지 않음(verify 전에 끝냄)
                self.log("session_lost_in_captcha", where="captcha_input")
                raise RunEnd("session_lost_in_captcha")   # 다음 회차에서 새 캡차를 처음부터
            if d.kind in ("rate_limit", "rest"):          # N4/R2: 429 등 → 로그아웃+120분
                self.solving_captcha = False
                return self.handle(d, "captcha_input", im, 0)
            return
        self.page_check("before_" + kind)

    def pre(self, where):
        why = self.stops.check(self.job, self.family)
        if why:
            raise RunEnd("stop_file", why)
        mm = self.spec.get("max_minutes")
        if mm and self.now() - self.t_start > mm * 60:
            raise RunEnd("time_cap")
        if self.power and "stop" in self.power.check():
            raise RunEnd("battery_low")
        self._heartbeat()          # 긴 단계(목록 등) 중에도 잠금 유지(검수 H5)
        self._ntp_cycle()

    def _heartbeat(self):
        """잠금 하트비트(검수 H5). 서버·로컬 모두 과하지 않게 60초마다 한 번만."""
        if self.account_lock is None or not getattr(self, "_got_lock", False):
            return
        if self.now() - getattr(self, "_last_hb", 0.0) < 60:
            return
        self._last_hb = self.now()
        try:
            self.account_lock.heartbeat()
        except Exception as e:  # noqa: BLE001
            self.log("heartbeat_err", err=type(e).__name__)

    def _ntp_cycle(self):
        """R5: 10분 주기로 다음 지역 NTP 한 번만 미리 확인(순서대로 돌아감). 멈춤이 아니어도 배경 확인."""
        if not _truthy(self.cfg.get("ntp_check", True)):
            return
        if not getattr(self.hw, "is_real", False):   # 바깥(NTP) 요청은 실기기에서만
            return
        cyc = float(self.cfg.get("ntp", {}).get("cycle_min", 10)) * 60
        last = getattr(self, "_last_ntp", 0)
        if self.now() - last < cyc:
            return
        self._last_ntp = self.now()
        st = self._stall()
        nc = getattr(st, "ntp", None) if st else None
        if nc is not None and hasattr(nc, "next_region_probe"):
            try:
                nc.next_region_probe()
            except Exception as e:  # noqa: BLE001
                self.log("ntp_cycle_err", err=type(e).__name__)

    def page_check(self, where, ignore=(), _depth=0):
        """화면 확인. 문제가 없으면 화면 사진, 처리 후 다시 볼 수 있으면 다시 확인, 아니면 RunEnd."""
        im = self.hw.grab()
        d = self.guard.check(im, self.hw.ocr_text, self.hw.title(), ignore, login_phase=self.in_login)
        if d.ok:
            return im
        self.stats["page_errors"] += 1
        p = self.shot("PAGE_ERR_%s_%s" % (d.code, where), im)
        self.log("page_error", code=d.code, action=d.action, kind=d.kind, where=where, why=d.why, shot=p)
        if _depth >= 2:
            raise RunEnd("page_errors", d.code)
        return self.handle(d, where, im, _depth)

    def handle(self, d, where, im, depth):
        pc = self.cfg["pacing"]
        if d.kind == "rate_limit":                       # 문제 1
            r = self.rs.on_429(pc["rate_rest_min"], pc["after_429_gap_mult"], pc["max_429_per_day"], pc.get("gap_mult_max", 4.0))
            self.sl(30, 60)                              # 마우스 멈춤(요청 없음)
            self.safe_logout(after_error=True)
            self.rs.rest(pc["rate_rest_min"], "HTTP_429")  # 로그아웃 확인 뒤부터 최소 120분
            self.log("rate_limited", result=r, rest_min=pc["rate_rest_min"], gap_mult=self.rs.d["gap_mult"])
            raise RunEnd("rate_limited" if r == "rest" else "rate_limited_day_over")
        if d.kind == "stop":                             # 문제 7 등: 아무것도 안 누름
            self.stops.write(self.job, "%s %s %s" % (d.code, d.action, where))
            if d.rest_min:
                self.rs.rest(d.rest_min, d.code)
            if d.logout:
                self.safe_logout(after_error=True)
            raise RunEnd("blocked", d.code)
        if d.kind == "rest":
            self.safe_logout(after_error=True)
            self.rs.rest(d.rest_min, d.code)
            raise RunEnd("server_wait", d.code)
        if d.kind == "network":
            self.log("network_wait", sec=d.extra["wait_sec"])
            self.sleep(d.extra["wait_sec"])
            return self.page_check(where, _depth=depth + 1)
        if d.kind == "wait_queue":
            t0 = self.now()
            while self.now() - t0 < d.extra["wait_max_min"] * 60:
                self.sleep(30)
                im2 = self.hw.grab()
                if self.guard.check(im2, self.hw.ocr_text, self.hw.title()).kind != "wait_queue":
                    return self.page_check(where, _depth=depth + 1)
            raise RunEnd("queue_timeout")
        if d.kind == "relogin":
            if not self.spec.get("login"):
                raise RunEnd("session_lost")
            self.logged_in = False
            self.do_login({})
            return self.page_check(where, _depth=depth + 1)
        if d.kind == "back":
            self.hw.key("ALT+LEFT", "back (page error)")
            self.sl(2.0, 3.0)
            raise SkipItem(d.code)
        if d.kind == "captcha_number":                   # 문제 6: t761u (숫자 그림 캡차만 예외)
            if _truthy(self.cfg.get("captcha", {}).get("number_auto", True)):
                return self.do_captcha_number(im, where, depth)
            self.log("captcha_number_auto_off", where=where)   # 설정으로 꺼 두면 사람에게로 (규칙 10 기본)
            return self.do_captcha_human(im, where)
        if d.kind == "captcha_human":                    # 다른 봇 확인(체크박스·Cloudflare·그림고르기): 멈춤
            return self.do_captcha_human(im, where)
        return im

    # ---------------- 캡차 ----------------
    def _alert(self, title, detail=""):
        """사람 눈·귀에 띄게: 콘솔·소리·ALERT 파일 (검수 M9)."""
        try:
            import winsound
            winsound.MessageBeep()
        except Exception:  # noqa: BLE001
            pass
        print("\a*** BIDGUARD 사람 확인 필요: %s %s ***" % (title, detail), flush=True)
        try:
            rd = os.path.join(self.cfg["log_dir"], "..", "reports")
            os.makedirs(rd, exist_ok=True)
            stamp = dt.datetime.fromtimestamp(self.now()).strftime("%Y%m%d_%H%M%S")
            open(os.path.join(rd, "ALERT_%s.txt" % stamp), "w", encoding="utf-8").write(
                "%s %s %s\n" % (dt.datetime.fromtimestamp(self.now()).strftime("%Y-%m-%d %H:%M:%S"), title, detail))
        except Exception:  # noqa: BLE001
            pass

    def do_captcha_human(self, im, where):
        """체크박스·Cloudflare·그림 고르기: 풀지 않고 멈춤. 그림·질문만 남기고 알림."""
        from .captcha import prepare_human
        cs = self.spec.get("captcha", {})
        crop = im.crop(rbox(cs.get("image_box_r", [0.3, 0.3, 0.7, 0.6]), im.size))
        rec = prepare_human(os.path.join(self.cfg["state_dir"], "captcha_ask"), crop, now=self.now)
        self.log("captcha_human_required", where=where, image=rec["image"])
        self.stops.write(self.job, "captcha_human_required " + where)
        self._alert("캡차/로봇 확인", where)
        raise RunEnd("captcha_human_required", where)

    def _captcha_box(self, im, cs):
        """보안문자 그림 상자를 찾는다 (검수 R3/V4). 자르기는 '캡차 그림 자체' 기준:
        조각그림이면 그 중심, 라벨만 찾으면 image_offset_r 로 그림 방향으로 옮긴 상자. 못 찾으면 (fallback,'none')."""
        w, h = im.size
        off = cs.get("image_offset_r", [0.0, 0.085, 0.26, 0.11])   # 라벨 기준 그림 (dx,dy,너비,높이) 비율
        bw, bh = max(1, int(w * off[2] / 2)), max(1, int(h * off[3] / 2))
        if self.locator.bank:
            try:
                hit = self.locator.bank.find(im, "captcha_image", None)
            except Exception:  # noqa: BLE001
                hit = None
            if hit:     # 그림 자체를 찾음 → 그 중심에 그림 크기 상자(라벨 치우침 없음)
                return (max(0, hit[0] - bw), max(0, hit[1] - bh), min(w, hit[0] + bw), min(h, hit[1] + bh)), "snippet"
        for word in cs.get("image_anchor_words", ["보안문자", "자동입력방지", "숫자를입력", "그림의숫자"]):
            hit = self.locator.find(im, "captcha_anchor", [word],
                                    cs.get("input_box_r", [0.2, 0.25, 0.8, 0.8]), use_ai=False, learn=False)
            if hit:     # 라벨만 찾음 → 그림이 라벨 옆/아래라서 offset 만큼 옮겨 자름(V4 ②)
                cx, cy = hit[0] + int(off[0] * w), hit[1] + int(off[1] * h)
                return (max(0, cx - bw), max(0, cy - bh), min(w, cx + bw), min(h, cy + bh)), "ocr"
        return rbox(cs.get("image_box_r", [0.3, 0.3, 0.7, 0.6]), im.size), "none"

    def _captcha_crop_safe_for_ai(self, im, box):
        """자른 그림에 아이디/비밀번호/비밀값이 보이면 AI 금지 (검수 R3/V4). 대소문자 무시·부분일치."""
        if self.in_login:                 # 로그인 중에는 AI 로 안 보냄
            return False
        try:
            words = self.hw.ocr_words(im, box) or []
        except Exception:  # noqa: BLE001
            return False
        joined = _ns(" ".join(w[0] for w in words)).lower()   # V4 ①: 대소문자 무시
        for k in ("아이디", "비밀번호", "비번", "계정", "주소", "이메일", "id", "password", "passwd", "pw", "login", "e-mail"):
            if k in joined:
                return False
        red = getattr(self.ai, "red", None)
        if red is not None:
            try:
                flat = " ".join(w[0] for w in words)
                if red.text(flat) != flat:     # V4 ③: 비밀값이 부분이라도 보이면 금지
                    return False
            except Exception:  # noqa: BLE001
                pass
        return True

    def _field_box(self, im, x, y, cs=None):
        """찾은 입력칸 중심 → 그 칸 하나만 덮는 작은 상자(W1: 넓은 input_box_r 를 쓰지 않음)."""
        W, H = im.size
        fw, fh = (cs or {}).get("field_half_r", [0.075, 0.018])
        return (max(0, int(x - fw * W)), max(0, int(y - fh * H)), min(W, int(x + fw * W)), min(H, int(y + fh * H)))

    def _mask_boxes(self, im=None):
        """가릴 상자들: 찾은 ID/PW/캡차 입력칸 + 사이트 JSON 의 redact_box_r(정확한 칸일 때만)."""
        out = [tuple(b) for k, b in self.input_boxes.items() if b and self._box_same_screen(k, im)]
        r = (self.spec.get("captcha") or {}).get("redact_box_r")
        if r and im is not None:
            out.append(rbox(r, im.size))
        return out

    def _set_input_box(self, name, im, box):
        """v1.6(검수 E5): 입력칸은 그 칸을 찾은 화면에서만 가린다(화면 지문 함께 저장)."""
        from .refpack import dhash
        self.input_boxes[name] = box
        try:
            self._input_screens[name] = dhash(im)
        except Exception:  # noqa: BLE001
            self._input_screens[name] = None

    def _box_same_screen(self, name, im, dist=12):
        if im is None:
            return True
        h0 = getattr(self, "_input_screens", {}).get(name)
        if h0 is None:
            return True
        from .refpack import dhash, hamming
        try:
            return hamming(h0, dhash(im)) <= dist
        except Exception:  # noqa: BLE001
            return True

    def _mask_overlap(self, im, box):
        for b in self._mask_boxes(im):
            if min(box[2], b[2]) > max(box[0], b[0]) and min(box[3], b[3]) > max(box[1], b[1]):
                return True
        return False

    def do_captcha_number(self, im, where, depth):
        """숫자 그림 캡차(t761u): 기억 → (안전하면)AI 1번 → 틀리면 멈추고 'xxxxxx가 맞나요?' → 사람 답 입력."""
        from .captcha import solve_number, CaptchaMemory
        cs = self.spec.get("captcha", {})
        inp = self.locator.find(im, "captcha_input", cs.get("input_words", ["입력", "보안문자"]),
                                cs.get("input_box_r", [0.25, 0.3, 0.75, 0.75]), use_ai=False)
        if not inp:
            self.stops.write(self.job, "captcha_input_not_found")
            self._alert("캡차 입력칸 못 찾음", where)
            raise RunEnd("captcha_input_not_found")
        self._set_input_box("captcha_input", im, self._field_box(im, inp[0], inp[1], cs))
        box, found_by = self._captcha_box(im, cs)
        crop = im.crop(box)                             # W1: 가리지 않은 원래 그림(기억·사람·AI 공통)
        from .captcha import is_blank
        blank = is_blank(crop)
        if blank:                                       # W1: 거의 한 색이면 한 번 다시 찾아 자르고, 그래도면 사람
            self.log("captcha_crop_blank", where=where, found_by=found_by)
            box2 = rbox(cs.get("image_box_r", [0.3, 0.3, 0.7, 0.6]), im.size)
            if box2 != box and not is_blank(im.crop(box2)):
                box, found_by, crop, blank = box2, "fallback", im.crop(box2), False
        overlap = self._mask_overlap(im, box)           # W1: 정확한 입력칸(ID/PW/캡차)과 겹치면 AI 금지
        ai_ok = (self.ai is not None and found_by not in ("none", "fallback") and not blank and not overlap
                 and self._captcha_crop_safe_for_ai(im, box))
        if self.ai is not None and not ai_ok:
            self.log("captcha_ai_skipped", where=where, found_by=found_by, blank=blank, overlap=overlap)

        def type_digits(dg):
            self.hw.click(inp[0], inp[1], "captcha input")
            self.hw.key("CTRL+A", "select")
            self.hw.key("BACKSPACE", "clear")
            self.hw.type_text(dg, "captcha digits")
            ok = self.locator.find(self.hw.grab(), "captcha_ok", cs.get("ok_words", ["확인"]),
                                   cs.get("ok_box_r", [0.25, 0.3, 0.75, 0.85]), use_ai=False)
            if ok:
                self.hw.click(ok[0], ok[1], "captcha ok")
            else:
                self.hw.key("ENTER", "captcha enter")
            self.sl(1.5, 2.5)

        def verify():
            return self.guard.check(self.hw.grab(), self.hw.ocr_text, self.hw.title()).kind != "captcha_number"

        memory = CaptchaMemory(os.path.join(self.cfg["kb_dir"], "captcha_memory.json"))
        self.solving_captcha = True
        try:
          r = solve_number(crop, type_digits, verify,
                         ai_read=(self.ai.read_digits if ai_ok else None), memory=(None if blank else memory),
                         ask_dir=os.path.join(self.cfg["state_dir"], "captcha_ask"), log=self.log,
                         wait_sec=self.cfg["captcha"]["human_wait_sec"],
                         stop=lambda: bool(self.stops.check(self.job, self.family)),
                         sleep=self.sleep, now=self.now, console=self.console)
        finally:
            self.solving_captcha = False
        if r == "fail":
            self.stops.write(self.job, "captcha_fail")
            self._alert("캡차 실패", where)
            raise RunEnd("captcha_fail")
        self.log("captcha_solved", by=r, where=where)
        return self.page_check(where, _depth=depth + 1)

    # ---------------- 동작 ----------------
    def click_el(self, st, im=None, note=None):
        im = im or self.hw.grab()
        hit = self.locator.find(im, st["el"], st.get("words", []), st.get("box_r", [0, 0.05, 1, 0.6]),
                                st.get("avoid", []), self.forbid, st.get("goal"), use_ai=st.get("ai", True))
        if not hit:
            hx = st.get("hint_xy")
            if hx and self.spec.get("allow_hint_xy") and self.family != "court":
                hit = (hx[0], hx[1], "hint_xy")
            else:
                return None
        try:
            self.hw.click(hit[0], hit[1], note or st["el"])
        except CertClickBlocked as e:
            self.stops.write(self.job, "blocked_cert_click " + str(e)[:80])
            raise RunEnd("blocked_cert_click", str(e)[:80])
        self.log("click", el=st["el"], how=hit[2], at=[int(hit[0]), int(hit[1])])
        return hit

    def expect_words(self, words, box_r=None, timeout=8.0, interval=0.6):
        if not words:
            return True
        t0 = self.now()
        while True:
            im = self.hw.grab()
            box = rbox(box_r, im.size) if box_r else None
            txt = _ns(self.hw.ocr_text(im, box) if box else self.hw.ocr_text(im, (0, 0) + im.size))
            if any(_ns(w) in txt for w in words):
                return True
            if self.now() - t0 >= timeout:
                return False
            self.sleep(interval)

    def settle(self, where, expect=None, box_r=None, rows_fn=None, required=True):
        """화면 멈춤 판단. done 이면 True. 멈춤이면 원인 진단·대응.
        required=False 면 못 봐도 끊지 않고 trouble 기록 후 False (검수 R4)."""
        im0 = self.hw.grab()
        box = rbox(box_r, im0.size) if box_r else None
        state, waited, info = self.screen.settle(expect=expect, box=box, caps=self.screen_caps, rows_fn=rows_fn, key=where)
        if state == "done":
            self.react.add(where, waited)                 # v1.6: 사이트·단계별 실제 반응시간 누적
            return True
        return self._on_frozen(where, expect, box, rows_fn, state, required)

    def _on_frozen(self, where, expect, box, rows_fn, state, required=True):
        # (3) 멈춤/로딩상한: 먼저 page_guard(오류코드면 그 대응), 아니면 1번만 다시 확인.
        self.page_check(where)
        st2, _w, _i = self.screen.settle(expect=expect, box=box, caps=self.screen_caps, rows_fn=rows_fn, key=where)
        if st2 == "done":
            return True
        code, detail = self._diagnose(where)
        self.log("stall_diag", where=where, state=state, code=code, **{k: detail[k] for k in detail if k != "by_ai"})
        self.shot("stall_%s" % where)
        if code == "DATA_FLOWING":
            self.screen_caps["load_max_sec"] = min(self.screen_caps["load_max_sec"] * 2, 600.0)   # 더 기다림
            st3, _w, _i = self.screen.settle(expect=expect, box=box, caps=self.screen_caps, rows_fn=rows_fn, key=where)
            if st3 == "done":
                return True
        if code == "PC_HICCUP":
            self.log("pc_hiccup", where=where)       # 잠깐 멈췄다 깨어남 → 끊지 않고 한 번 더
            st3, _w, _i = self.screen.settle(expect=expect, box=box, caps=self.screen_caps, rows_fn=rows_fn, key=where)
            if st3 == "done":
                return True
        if not required:
            # 선택 단계(required:false)는 못 봐도 기록만 하고 계속 (R4)
            self.log("trouble", step=where, what="expect_not_seen_optional", code=code)
            return False
        if code == "PC_FROZEN":
            self.stops.write(self.job, "pc_frozen " + where)
            self._alert("PC 멈춤", where)
            raise RunEnd("pc_frozen", where)
        if code == "SITE_NO_RESPONSE":
            self.rs.rest(self.cfg.get("stall", {}).get("site_rest_min", 30), "site_no_response")   # 5xx 처럼 쉬고 재시도
            self.log("site_no_response", where=where)
            raise RunEnd("site_no_response", where)
        if code in ("ISP_DOWN", "LOCAL_NET_DOWN"):
            return self._wait_net_recover(where, expect, box, rows_fn, code)
        if code == "PC_HICCUP":
            self.stops.write(self.job, "screen_frozen_after_hiccup " + where)
            raise RunEnd("screen_frozen", where)
        # 원인 불명 → 새로고침 없이 멈춤
        self.stops.write(self.job, "screen_frozen " + where)
        self.log("trouble", step=where, what="screen_frozen")
        self._alert("화면 멈춤", where)
        raise RunEnd("screen_frozen", where)

    def _wait_net_recover(self, where, expect, box, rows_fn, code):
        # R6/V2: 마감 시각 하나, 설정값 사용, 대기 중 heartbeat.
        # 망이 끊긴 채로는 로그아웃을 '확인'할 수 없으므로 로그아웃을 시도하지 않고(=STOP 안 씀) 복구를 기다린다.
        # 복구되면 그때 로그인 상태를 확인(로그아웃)하고 net_recovered_relogin 으로 끝낸다.
        wait_min = self.cfg.get("stall", {}).get("net_down_wait_min", 120)
        deadline = self.now() + wait_min * 60
        was_logged_in = self.logged_in and bool(self.spec.get("login"))
        self.log("net_down_wait_start", where=where, code=code, wait_min=wait_min, logged_in=was_logged_in)
        while self.now() < deadline:
            if self.account_lock is not None:
                self.account_lock.heartbeat()                # 긴 대기 중에도 잠금 유지
            self.sleep(300)                                  # 5분 간격(사용자 STOP 은 여전히 봄)
            c2, _d = self._diagnose(where)
            if c2 not in ("ISP_DOWN", "LOCAL_NET_DOWN", "ALL_DOWN"):
                self.log("net_recovered", where=where, was=code)
                if was_logged_in:
                    self.safe_logout()                       # 복구됐으니 이제 로그인 상태 확인 후 로그아웃
                raise RunEnd("net_recovered_relogin", where)   # 다음 회차에 로그인부터
        self.stops.write(self.job, "%s %s" % (code.lower(), where))   # 끝내 복구 못 하면 그때 STOP
        raise RunEnd(code.lower(), where)

    def _diagnose(self, where, net_skip=False):
        d = self._stall()
        if d is None:
            return "UNKNOWN", {"step": "C", "no_diag": True}
        return d.diagnose(self.spec.get("site"))

    def _site_head(self):
        """사이트 JSON site_alive_check=true 일 때만, 그리고 법원·iros 가 아닐 때만 HEAD 확인(규칙)."""
        site = self.spec.get("site")
        if not site or not _truthy(self.spec.get("site_alive_check", False)):
            return None
        host = host_of(site)
        if self.family in ("court", "iros") or is_banned(site):
            self.log("site_alive_check_refused", site=site)      # 법원·iros 로는 직접 요청 금지
            self.refusals.policy_refused(host, "site_alive")      # v1.5: 요청은 안 보내고 POLICY_REFUSED 로 기록
            return None
        url = site if site.startswith("http") else "https://" + site
        retry = float(self.cfg.get("site_alive_retry_sec", 300))

        def head(_url=url, _host=host):
            import urllib.request
            import urllib.error
            nxt = getattr(self, "_head_next_ok", 0.0)
            if self.now() < nxt:                              # 다음 허용 시각 전에는 다시 안 보냄
                return False

            class _NoRedirect(urllib.request.HTTPRedirectHandler):   # v1.6(검수 E3·P4): 리다이렉트 안 따라감
                def redirect_request(self, *a, **k):
                    return None
            opener = urllib.request.build_opener(_NoRedirect)
            req = urllib.request.Request(_url, method="HEAD", headers={"User-Agent": self.cfg.get("user_agent", "BIDGUARD-standalone")})
            try:
                with opener.open(req, timeout=4) as r:
                    st = int(getattr(r, "status", 200))
                    if st >= 500:
                        self.refusals.remote_fail(_host, "site_alive", http_status=st, retry_sec=retry)
                        self._head_next_ok = self.now() + retry
                    return 200 <= st < 500
            except urllib.error.HTTPError as e:
                code = int(getattr(e, "code", 0) or 0)
                if 300 <= code < 400:                         # 넘기기: 따라가지 않고, 넘길 곳 호스트도 정책 검사
                    loc = (e.headers.get("Location") if e.headers else "") or ""
                    lh = host_of(loc) if "//" in loc else ""
                    if lh and is_banned(loc):
                        self.refusals.policy_refused(lh, "site_alive_redirect")
                        self.log("site_alive_redirect_refused", to=lh)
                    else:
                        self.log("site_alive_redirect", to=lh or "relative")
                    return True                               # 응답이 왔으니 '살아있음'
                self.refusals.remote_fail(_host, "site_alive", exc=e, retry_sec=retry)
                self._head_next_ok = self.now() + retry
                return 400 <= code < 500                      # 4xx 도 '살아있음'
            except Exception as e:  # noqa: BLE001
                self.refusals.remote_fail(_host, "site_alive", exc=e, retry_sec=retry)
                self._head_next_ok = self.now() + retry
                return False
        return head

    def _stall(self):
        if getattr(self, "stall", None) is not None:
            return self.stall
        try:
            from .stall_diag import StallDiag
            clock_box = self.spec.get("clock_box_r", [0.9, 0.95, 1.0, 1.0])
            def read_clock():
                im = self.hw.grab()
                return self.hw.ocr_text(im, rbox(clock_box, im.size))
            def move_mouse(dx, dy):
                w, h = self.hw.grab().size                  # 화면 크기에 맞춰 빈 곳(작업표시줄 위)
                self.hw.move(w // 2 + dx, h - 70 + dy, "stall probe")   # 몇 px (클릭 아님)
            def cursor_pos():                               # R1: 커서 위치(윈도우), 못 읽으면 None → PC_FROZEN 보류
                getp = getattr(self.hw, "cursor_pos", None)
                try:
                    return getp() if getp else None
                except Exception:  # noqa: BLE001
                    return None
            ntp = None
            if _truthy(self.cfg.get("ntp_check", True)):          # 전역 ntp_check 토글(기본 켜짐)
                try:
                    from .sntp import NtpChecker, merge_regions
                    nc = self.cfg.get("ntp", {})
                    cyc = float(nc.get("cycle_min", 10)) * 60
                    ntp = NtpChecker(regions=merge_regions(nc.get("regions"),
                                                           on_ban=lambda sv: self.refusals.policy_refused(sv, "ntp")),
                                     on_fail=lambda sv, why: self.refusals.remote_fail(sv, "ntp", reason=why, retry_sec=cyc),
                                     now=self.now,
                                     log=self.log, timeout=nc.get("timeout_sec", 2.0),
                                     hiccup_sec=nc.get("hiccup_sec", 30.0),
                                     stats_path=os.path.join(self.cfg["kb_dir"], "knowhow", "ntp_stats_local.json"))
                except Exception as e:  # noqa: BLE001
                    self.log("ntp_init_err", err=type(e).__name__)
            self.stall = StallDiag(move_mouse=move_mouse, grab=self.hw.grab, read_clock=read_clock,
                                   cursor_pos=cursor_pos, now=self.now, sleep=self.sleep, log=self.log, ntp=ntp,
                                   site_head=self._site_head(),
                                   deepseek=(self.ai.stall_hint if self.ai and hasattr(self.ai, "stall_hint") else None),
                                   stats_path=os.path.join(self.cfg["kb_dir"], "knowhow", "stall_stats_local.json"))
        except Exception as e:  # noqa: BLE001
            self.log("stall_init_err", err=type(e).__name__)
            self.stall = None
        return self.stall

    def do_goto(self, st):
        ab = self.spec.get("addr_bar_xy", [420, 62])
        self.hw.click(ab[0] + random.randint(-60, 60), ab[1], "address bar")
        self.sl(0.5, 0.9)
        self.hw.type_text(st["url"].split("//")[-1], "goto")
        self.sl(0.3, 0.6)
        self.hw.key("ENTER", "goto enter")
        self.wait_after(st.get("name") or "goto", *st.get("wait", [4.0, 6.0]))
        self.page_check("goto")
        if st.get("expect"):
            self.settle(st.get("name") or "goto", expect=[st["expect"]], box_r=[0, 0, 1, 0.12],
                        required=st.get("required", False))   # goto expect 는 기본 기록만(R4)

    def do_click(self, st):
        im = self.page_check("before_" + st.get("name", st["el"]))
        hit = self.click_el(st, im)
        if not hit:
            self.log("trouble", step=st.get("name"), what="not_found", el=st["el"])
            if st.get("required", True):
                raise RunEnd("not_found", st["el"])
            return
        self.wait_after("after_" + st.get("name", st["el"]), *st.get("wait", [1.5, 2.5]), im=im)
        im_after = self.page_check("after_" + st.get("name", st["el"]))
        self._gallery(st.get("name", st["el"]), im_after)
        if st.get("shot") and st["type"] in ("click", "nav_click"):
            self.shot(st["shot"])
        if st.get("expect_words"):
            self.settle("after_" + st.get("name", st["el"]), expect=st["expect_words"],
                        box_r=st.get("expect_box_r"), required=st.get("required", True))

    do_nav_click = do_click

    def do_select_scroll(self, st):
        """C8: 드롭다운을 열고 휠 한 칸씩 → 정확한 이름 OCR → 안 되면 아래키 → 그래도 없으면 STOP(COURT_NOT_FOUND).
        피코2 CLICK/WHEEL/KEY 만. 정확 일치만 클릭(부분일치 금지), avoid·수도권 거부는 사이트 JSON 으로."""
        from .court_select import pick_court
        target = st["target"]
        im = self.page_check("before_" + st.get("name", "select"))
        fb = rbox(st.get("field_box_r", [0.25, 0.25, 0.85, 0.55]), im.size)
        lb = rbox(st.get("list_box_r", [0.25, 0.3, 0.7, 0.95]), im.size)
        res = pick_court(
            target, self.hw.grab, lambda g, box: self.hw.ocr_words(g, box) or [],
            lambda x, y, note: self.hw.click(x, y, note),
            lambda x, y, t, note: self.hw.wheel(x, y, t, note),
            lambda x, y: self.hw.move(x, y, "select list"),
            lambda k, note: self.hw.key(k, note),
            page_guard=lambda where: self.page_check("select_" + where),
            log=lambda ev=None, **k: self.log("select_scroll", step=ev, **k),
            field_box=fb, list_box=lb, max_wheel=int(st.get("max_wheel", 16)), max_key=int(st.get("max_key", 40)),
            sleep=self.sleep, refuse_metro=_truthy(st.get("refuse_metro", False)), avoid=tuple(st.get("avoid", ())))
        if res.get("status") != "OK":
            self.log("trouble", step=st.get("name"), what=res.get("status"), target=target)
            if st.get("required", True):
                self.stops.write(self.job, res.get("status", "COURT_NOT_FOUND"))
                self._alert("법원 못 찾음", "%s %s" % (target, res.get("status")))
                raise RunEnd(res.get("status", "COURT_NOT_FOUND"), target)
            return
        self.sl(*st.get("wait", [1.0, 1.8]))
        self._gallery(st.get("name", "select"), self.page_check("after_" + st.get("name", "select")))

    def _gallery(self, step, im=None):
        """v1.6: 단계별 전체 화면 화보(대표). 로그인 중엔 안 찍고, ID/PW/캡차 칸이 보이면 그 칸을 찾아 둔 때만(가림)."""
        bank = self.locator.bank
        if bank is None or not hasattr(bank, "save_gallery") or self.in_login:
            return None
        try:
            im = im or self.hw.grab()
            txt = _ns(self.hw.ocr_text(im, (0, 0) + im.size))
            if ("비밀번호" in txt or "아이디" in txt) and not {"login_id", "login_pw"} <= set(self.input_boxes):
                self.log("gallery_skip", step=step, why="login_fields_not_located")
                return None
            if ("보안문자" in txt or "자동입력방지" in txt) and "captcha_input" not in self.input_boxes:
                self.log("gallery_skip", step=step, why="captcha_field_not_located")
                return None
            forb = any(w in txt for w in ("인증서", "공동인증", "금융인증", "간편인증"))
            dist = bank.gallery_distance(im, step)
            fn = bank.save_gallery(im, step, forbidden=forb)
            self.log("gallery", step=step, saved=fn, forbidden=forb, dist=dist)
            return fn
        except Exception as e:  # noqa: BLE001
            self.log("gallery_err", step=step, err=type(e).__name__)
            return None

    def do_hover(self, st):
        im = self.page_check("before_hover")
        hit = self.locator.find(im, st["el"], st.get("words", []), st.get("box_r", [0, 0.05, 1, 0.5]),
                                st.get("avoid", []), self.forbid, use_ai=st.get("ai", True))
        if not hit:
            raise RunEnd("not_found", st["el"])
        self.hw.move(960 + random.randint(-40, 40), 700, "neutral")
        self.hw.hover(hit[0], hit[1], random.randint(700, 950), st["el"])

    def do_open_close(self, st):
        self.do_click(dict(st, required=st.get("required", False)))
        if st.get("shot"):
            self.shot(st["shot"])
        for c in st.get("close", [{"how": "esc"}]):
            how = c.get("how")
            if how == "esc":
                self.hw.key("ESC", "close esc")
            elif how == "same":
                self.click_el(st, note="close same")
            elif how == "outside":
                W, H = self.hw.grab().size
                self.hw.click(int(c["rx"] * W), int(c["ry"] * H), "close outside")
            elif how == "words":
                self.click_el({"el": st["el"] + "_close", "words": c["words"], "box_r": c.get("box_r", [0, 0, 1, 1])})
            self.sl(0.8, 1.4)
            if not st.get("closed_words") or not self.expect_words(st["closed_words"], st.get("closed_box_r"), 1.5):
                break
        self.page_check("after_close")

    def do_popups(self, st):
        ok = popup_mod.close_all(self.hw.grab, lambda im: self.hw.ocr_lines(im), self.hw.click, self.hw.key,
                                 self.log, settle=lambda: self.sl(1.0, 1.6))
        if not ok and st.get("required", False):
            raise RunEnd("popup_left")
        self.page_check("after_popups")

    def do_login(self, st):
        """문제 13: 로그인 화면에서는 아이디/비밀번호 탭·칸·로그인 버튼만. 인증서는 ClickGuard 가 막는다."""
        if self.logged_in:
            return
        self.in_login = True
        try:
            self._login()
        finally:
            self.in_login = False

    def _login(self):
        L = self.spec["login"]
        im = self.page_check("login_start", ignore=("SESSION_EXPIRED",))
        if self.expect_words(L.get("logged_in_words", ["로그아웃"]), L.get("top_box_r", [0.5, 0.1, 0.75, 0.2]), 1):
            self.logged_in = True
            return
        self.do_click({"name": "top_login", "el": "top_login", "words": ["로그인"], "box_r": L.get("top_box_r", [0.5, 0.1, 0.75, 0.2]),
                       "avoid": ["인증서"], "ai": False, "wait": [2.5, 3.5]})
        im = self.hw.grab()
        # 아이디/비밀번호 탭 (인증서 탭이 켜져 있을 때만)
        self.click_el({"el": "idpw_tab", "words": ["아이디", "비밀번호로그인"], "box_r": L.get("tab_box_r", [0.45, 0.25, 0.72, 0.36]),
                       "avoid": ["인증서"], "ai": False}, im)
        self.sl(1.0, 1.6)
        uid = self._cred(["COURT_LOGIN_ID", "login_id", "id"])
        upw = self._cred(["COURT_LOGIN_PW", "password", "pw"])
        if not uid or not upw:
            raise RunEnd("no_login_credentials")
        f_id = self.click_el({"el": "login_id_box", "words": ["아이디"], "box_r": L.get("card_box_r", [0.31, 0.3, 0.69, 0.75]),
                              "avoid": ["인증서", "찾기"], "ai": False})
        if not f_id:
            raise RunEnd("login_id_box_not_found")
        _im = self.hw.grab()
        self._set_input_box("login_id", _im, self._field_box(_im, f_id[0], f_id[1]))
        self.hw.type_text(uid.reveal(), "login id", secret=True)
        f_pw = self.click_el({"el": "login_pw_box", "words": ["비밀번호"], "box_r": L.get("card_box_r", [0.31, 0.3, 0.69, 0.75]),
                              "avoid": ["인증서", "찾기", "변경"], "ai": False})
        if not f_pw:
            raise RunEnd("login_pw_box_not_found")
        _im = self.hw.grab()
        self._set_input_box("login_pw", _im, self._field_box(_im, f_pw[0], f_pw[1]))
        if L.get("password_mode", "virtual_keypad" if self.family == "court" else "keyboard") == "virtual_keypad":
            self._vk_password(upw, L)                      # iros·법원: 가상키패드만 (검수 H3)
        else:
            self.hw.type_text(upw.reveal(), "login pw", secret=True)
        self.sl(0.4, 0.8)
        self.hw.key("ENTER", "login enter")
        self.sl(3.0, 4.0)
        self.page_check("after_login")
        if not self.expect_words(L.get("logged_in_words", ["로그아웃"]), L.get("top_box_r", [0.5, 0.1, 0.75, 0.2]), 8):
            self.shot("login_fail")
            raise RunEnd("login_fail")
        self.logged_in = True
        self.log("login_ok")

    def _cred(self, names):
        # M8: 별칭(COURT_LOGIN_ID/login_id/id 등)을 먼저 '묻지 않고' 모두 확인, 없을 때만 대표 이름으로 입력받음.
        if not self.secrets:
            return None
        for n in names:
            v = self.secrets.get(n, required=False)
            if v:
                return v
        return self.secrets.get(names[0], required=True)

    def _vk_password(self, upw, L):
        vk = L.get("vk", {})
        stars_box_r = vk.get("stars_box_r", L.get("card_box_r", [0.31, 0.3, 0.69, 0.75]))

        def clear():
            c = self.locator.find(self.hw.grab(), "vk_clear", vk.get("clear_words", ["모두지움", "전체삭제", "지움"]),
                                  vk.get("box_r", [0.25, 0.4, 0.8, 0.98]), use_ai=False)
            if not c:
                return False
            self.hw.click(c[0], c[1], "vk clear")
            self.sl(0.4, 0.8)
            return True

        def count_stars():
            txt = self.hw.ocr_text(self.hw.grab(), rbox(stars_box_r, self.hw.grab().size))
            return sum(txt.count(ch) for ch in ("●", "*", "•", "·", "＊"))

        def find_key(label):
            # 요소 이름은 비번 글자와 무관한 상수(검수 N5). 로그·조각그림 열쇠에 비번이 안 샘.
            hit = self.locator.find(self.hw.grab(), "vk_key", [str(label)],
                                    vk.get("box_r", [0.25, 0.4, 0.8, 0.98]), use_ai=False, learn=False)
            return (hit[0], hit[1]) if hit else None

        def click_key(x, y, note):
            self.hw.click(x, y, note)        # note 는 vk_login 이 'vk key' 처럼 글자 없이 줌

        res = vk_login.type_password(upw, click_key=click_key, count_stars=count_stars, clear=clear,
                                     find_key=find_key, log=self.log, settle=lambda: self.sl(0.25, 0.4),
                                     same_char_pause=lambda: self.sl(0.7, 0.9), jitter_px=self.cfg.get("vk_jitter_px", 5))
        if not res.ok:
            self.shot("vk_login_stop")
            self.stops.write(self.job, "vk_login %s (%d/%d)" % (res.why, res.entered, res.expected))
            self._alert("가상키패드 비번 입력 멈춤", res.why)
            raise RunEnd("vk_login_" + res.why)

    def safe_logout(self, after_error=False):
        old = self.recovery_input
        self.recovery_input = True
        try:
            return self._safe_logout(after_error)
        finally:
            self.recovery_input = old

    def _safe_logout(self, after_error=False):
        """로그아웃: 상단 '로그아웃' → 확인창 '예'. 오류 화면이면 크롬 뒤로 1번 후 1번 더. 새로고침 금지."""
        if not self.spec.get("login"):
            return False
        L = self.spec["login"]
        if self._logout_confirmed(L):                    # W2: 이미 로그아웃된 화면이면 STOP 없이 끝
            self.logged_in = False
            self.log("logout", ok=True, already=True, after_error=after_error)
            return True
        for attempt in range(2):
            im = self.hw.grab()
            hit = self.locator.find(im, "logout", ["로그아웃"], L.get("top_box_r", [0.5, 0.1, 0.75, 0.2]),
                                    use_ai=False, learn=False)
            if hit:
                try:
                    self.hw.click(hit[0], hit[1], "logout")
                    self.sl(1.0, 1.6)
                    yes = self.locator.find(self.hw.grab(), "logout_yes", ["예", "확인"], [0.25, 0.2, 0.75, 0.8],
                                            avoid=["아니"], use_ai=False, learn=False)
                    if not yes:                              # 확인창 '예'를 못 찾으면 실패로 본다 (검수 M1)
                        self.log("logout", ok=False, why="confirm_yes_not_found")
                        continue
                    self.hw.click(yes[0], yes[1], "logout yes")
                except CertClickBlocked:
                    return False
                self.sl(1.5, 2.5)
                if self._logout_confirmed(L):               # 상단 상자만, 1초 간격 2번 연속
                    self.logged_in = False
                    self.log("logout", ok=True, after_error=after_error)
                    return True
            if attempt == 0 and after_error:
                self.hw.key("ALT+LEFT", "back to find logout")
                self.sl(2.0, 3.0)
        if after_error:
            self.hw.key("CTRL+W", "close tab (logout unavailable)")
        self.stops.write(self.job, "logout_unconfirmed")
        self.log("logout", ok=False, after_error=after_error, tab_closed=after_error)
        return False

    def _logout_confirmed(self, L):
        box = L.get("top_box_r", [0.5, 0.1, 0.75, 0.2])
        for i in range(2):
            txt = _ns(self.hw.ocr_text(self.hw.grab(), rbox(box, self.hw.grab().size)))
            if not ("로그인" in txt and "로그아웃" not in txt):
                return False
            if i == 0:
                self.sleep(1.0)
        return True

    # ---------------- 물건 돌기 ----------------
    def rows(self, im, R):
        box = rbox(R.get("box_r", [0.2, 0.3, 0.9, 0.95]), im.size)
        rx = re.compile(R.get("regex", r"\d{4}\s*타\s*경\s*\d+"))
        require = [_ns(w) for w in R.get("require_words", [])]     # 예: ['신건'] — 비수도권 신건만 (H4)
        ws = self.hw.ocr_words(im, box)
        out, seen = [], set()
        for t, x, y in sorted(ws, key=lambda w: w[2]):
            m = rx.search(t or "")
            if not m:
                continue
            key = _ns(m.group(0))
            if key in seen:
                continue
            if require:
                line = _ns(" ".join(w[0] for w in ws if abs(w[2] - y) <= R.get("line_h", 16)))
                if not any(rw in line for rw in require):
                    continue
            seen.add(key)
            out.append((key, x, y))
        return out

    def wait_download(self, since, timeout=60):
        d = os.path.expandvars(self.spec.get("download_dir", "%USERPROFILE%\\Downloads"))
        t0 = self.now()
        while self.now() - t0 < timeout:
            fs = [p for p in glob.glob(os.path.join(d, "*")) if not p.endswith((".crdownload", ".tmp"))
                  and os.path.getmtime(p) >= since]
            if fs:
                return max(fs, key=os.path.getmtime)
            self.sleep(2)
        return None

    def do_items_loop(self, st):
        R = st["rows"]
        pc = self.cfg["pacing"]
        pages = 0
        while True:
            im = self.page_check("list")
            self.settle("list", rows_fn=lambda im2: self.rows(im2, R))     # 목록 로딩·줄 수 안정까지
            im = self.hw.grab()
            todo = [r for r in self.rows(im, R) if r[0] not in self.done]
            for key, x, y in todo:
                self.pre("item")
                self._heartbeat()              # 40건 루프 안에서도 잠금이 '오래됨'으로 처리되지 않게(검수 H5)
                waited = self.pacer.before_item(self.log)
                if waited is None:
                    self.log("run_cap", items=self.stats["items"])
                    raise RunEnd("run_cap")
                self.stats["items"] += 1
                try:
                    self.hw.click(x, y, "item " + key)
                    self.sl(2.5, 3.5)
                    self.page_check("detail")
                    for sub in st.get("per_item", []):
                        self.run_item_step(sub, key)
                    self.done.add(key)
                    self._save_done()
                    self.guard.reset_consecutive()
                except SkipItem as e:
                    self.log("item_skip", case=key, why=e.code)
                except CertClickBlocked as e:
                    self.stops.write(self.job, "blocked_cert_click")
                    raise RunEnd("blocked_cert_click", str(e)[:80])
                back = st.get("back")
                if back:
                    self.do_click(dict(back, required=False))
                else:
                    self.hw.key("ALT+LEFT", "back to list")
                    self.sl(2.5, 3.5)
                self.page_check("back_list")
            nxt = st.get("next_page")
            pages += 1
            if not nxt or pages >= st.get("max_pages", 50):
                return
            im = self.page_check("pager")
            if not self.click_el(nxt, im):
                return
            self.sl(2.0, 3.0)

    def _save_done(self):
        os.makedirs(os.path.dirname(self.done_path), exist_ok=True)
        json.dump(sorted(self.done), open(self.done_path, "w", encoding="utf-8"), ensure_ascii=False)

    def run_item_step(self, sub, key):
        t = sub["type"]
        if t == "download":
            since = self.now() - 1
            hit = self.click_el(sub)
            if not hit:
                self.log("trouble", what="download_btn_not_found", case=key)
                return
            self.sl(2.0, 3.0)
            self.page_check("pdf")
            sd_words = sub.get("save_dialog_words", ["다른 이름으로 저장", "저장"])
            sd_box = rbox(sub["save_dialog_box_r"], self.hw.grab().size) if sub.get("save_dialog_box_r") else None
            sd_state, _w, _i = self.screen.settle(expect=sd_words, box=sd_box, caps=self.screen_caps,
                                                  key="save_dialog")     # 뷰어 로딩 중이면 더 기다림
            dlg = sd_state == "done"
            r = self.guard.on_save_result(dlg, self.cfg["pacing"]["no_save_dialog_suspect_in_a_row"])
            if not dlg:                                    # 문제 2
                self.page_check("no_save_dialog")          # 오류 화면인지 먼저
                self.log("no_save_dialog", case=key, in_row=self.guard.no_save_in_row, result=r)
                if r == "suspect_rest":
                    self.log("rate_suspect", rest_min=self.cfg["pacing"]["suspect_rest_min"])
                    self.sleep(self.cfg["pacing"]["suspect_rest_min"] * 60)
                elif r == "rate_limit":
                    from .screen_guard import Decision
                    self.handle(Decision("rate_limit", "NO_SAVE_DIALOG_x3", "RATE_LIMIT", "저장창 3번 연속 안 뜸",
                                         logout=True), "no_save_dialog", self.hw.grab(), 0)
                return
            self.hw.key("ENTER", "save")
            f = self.wait_download(since)
            if f and self.storage:
                dst, where = self.storage.adopt_file(f, os.path.join(self.job, key, os.path.basename(f)))
                self.stats["saved"] += 1
                self.log("saved", case=key, where=where, path=dst)
            return
        if t == "click":
            self.do_click(dict(sub, required=sub.get("required", False)))
        elif t == "shot":
            self.shot("%s_%s" % (key, sub.get("shot", "item")))
        elif t == "wait":
            self.sl(*sub.get("sec", [1, 2]))
        elif t == "key":
            self.hw.key(sub["key"], sub.get("name", "key"))
            self.sl(0.8, 1.4)
            self.page_check("after_key")

    # ---------------- 실행 ----------------
    def _region_ok(self):
        """법원 범위 확인 (검수 H4). 수집기 region 과 사이트 region_required 가 다르면 거부."""
        need = self.spec.get("region_required")
        if not need:
            # 법원 회차는 범위를 꼭 밝혀야 함(서버가 빼서 통과시키는 것 방지, 검수 H4③)
            if self.family == "court":
                return False, "region_required_missing"
            return True, ""
        reg = self.cfg.get("region") or ("" if "-" not in self.collector_id else self.collector_id.split("-")[0])
        if not reg:
            return False, "region_unset"
        if reg != need:
            return False, "region_%s_needs_%s" % (reg, need)
        return True, ""

    def run(self):
        steps = self.spec.get("steps", [])
        self.t_start = self.now()
        if not self.collector_id:                        # 검수 M11
            self.log("not_started", why="collector_id_unset")
            return {"end": "config_error", "why": "collector_id_unset", "stats": self.stats}
        rok, rwhy = self._region_ok()
        if not rok:
            self.log("not_started", why=rwhy)
            return {"end": "region_refused", "why": rwhy, "stats": self.stats}
        kok, kwhy = self._key_ok()
        if not kok:
            self.stops.write(self.job, kwhy)
            self.log("not_started", why=kwhy)
            self._alert("자료 키 갱신 필요", "2026-12-01 00:00 KST 가 지났는데 자료 키가 시험 키예요. 설정 crypto 를 바꾸세요.")
            return {"end": "key_upgrade_required", "why": kwhy, "stats": self.stats}
        # v1.6(t844u): 온라인 전제. 실기기에서는 관제기에서 받은 참고자료(메모리)가 없으면 시작하지 않는다(폴백 없음).
        if self.require_refpack and self.refpack is None:
            self.stops.write(self.job, "server_required refpack_not_loaded")
            self.log("not_started", why="refpack_not_loaded")
            return {"end": "server_required", "why": "refpack_not_loaded", "stats": self.stats}
        ok, why = self.rs.can_start()
        if not ok:
            self.log("not_started", why=why)
            return {"end": "rest_wait", "why": why, "stats": self.stats}
        # 같은 계정 동시 사용 피하기 (검수 H5): 법원 로그인 회차만
        got_lock = False
        if self.account_lock is not None and self.spec.get("login"):
            uid = self._cred(["COURT_LOGIN_ID", "login_id", "id"])
            if uid is not None:
                from .account_lock import account_tag
                self.account_lock.tag = account_tag(uid)
                self.account_lock.path = os.path.join(self.cfg["state_dir"], "court_account_%s.lock" % self.account_lock.tag)
                lok, lwhy = self.account_lock.acquire()
                self.log("account_lock", ok=lok, why=lwhy)
                if not lok:
                    return {"end": "account_busy", "why": lwhy, "stats": self.stats}
                got_lock = True
        self._got_lock = got_lock
        self._last_hb = self.now()
        self.log("start", site=self.spec.get("site"), collector_id=self.collector_id,
                 steps=len(steps), limits=self.pacer.limits())
        end, detail = "done", ""
        try:
            for st in steps:
                self.pre(st.get("name", st["type"]))
                if self.account_lock is not None and got_lock:
                    self.account_lock.heartbeat()
                getattr(self, "do_" + st["type"])(st)
        except RunEnd as e:
            end, detail = e.why, e.detail
        except StopRequested as e:                       # 긴 잠자기 중 STOP (검수 M2)
            end, detail = "stop_file", e.why
        except SkipItem as e:
            end, detail = "page_errors", e.code
        finally:
            if self.logged_in and end not in ("blocked", "blocked_cert_click", "rate_limited", "rate_limited_day_over", "stop_file", "captcha_human_required", "captcha_fail"):
                try:
                    self.safe_logout()
                except (RunEnd, StopRequested):
                    pass
            if got_lock and self.account_lock is not None:
                self.account_lock.release()
            if self.storage:
                try:
                    self.storage.flush_to_nas()
                except Exception:  # noqa: BLE001
                    pass
        tr = self.time_report()
        self._save_tables()
        self.log("run_report", **tr)
        self.log("end", end=end, detail=detail, stats=self.stats)
        return {"end": end, "detail": detail, "stats": self.stats, "time_report": tr}

    def do_shot(self, st):
        self.page_check("shot")
        self.shot(st.get("shot", st.get("name", "shot")))

    def do_wait(self, st):
        self.sl(*st.get("sec", [1, 2]))

    def do_note(self, st):
        self.log("note", text=st.get("text", ""))

    def do_logout(self, st):
        self.safe_logout()


class SkipItem(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code
