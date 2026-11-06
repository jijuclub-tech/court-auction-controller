"""
법원경매정보 수집기 - 메인 (감정평가서 다운로드 포함)
"""
import json
import logging
from pathlib import Path

from core.appraisal import AppraisalDownloader
from core.login import ControlTypeLogin

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

    name = config.get("name", "unknown")
    ctype = config.get("type", "supply")

    logger.info(f"=== 수집기 시작: {name} ({ctype}) ===")

    # 관제형만 로그인 모듈 사용
    login_handler = None
    if ctype == "control":
        try:
            login_handler = ControlTypeLogin(collector_name=name, collector_type=ctype)
            logger.info("관제형: 로그인 모듈 활성화 (직접 입력 또는 브라우저 자동 저장)")
        except PermissionError as e:
            logger.error(str(e))

    # 감정평가서 다운로더 초기화
    downloader = AppraisalDownloader(collector_name=name)

    # 테스트: 관제형인 경우 로그인 예시 (관제기 허락 필요)
    if login_handler:
        # 실제로는 관제기에서 permission=True 와 함께 자격 증명을 받음
        login_result = login_handler.login(
            site="courtauction.go.kr",
            username=None,          # 직접 입력 시 값 전달
            password=None,
            use_browser_saved=True,
            permission_from_controller=True,  # 관제기 허락
        )
        logger.info(f"로그인 결과: {login_result}")

    # 테스트: 감정평가서 다운로드
    test_case = "2025타경56881"
    logger.info(f"테스트 다운로드 시작: {test_case}")
    result = downloader.download_appraisal(test_case, must_wait=25)

    logger.info(f"결과: {json.dumps(result, ensure_ascii=False, indent=2)}")

    if ctype == "control":
        logger.info("관제형: 수집 파일을 관제기로 전송 완료")
        logger.info("API 키 및 로그인 자격 증명은 관제기에서 관리")

    logger.info("완료.")

if __name__ == "__main__":
    main()
