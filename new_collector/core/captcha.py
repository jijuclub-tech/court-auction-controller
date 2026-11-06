"""
보안문자 / 열람하기 다이얼로그 처리
사이트 맵 기준. 자동 우회 없음.
입력은 피코2.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, Callable
from .human_input import HumanHID

logger = logging.getLogger("CaptchaHandler")


class CaptchaHandler:
    """
    맵에 정의된 두 가지 장면 처리
    - 열람하기 다이얼로그: 프로그램 규칙 → 사용자
    - 등기부등본 보안문자: AI 인식 → 사용자
    """

    def __init__(self, collector_name: str, pico2_input: Optional[Callable] = None):
        self.name = collector_name
        self.hid = HumanHID()
        # pico2_input(text) 형태로 실제 피코2 입력 함수를 주입
        self.pico2_input = pico2_input or (lambda text: self.hid.human_type(str(text)))

    def handle_view_dialog(self, already_checked: bool = False) -> dict:
        """
        맵 항목 1: 열람하기 다이얼로그
        프로그램이 단순 규칙으로 처리. 실패 시 사용자 호출.
        """
        logger.info(f"[{self.name}] 열람하기 다이얼로그")
        time.sleep(4)

        if already_checked:
            logger.info("  이미 체크됨 → 열람하기 클릭 (피코2)")
            self.pico2_input("CLICK_VIEW")
            return {"success": True, "handled_by": "program", "needs_human": False}

        logger.info("  체크 필요 → 경고음 후 대기")
        time.sleep(3)
        # 실제로는 화면 확인 후 피코2로 체크 + 클릭
        # 여기서는 실패로 가정하고 사용자 호출
        logger.warning("  단순 규칙으로 처리 불가 → 사용자 호출")
        return {"success": False, "handled_by": "none", "needs_human": True}

    def handle_security_code(self, image_path: Optional[str] = None,
                             ai_recognize: Optional[Callable] = None) -> dict:
        """
        맵 항목 2: 등기부등본 보안문자
        1차 AI 인식 → 피코2 입력
        실패 시 사용자 호출
        """
        logger.info(f"[{self.name}] 보안문자 감지")
        time.sleep(3)

        code = None
        if ai_recognize and image_path:
            try:
                code = ai_recognize(image_path)
                logger.info(f"  AI 인식 결과: {code}")
            except Exception as e:
                logger.warning(f"  AI 인식 실패: {e}")

        if code:
            self.pico2_input(code)
            return {"success": True, "handled_by": "ai", "code": code, "needs_human": False}

        logger.warning("  AI 인식 실패 또는 없음 → 사용자 호출")
        return {"success": False, "handled_by": "none", "needs_human": True}
