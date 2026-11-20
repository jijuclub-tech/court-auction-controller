# -*- coding: utf-8 -*-
"""[v1.5] C8 스크롤 법원 고르기(2호 patch_C8 과 같은 규칙). 법원 드롭다운을 열고, 법원(예: 대전지방법원)을
휠로 한 칸씩 내리며 OCR 로 정확히 찾아 누른다. 안 되면 select 초점에서 아래키로 선택값을 바꿔 확인.
그래도 없으면 COURT_NOT_FOUND. 입력은 피코2만(KEY/WHEEL/CLICK). JS·Selenium·URL 입력 금지.

핵심 규칙
  * 정확한 이름만 일치: '대전지방법원' 을 찾을 때 '대전지방법원 천안지원' 같은 부분일치는 고르지 않는다.
  * v1.6: 비교는 '한 줄 전체'로만(단어+줄 OCR 함정 방지). 칸 확인·아래키 ENTER 도 같음 비교. 수도권 대상은 시작부터 거부.
  * 수도권 법원은 절대 클릭하지 않는다(정확 일치한 대상만 클릭하므로 구조적으로 안전).
  * 목록 끝(두 번 연속 같은 화면)에 닿으면 휠을 멈춘다(무한 스크롤 방지).
  * 매 단계 page_guard 를 부른다(STOP 파일·오류 화면 확인은 호출자가 넘김).

이 파일은 피코·OCR 없이 시험할 수 있게 순수 함수로 만들었다(주입식 grab/lines/click/wheel/move/key).
"""
from __future__ import annotations

import re

# 수도권(서울·경기·인천) 법원 — 혹시라도 후보로 잡혀도 클릭 금지(겹판정 방어용 목록)
METRO_HINT = ("서울", "인천", "수원", "성남", "안양", "안산", "부천", "고양", "의정부", "경기")


def norm(s):
    return re.sub(r"\s+", "", s or "")


def is_metro(name):
    n = norm(name)
    return any(h in n for h in METRO_HINT)


ROW_TOL = 14   # 같은 줄로 보는 y 차이(px)


def row_exact(lines, court, xmin=None, tol=ROW_TOL):
    """v1.6(검수 E1·P2): '한 줄 전체'가 법원 이름과 똑같을 때만 그 자리를 돌려준다(공백 무시).
    OCR 이 단어와 줄을 함께 주거나 한 줄을 조각으로 나눠도, 같은 줄(y 차이 tol 이내)에 대상 이름의 일부가
    아닌 다른 글자(예 '천안지원')가 있으면 거부한다. 부분일치는 쓰지 않는다."""
    cn = norm(court)
    if not cn:
        return None
    rows = [(t, x, y) for t, x, y in lines if norm(t) and (xmin is None or x > xmin)]
    for t, x, y in rows:
        if norm(t) != cn:
            continue
        same_row = [norm(t2) for t2, _x2, y2 in rows if abs(y2 - y) <= tol]
        if all(r2 == cn or (r2 in cn and len(r2) < len(cn)) for r2 in same_row):
            return (x, y, t)
    return None


def find_exact(lines, court):
    """한 줄 전체가 법원 이름과 같은 자리만(같은 줄에 지원 이름 등이 붙으면 거부)."""
    return row_exact(lines, court)


def _field_is(lines, court, xmin):
    """칸 값 줄 전체 == 대상 (대상보다 긴 값이 칸에 보이면 아님)."""
    cn = norm(court)
    longer = [t for t, x, _y in lines if x > xmin and cn in norm(t) and norm(t) != cn]
    return bool(row_exact(lines, court, xmin=xmin)) and not longer


def find_field(lines, xmin=0, y_pref=380):
    """법원 '칸'(현재 선택값이 보이는 자리) 후보. 지방법원/지원으로 끝나고 오른쪽(x>xmin)."""
    cands = [(t, x, y) for t, x, y in lines
             if (("지방법원" in norm(t)) or norm(t).endswith("지원")) and x > xmin]
    cands.sort(key=lambda r: abs(r[2] - y_pref))
    return (cands[0][1], cands[0][2], cands[0][0]) if cands else None


def _confirm(court, grab, lines, field_box, sleep, log, xmin=0):
    sleep(1.2)
    fl = lines(grab(), field_box)
    # v1.6(검수 E1): 칸 값 줄 전체가 대상과 '같음'일 때만 OK. 포함 검사는 쓰지 않는다.
    if _field_is(fl, court, xmin):
        log(ev="court_ok", court=court)
        return {"status": "OK", "court": court}
    log(ev="select_not_confirmed", court=court, seen=[t for t, _x, _y in fl][:6])
    return {"status": "SELECT_NOT_CONFIRMED", "court": court}


def pick_court(court, grab, lines, click, wheel, move, key, *, page_guard=None,
               log=lambda **k: None, field_box=(500, 250, 1600, 550),
               list_box=(500, 300, 1300, 1050), max_wheel=16, max_key=40,
               sleep=lambda s: None, refuse_metro=True, field_xmin=0, avoid=()):
    """돌려줌 {'status': 'OK'|'COURT_NOT_FOUND'|'SELECT_NOT_CONFIRMED'|'COURT_FIELD_NOT_FOUND'}."""
    def guard(where):
        if page_guard:
            page_guard(where)

    court_n = norm(court)

    def refused(name):
        return (refuse_metro and is_metro(name)) or any(norm(a) and norm(a) in norm(name) for a in avoid)

    if refused(court):                       # v1.6(검수 E2·P3): 대상 자체가 수도권/피할 이름이면 입력 없이 거부
        log(ev="refuse_metro", name=court)
        return {"status": "COURT_NOT_FOUND", "court": court}

    # 1) 법원 칸을 찾아 클릭해 드롭다운을 연다.
    guard("field")
    fl = lines(grab(), field_box)
    fld = find_field(fl, xmin=field_xmin, y_pref=(field_box[1] + field_box[3]) // 2)
    if not fld:
        log(ev="court_field_not_found")
        return {"status": "COURT_FIELD_NOT_FOUND"}
    fx, fy = int(fld[0]), int(fld[1])
    click(fx, fy, "court dropdown")
    sleep(1.3)

    # 목록 가운데에 마우스를 둔다(휠은 목록 위에서 돌려야 스크롤된다).
    cx = (list_box[0] + list_box[2]) // 2
    cy = (list_box[1] + list_box[3]) // 2
    move(cx, cy)

    # 2) 휠을 한 칸씩 내리며 매번 OCR 로 정확한 이름 찾기.
    prev = None
    for i in range(max_wheel):
        guard("wheel_%d" % i)
        ln = lines(grab(), list_box)
        hit = find_exact(ln, court)
        if hit:
            if refused(hit[2]):
                log(ev="refuse_metro", name=hit[2])
                return {"status": "COURT_NOT_FOUND", "court": court}
            log(ev="found_by_wheel", tries=i, at=[int(hit[0]), int(hit[1])])
            click(int(hit[0]), int(hit[1]), court)
            return _confirm(court, grab, lines, field_box, sleep, log, field_xmin)
        cur = "|".join(norm(t) for t, _x, _y in ln)
        if cur and cur == prev:             # 두 번 연속 같은 화면 → 목록 끝
            log(ev="list_end", tries=i)
            break
        prev = cur
        wheel(cx, cy, -1, "court list scroll")   # 한 칸씩만
        sleep(0.8)

    # 3) 휠로 못 찾으면: select 에 초점을 두고 아래키로 선택값을 바꾸며 OCR 확인.
    guard("key_focus")
    click(fx, fy, "court dropdown focus")
    sleep(0.6)
    prevval = None
    same = 0
    for i in range(max_key):
        guard("key_%d" % i)
        fl2 = lines(grab(), field_box)
        selnow = find_field(fl2, xmin=field_xmin, y_pref=(field_box[1] + field_box[3]) // 2)
        val = norm(selnow[2]) if selnow else ""
        # v1.6(검수 E1·E2): 칸 값 줄 전체가 대상과 같고 수도권/피할 이름이 아닐 때만 ENTER
        if _field_is(fl2, court, field_xmin) and not refused(val):
            key("ENTER", "court enter")
            log(ev="found_by_key", tries=i)
            return _confirm(court, grab, lines, field_box, sleep, log, field_xmin)
        if val and val == prevval:
            same += 1
            if same >= 2:                   # 선택값이 두 번 연속 같음 → 목록 끝
                log(ev="key_end", tries=i)
                break
        else:
            same = 0
        prevval = val
        key("DOWN", "court down")
        sleep(0.35)

    # 4) 그래도 없으면 멈춤.
    log(ev="court_not_found", court=court)
    return {"status": "COURT_NOT_FOUND", "court": court}
