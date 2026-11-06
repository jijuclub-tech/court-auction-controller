"""
수집기 → 관제기 보고 모듈
파일 기반 보고 (로컬/데모용). 실제 운영 시 HTTP 등으로 교체 가능.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger("Reporter")


class ControllerReporter:
    def __init__(self, collector_name: str, report_dir: Optional[str] = None):
        self.name = collector_name
        self.report_dir = Path(report_dir) if report_dir else Path("reports")
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def report(self, case_no: str, task_type: str, success: bool,
               file_path: Optional[str] = None, error: str = "",
               extra: Optional[Dict[str, Any]] = None) -> Path:
        """관제기로 결과 보고 (JSON 파일 기록)"""
        record = {
            "collector": self.name,
            "case_no": case_no,
            "task_type": task_type,
            "success": success,
            "file_path": file_path,
            "error": error,
            "reported_at": datetime.now().isoformat(),
            "extra": extra or {},
        }
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{case_no}_{task_type}.json"
        out = self.report_dir / fname
        out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"[{self.name}] 관제기 보고 저장: {out}")
        return out

    # ── HTTP 보고 (관제기 POST /api/collector/report) ──────────────
    # 위 report()의 로컬 파일 기록은 그대로 두고, 관제기 주소가 있으면 결과를 관제기에도 보낸다.
    # 보내지 못한 보고는 reports/unsent/에 보관했다가 다음 연결 때 다시 보낸다 (보고 유실 방지).

    @property
    def unsent_dir(self) -> Path:
        return self.report_dir / "unsent"

    def send_to_controller(self, controller_url: str, case_no: str, task_type: str, success: bool,
                           file_path: Optional[str] = None, error: str = "",
                           instruction_id: str = "", needs_human: bool = False,
                           extra: Optional[Dict[str, Any]] = None) -> bool:
        payload = {
            "name": self.name,
            "case_no": case_no,
            "task_type": task_type,          # 관제기 작업 유형 이름 (예: 감정평가서, 등기부등본)
            "success": success,
            "file_path": file_path,
            "error": error,
            "instruction_id": instruction_id,
            "needs_human": needs_human,
            "reported_at": datetime.now().isoformat(),
            "extra": extra or {},
        }
        if self._post(controller_url, payload):
            return True
        self.unsent_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{case_no}.json"
        (self.unsent_dir / fname).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.warning(f"[{self.name}] 관제기 보고 전송 실패 → 보관 후 재전송 대기: {fname}")
        return False

    def flush_unsent(self, controller_url: str) -> int:
        """보관해 둔 미전송 보고를 다시 보냄. 보낸 건수를 돌려줌"""
        if not controller_url or not self.unsent_dir.exists():
            return 0
        sent = 0
        for p in sorted(self.unsent_dir.glob("*.json")):
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not self._post(controller_url, payload):
                break  # 아직 연결이 안 되면 다음 기회에
            p.unlink()
            sent += 1
        if sent:
            logger.info(f"[{self.name}] 미전송 보고 {sent}건 재전송 완료")
        return sent

    def _post(self, controller_url: str, payload: dict) -> bool:
        if not controller_url:
            return False
        try:
            import requests
            r = requests.post(f"{controller_url.rstrip('/')}/api/collector/report", json=payload, timeout=10)
            if r.status_code == 200 and r.json().get("ok"):
                logger.info(f"[{self.name}] 관제기 보고 전송: {payload.get('case_no')} → "
                            f"{r.json().get('result', {}).get('task_status', '')}")
                return True
            logger.warning(f"[{self.name}] 관제기 보고 거부: HTTP {r.status_code} {r.text[:200]}")
            if r.status_code == 400:
                return True  # 형식 오류는 다시 보내도 같음 → 보관하지 않음 (로그로 확인)
        except Exception as e:
            logger.warning(f"[{self.name}] 관제기 보고 연결 실패: {e}")
        return False
