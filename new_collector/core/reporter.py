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
