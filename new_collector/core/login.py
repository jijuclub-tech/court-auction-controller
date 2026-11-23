"""
관제형 수집기 전용 로그인 모듈
- 관제기로부터 허락된 행위만 수행
- 아이디/암호를 직접 입력하거나
- 브라우저에 자동 저장된 기록이 있으면 활용
- 보급형 수집기는 사용하지 않음
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any
from .human_input import HumanHID

logger = logging.getLogger("ControlLogin")


CRED_PATH = Path("credentials_local.json")


def save_local_credentials(username: str, password: str):
    """로컬 자격 증명 저장 (관제형 전용, 평문 저장은 개발용)"""
    import json
    data = {"username": username, "password": password}
    CRED_PATH.write_text(json.dumps(data), encoding="utf-8")
    logger.info("로컬 자격 증명 저장")


def load_local_credentials() -> dict:
    import json
    if not CRED_PATH.exists():
        return {}
    try:
        return json.loads(CRED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


class ControlTypeLogin:
    """
    관제형 수집기만 사용하는 로그인 처리
    """

    def __init__(self, collector_name: str, collector_type: str = "supply"):
        self.name = collector_name
        self.ctype = collector_type
        self.hid = HumanHID()  # 피코2 사람처럼 입력
        if self.ctype != "control":
            raise PermissionError("로그인 모듈은 관제형 수집기만 사용할 수 있습니다.")

    def login(
        self,
        site: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        use_browser_saved: bool = True,
        permission_from_controller: bool = False,
    ) -> Dict[str, Any]:
        """
        로그인 수행
        - permission_from_controller: 관제기가 허락한 행위인지 확인
        - username/password가 있으면 직접 입력
        - 없으면 브라우저 자동 저장 기록 활용 시도
        """
        if not permission_from_controller:
            logger.warning(f"[{self.name}] 관제기 허락 없음. 로그인 거부")
            return {"success": False, "reason": "no_permission"}

        logger.info(f"[{self.name}] 로그인 시작: {site}")

        # 1. 직접 입력 우선
        if username and password:
            logger.info(f"[{self.name}] 아이디/암호 직접 입력 방식")
            result = self._fill_credentials(username, password)
            return result

        # 2. 브라우저 자동 저장 기록 활용
        if use_browser_saved:
            logger.info(f"[{self.name}] 브라우저 자동 저장 기록 확인")
            saved = self._try_browser_saved_credentials(site)
            if saved.get("found"):
                logger.info(f"[{self.name}] 자동 저장 기록으로 로그인 시도")
                return self._fill_credentials(saved["username"], saved["password"], source="browser_saved")

        logger.warning(f"[{self.name}] 로그인 수단 없음")
        return {"success": False, "reason": "no_credentials"}

    def _fill_credentials(self, username: str, password: str, source: str = "direct") -> Dict[str, Any]:
        """아이디/암호 입력 (피코2 HumanHID)"""
        logger.info(f"[{self.name}] 자격 증명 입력 (출처: {source})")
        # 실제로는 화면 좌표를 맵에서 받아 HumanHID로 이동·클릭 후 입력
        # 여기서는 시뮬레이션
        self.hid.pause(0.4, 0.9)
        self.hid.human_type(username)
        self.hid.pause(0.2, 0.5)
        self.hid.human_type(password)
        self.hid.pause(0.3, 0.7)
        # 로그인 버튼 클릭 (좌표는 맵 기반)
        # self.hid.click(login_btn_x, login_btn_y)
        logger.info(f"[{self.name}] 로그인 입력 완료 (HumanHID)")
        return {
            "success": True,
            "source": source,
            "username": username,
            "site": "logged_in",
        }

    def _try_browser_saved_credentials(self, site: str) -> Dict[str, Any]:
        """
        브라우저에 자동 저장된 로그인 기록 확인
        실제 구현 시:
        - Chrome/Edge 프로필의 Login Data 또는
        - 브라우저 저장 기록 또는 OS 자격 증명 관리자 활용
        """
        logger.info(f"[{self.name}] 브라우저 저장 기록 조회: {site}")
        # 시뮬레이션: 저장된 기록이 있다고 가정하지 않음
        # 실제로는 브라우저 프로필 또는 OS 자격 증명 관리자 연동
        return {"found": False}

    def logout(self) -> bool:
        logger.info(f"[{self.name}] 로그아웃")
        return True
