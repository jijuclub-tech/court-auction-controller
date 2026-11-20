# -*- coding: utf-8 -*-
"""설정. config.json(수집기 폴더) 이 있으면 덮어쓴다. 비밀값은 설정 파일에 넣을 수 없다(넣으면 거부)."""
from __future__ import annotations

import copy
import json
import os

from .secrets_store import NAMES, SECRET_WORDS

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "machine": "",                      # 예: jijuclub5 (기록용)
    "collector_id": "",                 # 필수. 예: nonmetro-01(2호 비수도권), metro-01(1호 수도권). 비면 시작 거부
    "region": "",                       # metro | nonmetro (법원 범위 확인용). 비면 collector_id 로 추정
    "kb_server": {
        "url": "",                      # 관제기(사무실 서버) 주소. 필수(v1.6): 비었거나 접속 실패면 작업을 시작하지 않음(STOP). https 만(allow_http 예외)
        "allow_http": False,            # 사무실 내부망 시험 때만 True. 그때도 host 를 반드시 적어야 함
        "host": "",                     # 허용할 서버 호스트 하나(예: kb.office.local). 리다이렉트는 따라가지 않음
        "timeout_sec": 8,
        "verify_tls": True,             # https 인증서 검사(allow_http=True 면 무시)
    },
    "court_account_window": [],         # 이 수집기가 법원 계정을 써도 되는 시간대(KST). 예: [["00:00","11:00"]].
    "court_account_lock_min": 180,      # 서버 잠금 못 쓸 때 로컬 잠금 유효시간(분)
    "kb_dir": os.path.join(HERE, "collector_kb"),
    "state_dir": os.path.join(HERE, "state"),
    "log_dir": os.path.join(HERE, "logs"),
    "stop_dir": HERE,
    "storage": {
        "nas_root": "X:\\BIDGUARD\\court",
        "nas_subst": {"drive": "X:", "target": ""},  # 2호: X: 안 보이면 subst X: <target> (target 비면 안 함)
        "local_root": "D:\\BIDGUARD\\court",
        "fallback_root": "C:\\BIDGUARD\\court",
    },
    "pacing": {
        "item_gap_sec": [60, 120],
        "max_items_per_hour": 20,
        "max_items_per_run": 40,
        "work_min_before_break": [50, 55],
        "break_min": [5, 10],
        "rate_rest_min": 120,
        "after_429_gap_mult": 2.0,
        "max_429_per_day": 1,          # 1번은 쉬고 다시, 같은 날 2번째면 그날 끝
        "no_save_dialog_suspect_in_a_row": 2,
        "suspect_rest_min": 10,
        "gap_mult_max": 4.0,            # 429 간격 배수 상한(무한 누적 막기)
    },
    "pico": {"port": "AUTO", "fallback": "COM8", "min_build": "1.0.7"},
    "ai": {"max_rounds": 2, "order": ["deepseek", "gemini"],
           "deepseek_url": "https://api.deepseek.com/chat/completions", "deepseek_model": "deepseek-chat",
           "gemini_model": "gemini-2.0-flash",
           "local_llm_url": "", "local_llm_model": "qwen2.5:3b"},   # v1.6: ollama(무료·선택). 이 PC 주소만(예 http://127.0.0.1:11434)
    # v1.6(t850u·t851u): 자료 암호화 키. 지금은 시험 키 'test'. 2026-12-01 00:00 KST 뒤에도 'test'면 멈춤.
    # 갱신: key_source 를 "dpapi" 로 바꾸고 keys 에서 DATA_KEY 입력(DPAPI 저장). 설정 파일에 진짜 키 금지('test' 외 값은 거부).
    "crypto": {"key_source": "config", "key": "test"},  # KEY-UPGRADE-AT-2026-12-01T00:00+09:00
    "routing": {"promote_after": 2, "demote_after": 3},          # v1.6(t845u): 판단 담당표 승격·강등 문턱
    "time_budget": {"deficit_sec": 20.0, "slack_factor": 0.6},   # v1.6: 누적 판단 지연이 넘으면 여유 대기만 줄임
    "adaptive_wait": {"min_samples": 5, "min_gap_sec": 0.3, "court_min_gap_sec": 1.0},
    "power": {"battery_min_pct": 20, "warn_unplugged": True},
    "captcha": {"human_wait_sec": 600, "number_auto": True},   # number_auto: 숫자 그림 캡차만 예외(t761u, 대표 직접 승인). false 면 사람에게.
    "ntp_check": True,   # NTP 확인 켜고 끄기(기본 켜짐). 끄면 멈춤 진단에서 NTP 건너뜀.
    "ntp": {"regions": [], "timeout_sec": 2.0, "hiccup_sec": 30.0,   # 공개 NTP 지역 묶음은 코드 기본(sntp.py). regions 로 '늘리기'만.
            "cycle_min": 10},                                       # 평소 10분마다 1지역 순환(멈춤 의심 때 즉시 한 바퀴)
    "stall": {"net_window_sec": 5.0, "net_min_delta_bytes": 20000, "site_rest_min": 30, "net_down_wait_min": 120},
    "court_home_url": "https://www.courtauction.go.kr/",
    # v1.5: 조각 그림 다중 크기(맥락 그림). every: 캡처 n번째마다 그 크기 1장. fail: 실패 횟수 문턱마다 그 크기 1장.
    "snippet_scales": {"every": {"2": 10, "4": 100, "8": 300}, "fail": {"2": 2, "4": 5, "8": 10}, "max_per_scale": 3},
    "site_alive_retry_sec": 300,        # v1.5: HEAD 실패 뒤 다음 허용까지(초)
    # v1.6(t844u): 사이트 용량 등급(관제기 매니페스트가 우선). 이름 일부 → 등급. 관제기가 관리 안 하는 사이트는 최대 100MB·기본 C
    "site_grades": {"courtauction": "A"},
    "refpack": {"dup_dist": 6, "protect_top_pct": 20, "gallery_max_per_step": 3},
}

# W3: 참/거짓 설정은 모두 같은 규칙(_truthy)으로 읽는다. 'false'/'0'/'no'/'off'/'n'/'' → 끔.
BOOL_KEYS = (("ntp_check",), ("captcha", "number_auto"),
             ("kb_server", "allow_http"), ("kb_server", "verify_tls"), ("power", "warn_unplugged"))


# 설정값을 안전 범위로 묶는다. 사이트 JSON·서버가 느슨하게 줘도 이 밖으로는 못 감 (검수 H1).
# (키): (하한, 상한, 더 안전한 쪽=크다|작다). pacing 값은 '느리게=안전'.
_BOUNDS_PAIR = {
    "item_gap_sec": (30, 600),
    "work_min_before_break": (5, 90),
    "break_min": (5, 30),
}
_BOUNDS_NUM = {
    "max_items_per_hour": (1, 20),
    "max_items_per_run": (1, 40),
    "rate_rest_min": (120, 100000),
    "after_429_gap_mult": (2.0, 4.0),
    "max_429_per_day": (0, 1),
    "no_save_dialog_suspect_in_a_row": (1, 5),
    "suspect_rest_min": (1, 120),
    "gap_mult_max": (1.0, 4.0),
}


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, v))
    except TypeError:
        return lo


def clamp_pacing(p):
    """pacing 값을 안전 범위로. 0=상한없음 같은 구멍을 막는다."""
    out = dict(p)
    for k, (lo, hi) in _BOUNDS_NUM.items():
        if k in out:
            out[k] = _clamp(out[k], lo, hi)
    for k, (lo, hi) in _BOUNDS_PAIR.items():
        v = out.get(k)
        if isinstance(v, (list, tuple)) and len(v) == 2:
            a, b = _clamp(min(v), lo, hi), _clamp(max(v), lo, hi)
            out[k] = [a, b]
        else:
            out[k] = [lo, lo]
    return out


def merge_site_pacing(base, site):
    """사이트 JSON 의 pacing 은 '더 느리게(안전)' 하는 쪽으로만 반영한다 (검수 H1)."""
    base = clamp_pacing(base)
    if not site:
        return base
    site = clamp_pacing(dict(base, **site))
    out = dict(base)
    out["item_gap_sec"] = [max(base["item_gap_sec"][0], site["item_gap_sec"][0]),
                           max(base["item_gap_sec"][1], site["item_gap_sec"][1])]
    out["break_min"] = [max(base["break_min"][0], site["break_min"][0]),
                        max(base["break_min"][1], site["break_min"][1])]
    out["max_items_per_hour"] = min(base["max_items_per_hour"], site["max_items_per_hour"])
    out["max_items_per_run"] = min(base["max_items_per_run"], site["max_items_per_run"])
    out["rate_rest_min"] = max(base["rate_rest_min"], site["rate_rest_min"])
    out["max_429_per_day"] = min(base["max_429_per_day"], site["max_429_per_day"])
    return out


def _merge(a, b):
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(a.get(k), dict):
            _merge(a[k], v)
        else:
            a[k] = v
    return a


def _secret_like(d, path=""):
    bad = []
    if isinstance(d, dict):
        for k, v in d.items():
            kl = str(k).lower()
            if path == "crypto." and kl in ("key", "key_source", "key_note"):
                if kl == "key" and str(v) != "test":  # KEY-UPGRADE-AT-2026-12-01T00:00+09:00 (설정엔 시험 키만, 새 키는 DPAPI)
                    bad.append(path + str(k))
                continue
            if k in NAMES or (any(w in kl for w in SECRET_WORDS) and kl not in ("verify_tls",)):
                bad.append(path + str(k))
            bad += _secret_like(v, path + str(k) + ".")
    return bad


class ConfigError(ValueError):
    pass


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = path or os.path.join(HERE, "config.json")
    if os.path.exists(path):
        user = json.load(open(path, encoding="utf-8"))
        bad = _secret_like(user)
        if bad:
            raise ConfigError("config.json 에 비밀값 칸이 있어요(지우세요): " + ", ".join(bad))
        _merge(cfg, user)
    cfg["pacing"] = clamp_pacing(cfg["pacing"])
    # R8: number_auto 는 엄격 해석. 'false'/'0'/'no'/'off'/0/None → 끔. 기본값(미설정)은 True 유지(t761u).
    normalize_bools(cfg)
    # R5: NTP 서버당 제한시간 0.5~3초로 조임
    nt = cfg.setdefault("ntp", {})
    try:
        nt["timeout_sec"] = max(0.5, min(3.0, float(nt.get("timeout_sec", 2.0))))
    except (TypeError, ValueError):
        nt["timeout_sec"] = 2.0
    return cfg


def normalize_bools(cfg):
    """BOOL_KEYS 를 모두 _truthy 로 바꾼다(없으면 기본값 유지). 바꾼 cfg 를 돌려줌."""
    for path in BOOL_KEYS:
        d, dd = cfg, DEFAULTS
        for k in path[:-1]:
            d = d.setdefault(k, {})
            dd = dd.get(k, {})
        d[path[-1]] = _truthy(d.get(path[-1], dd.get(path[-1], False)))
    return cfg


def _truthy(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    return str(v).strip().lower() not in ("false", "0", "no", "off", "n", "")
