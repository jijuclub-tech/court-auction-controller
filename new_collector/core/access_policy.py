"""
접근 등급에 따른 입력 방식 결정
- 공공기관 (*.go.kr 등): Selenium/Playwright 자동 금지 → 피코2만
- 그 외: 작동 전 사용자 선택 문의 (주의 안내 포함)
- 반복 선택 또는 이전 문제 시 옵션 미제시
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

PUBLIC_DOMAINS = {".go.kr", "courtauction.go.kr"}
SETTINGS_PATH = Path("settings.json")


def is_public_institution(site: str) -> bool:
    site = (site or "").lower().strip()
    if not site:
        return True  # 기본 안전
    if site in PUBLIC_DOMAINS or any(site.endswith(d) for d in PUBLIC_DOMAINS if d.startswith(".")):
        return True
    return False


def load_choice_history() -> dict:
    if not SETTINGS_PATH.exists():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        return data.get("browser_choice", {})
    except Exception:
        return {}


def save_choice(site: str, use_browser: bool, suppressed: bool = False):
    settings = {}
    if SETTINGS_PATH.exists():
        try:
            settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    history = settings.get("browser_choice", {})
    history[site] = {
        "use_browser": use_browser,
        "suppressed": suppressed,
        "count": history.get(site, {}).get("count", 0) + 1,
    }
    settings["browser_choice"] = history
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def should_suppress_option(site: str) -> bool:
    hist = load_choice_history().get(site, {})
    # 이전에 사용했다가 문제 있거나, 여러 번 거부한 경우 등
    return hist.get("suppressed", False) or hist.get("count", 0) >= 3 and not hist.get("use_browser")
