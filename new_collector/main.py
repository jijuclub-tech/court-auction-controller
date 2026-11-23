"""
법원경매정보 수집기 - 메인
고정된 시험용 사건번호 자동 다운로드를 없애고, run.bat과 같은 수집기 화면을 연다.
- 관제형: 관제기 지시서의 사건번호대로 수행 (사건번호 직접 입력 없음)
- 보급형: Collect 탭에 수집할 내용을 직접 입력
"""
import json
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("Main")

CONFIG_PATH = Path("config.json")


def load_config():
    if not CONFIG_PATH.exists():
        logger.error("config.json이 없습니다. install.bat을 먼저 실행하세요.")
        return None
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def main():
    config = load_config()
    if not config:
        return
    logger.info(f"=== 수집기 시작: {config.get('name', 'unknown')} ({config.get('type', 'supply')}) ===")
    from ui.app import main as ui_main
    ui_main()


if __name__ == "__main__":
    main()
