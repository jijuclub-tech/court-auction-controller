"""
사람처럼 보이는 HID 입력 (피코2 전용)

원칙:
- Playwright / Selenium 사용 금지
- 모든 마우스·키보드 동작은 피코2 HID로 수행
- 속도·경로·딜레이에 ±50% 이상 변동을 주어 봇 패턴 제거

강화 사항:
- 베지어 곡선 기반 비선형 마우스 이동 (가속 → 감속)
- 클릭 전 미세 정지·오버슈트·보정
- 타이핑 시 글자별 가변 딜레이 + 가끔 실수·백스페이스
- 전체 동작에 반응 속도 변동 (0.5x ~ 1.8x)
"""
from __future__ import annotations

import random
import time
import math
import logging
from typing import Optional, Callable, Tuple, List

logger = logging.getLogger("HumanInput")


class HumanHID:
    """
    피코2를 통한 사람처럼 보이는 입력
    실제 환경에서는 pico2_send 콜백이 시리얼/USB로 명령을 전송한다고 가정
    """

    def __init__(self, pico2_send: Optional[Callable] = None, base_speed: float = 1.0):
        self.pico2_send = pico2_send or self._simulate
        self.base_speed = base_speed
        self.current_pos = (0, 0)  # 가상 좌표 (실제는 화면 기준)

    def _simulate(self, cmd: str, **kwargs):
        logger.info(f"[Pico2 HID] {cmd} {kwargs}")

    def _speed_factor(self) -> float:
        """반응 속도 ±50%~80% 변동 (0.5x ~ 1.8x)"""
        return self.base_speed * random.uniform(0.5, 1.8)

    def _bezier_points(self, start: Tuple[int, int], end: Tuple[int, int],
                       steps: int = 20) -> List[Tuple[int, int]]:
        """3차 베지어 곡선으로 자연스러운 경로 생성"""
        # 제어점: 중간에서 약간 벗어난 위치 (사람 손떨림·곡선)
        mid_x = (start[0] + end[0]) / 2 + random.randint(-40, 40)
        mid_y = (start[1] + end[1]) / 2 + random.randint(-40, 40)
        cp1 = (mid_x + random.randint(-30, 30), mid_y + random.randint(-30, 30))
        cp2 = (mid_x + random.randint(-30, 30), mid_y + random.randint(-30, 30))

        points = []
        for i in range(steps + 1):
            t = i / steps
            # 가속 후 감속 (ease-in-out)
            t = t * t * (3 - 2 * t)
            x = (1-t)**3 * start[0] + 3*(1-t)**2*t * cp1[0] + 3*(1-t)*t**2 * cp2[0] + t**3 * end[0]
            y = (1-t)**3 * start[1] + 3*(1-t)**2*t * cp1[1] + 3*(1-t)*t**2 * cp2[1] + t**3 * end[1]
            points.append((int(x), int(y)))
        return points

    def move_to(self, target_x: int, target_y: int, steps: Optional[int] = None):
        """비선형 곡선으로 마우스 이동 (가속·감속)"""
        speed = self._speed_factor()
        if steps is None:
            dist = math.hypot(target_x - self.current_pos[0], target_y - self.current_pos[1])
            steps = max(8, min(40, int(dist / 15)))

        points = self._bezier_points(self.current_pos, (target_x, target_y), steps)

        for px, py in points:
            self.pico2_send("MOVE", x=px, y=py)
            # 속도에 따라 딜레이 변동
            delay = random.uniform(0.008, 0.025) / speed
            time.sleep(delay)

        self.current_pos = (target_x, target_y)

        # 도착 후 미세 정지 (사람처럼)
        time.sleep(random.uniform(0.05, 0.18) / speed)

    def click(self, x: Optional[int] = None, y: Optional[int] = None, button: str = "left"):
        """클릭 전 미세 이동·정지 후 클릭"""
        if x is not None and y is not None:
            # 정확한 좌표보다 약간 벗어난 곳 클릭 (사람)
            jitter_x = x + random.randint(-3, 4)
            jitter_y = y + random.randint(-3, 4)
            self.move_to(jitter_x, jitter_y)

        speed = self._speed_factor()
        # 클릭 전 짧은 정지
        time.sleep(random.uniform(0.04, 0.12) / speed)
        self.pico2_send("CLICK", button=button, x=self.current_pos[0], y=self.current_pos[1])
        # 클릭 후 미세 이동
        time.sleep(random.uniform(0.06, 0.15) / speed)

    def human_type(self, text: str, error_rate: float = 0.03):
        """
        글자마다 가변 딜레이 + 가끔 오타·백스페이스
        ±50% 이상 속도 변동
        """
        speed = self._speed_factor()
        for char in text:
            # 가끔 실수
            if random.random() < error_rate:
                wrong = random.choice("abcdefghijklmnopqrstuvwxyz0123456789")
                self.pico2_send("KEY", key=wrong)
                time.sleep(random.uniform(0.05, 0.12) / speed)
                self.pico2_send("KEY", key="BACKSPACE")
                time.sleep(random.uniform(0.08, 0.16) / speed)

            self.pico2_send("KEY", key=char)
            # 기본 딜레이 + ±50% 변동
            base = random.uniform(0.07, 0.22)
            delay = base * random.uniform(0.5, 1.5) / speed
            # 공백·특수문자 후 약간 더 길게
            if char in " \t.,!@#":
                delay *= random.uniform(1.2, 1.8)
            time.sleep(delay)

        # 입력 완료 후 짧은 정지
        time.sleep(random.uniform(0.1, 0.3) / speed)

    def scroll(self, amount: int):
        """스크롤도 가변 속도로"""
        speed = self._speed_factor()
        steps = abs(amount) // 3 + 1
        step = amount // steps if steps else amount
        for _ in range(steps):
            self.pico2_send("SCROLL", amount=step)
            time.sleep(random.uniform(0.03, 0.09) / speed)

    def pause(self, min_sec: float = 0.3, max_sec: float = 1.2):
        """사람처럼 가끔 생각하는 정지"""
        time.sleep(random.uniform(min_sec, max_sec) / self._speed_factor())


# 편의 함수 (기존 코드와 호환용)
def human_like_move(hid: HumanHID, target_x: int, target_y: int):
    hid.move_to(target_x, target_y)


def human_type(hid: HumanHID, text: str):
    hid.human_type(text)
