# -*- coding: utf-8 -*-
"""캡차 처리.

숫자 그림 캡차(한영님 t761u, 본인 계정·본인 지시):
  ① 조각그림/기억 먼저 — 같은 그림을 전에 풀었으면 그 숫자를 씀.
  ② 없으면 AI 가 판단해 **딱 한 번** 입력.
  ③ 틀리면 다시 시도하지 않고 멈춤 → 그림과 "376689가 맞나요?" 질문을 저장 →
     사람이 답(answer.json{digits} 또는 콘솔)하면 그 숫자를 **한 번** 입력.
다른 봇 확인(체크박스 reCAPTCHA·Cloudflare·그림 고르기)은 풀지 않고 멈춤(prepare_human + STOP).
비밀값이 아니라 캡차 숫자이므로 기록에 남겨도 되지만, 이미지 파일만 남기고 숫자는 question 에만 둔다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import time
import uuid


def digits(s):
    return "".join(re.findall(r"\d", str(s or "")))


def is_blank(crop, min_std=6.0, min_range=24):
    """거의 한 색(하얀/회색 판)이면 True (W1: 이런 그림은 AI·기억에 쓰지 않음)."""
    try:
        from PIL import ImageStat
        g = crop.convert("L")
        lo, hi = g.getextrema()
        return (hi - lo) < min_range or ImageStat.Stat(g).stddev[0] < min_std
    except Exception:  # noqa: BLE001
        return True


def _img_key(crop):
    """그림 해시 열쇠. 빈 그림이면 None(열쇠 없음 → 기억 안 씀)."""
    if is_blank(crop):
        return None
    small = crop.convert("L").resize((48, 16))
    return hashlib.sha256(small.tobytes()).hexdigest()[:24]


class CaptchaMemory:
    """전에 사람·AI 가 맞춘 숫자 그림을 그림 모양으로 기억(조각그림 먼저용)."""

    def __init__(self, path):
        self.path = path
        try:
            self.d = json.load(open(path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self.d = {}

    def get(self, crop):
        k = _img_key(crop)
        return self.d.get(k) if k else None

    def put(self, crop, dg):
        k = _img_key(crop)
        if k and 4 <= len(dg) <= 8:
            self.d[k] = dg
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            json.dump(self.d, open(tmp, "w", encoding="utf-8"))
            os.replace(tmp, self.path)


def prepare_human(ask_dir, crop, guess="", now=time.time):
    """멈춤 길(체크박스·Cloudflare·그림고르기, 또는 숫자캡차 시간초과). 그림·질문만 남김."""
    os.makedirs(ask_dir, exist_ok=True)
    uid = uuid.uuid4().hex
    img = os.path.join(ask_dir, "captcha_" + uid + ".png")
    crop.save(img)
    question = (str(guess) + "가 맞나요? 맞으면 그대로, 틀리면 그림의 숫자를 적어 answer.json 에 넣어 주세요.") if guess \
        else "그림의 숫자를 answer.json{\"digits\": \"...\"} 에 적어 주세요(또는 브라우저에서 직접)."
    rec = {"request_id": uid, "image": img, "guess": guess, "question": question,
           "answer_file": os.path.join(ask_dir, "answer.json"),
           "t_kst": dt.datetime.fromtimestamp(now(), dt.timezone(dt.timedelta(hours=9))).isoformat(),
           "action": "HUMAN_REQUIRED"}
    with open(os.path.join(ask_dir, "pending.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return rec


def wait_answer(ask_dir, wait_sec=600, stop=lambda: False, sleep=time.sleep, now=time.time, console=None, question=""):
    if console:
        d = digits(console(question))
        if d:
            return d
    p = os.path.join(ask_dir, "answer.json")
    t0 = now()
    while now() - t0 < wait_sec:
        if stop():
            return ""
        if os.path.exists(p):
            try:
                d = digits(json.load(open(p, encoding="utf-8")).get("digits"))
            except Exception:  # noqa: BLE001
                d = ""
            os.replace(p, p + ".used_%d" % int(now()))
            if d:
                return d
        sleep(5)
    return ""


def solve_number(crop, type_digits, verify, ai_read=None, memory=None, ask_dir="captcha_ask",
                 log=lambda *a, **k: None, wait_sec=600, stop=lambda: False, sleep=time.sleep, now=time.time,
                 console=None):
    """돌려줌 'mem_ok' | 'ai_ok' | 'human_ok' | 'fail'. AI·기억은 각각 한 번만 입력."""
    # ① 기억(조각그림 먼저)
    if memory is not None:
        mg = memory.get(crop)
        if mg:
            type_digits(mg)
            if verify():
                log("captcha_num", ok=True, by="memory")
                return "mem_ok"
            log("captcha_mem_wrong")
    # ② AI 한 번
    guess = ""
    if ai_read:
        try:
            guess = digits(ai_read(crop))
        except Exception as e:  # noqa: BLE001
            log("captcha_ai_err", err=type(e).__name__)
        if not 4 <= len(guess) <= 8:
            guess = ""
    if guess:
        type_digits(guess)
        if verify():
            if memory is not None:
                memory.put(crop, guess)
            log("captcha_num", ok=True, by="ai")
            return "ai_ok"
        why = "ai_wrong"
    else:
        why = "ai_unread"
    # ③ 멈추고 사람에게 (그림 + 'xxxxxx가 맞나요?')
    rec = prepare_human(ask_dir, crop, guess, now)
    log("captcha_ask", why=why, image=rec["image"], guess=guess)
    d = wait_answer(ask_dir, wait_sec, stop, sleep, now, console, rec["question"])
    if not d:
        log("captcha_ask_timeout", sec=wait_sec)
        return "fail"
    type_digits(d)
    ok = bool(verify())
    if ok and memory is not None:
        memory.put(crop, d)
    log("captcha_num", ok=ok, by="human")
    return "human_ok" if ok else "fail"
