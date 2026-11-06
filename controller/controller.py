"""
법원경매 관제기 (Controller)
- 설치: 우분투 서버
- 연동: bidguard 0단계 메뉴
- 대전·충남 지역 목표 수집 관리
- 감정평가서 우선 + 장시간 대기 강제
- 저장 경로 중앙 관리 (ChatGPT 지정 경로 지원)
- 수집기(보급형/관제형) 중앙 배정 및 지시서 발행
- GG옥션 단일 세션 제약 반영
- 1시간 단위 계획 재수립
"""
from __future__ import annotations

import json
import logging
import random
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4
import hashlib
import secrets
import base64
from cryptography.fernet import Fernet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("Controller")


class TaskType(str, Enum):
    NOTICE = "매각공고"
    BASIC = "기본정보"
    SPEC = "매각물건명세서"
    STATUS = "현황조사서"
    APPRAISAL = "감정평가서"
    MONITOR = "기일모니터링"


class TaskStatus(str, Enum):
    PENDING = "대기"
    ASSIGNED = "배정"
    RUNNING = "실행중"
    SUCCESS = "성공"
    FAILED = "실패"
    RETRY = "재시도"


class CaseStatus(str, Enum):
    NEW = "신건"
    FAILED_BID = "유찰"
    SUCCESS_BID = "낙찰"
    CANCELLED = "취소"
    POSTPONED = "연기"
    ONGOING = "진행중"


@dataclass
class CaseMaster:
    case_no: str
    court: str
    status: CaseStatus = CaseStatus.ONGOING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    has_spec: bool = False
    has_status: bool = False
    has_appraisal: bool = False
    appraisal_retry_count: int = 0
    last_appraisal_attempt: Optional[str] = None
    notes: str = ""


@dataclass
class Task:
    id: str = field(default_factory=lambda: str(uuid4()))
    case_no: str = ""
    task_type: TaskType = TaskType.BASIC
    priority: int = 5
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    scheduled_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    retry_count: int = 0
    max_retry: int = 2
    wait_seconds: int = 5
    error_msg: str = ""


@dataclass
class WorkInstruction:
    instruction_id: str = field(default_factory=lambda: str(uuid4()))
    collector_name: str = ""
    task_type: str = ""
    cases: List[str] = field(default_factory=list)
    menu_steps: List[str] = field(default_factory=list)
    must_wait_seconds: int = 5
    save_path: str = ""
    session_exclusive: bool = True
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    notes: str = ""


class AuctionController:
    """
    중앙 관제기
    - 사건 마스터 + 태스크 관리
    - 저장 경로 지정 (관제기가 처리)
    - 수집기 배정 및 지시서 발행
    - 감정평가서 대기 시간 강제
    """

    TARGET_COURTS = [
        "대전지방법원",
        "대전지방법원 홍성지원",
        "대전지방법원 공주지원",
        "대전지방법원 논산지원",
        "대전지방법원 서산지원",
        "대전지방법원 천안지원",
    ]

    APPRAISAL_WAIT_MIN = 20
    APPRAISAL_WAIT_MAX = 45
    APPRAISAL_DAILY_LIMIT = 40

    def __init__(self, db_path: str = "controller.db", base_save_path: Optional[str] = None):
        self.db_path = Path(db_path)
        self.base_save_path = Path(base_save_path) if base_save_path else Path("/data/auction/collected")
        self.base_save_path.mkdir(parents=True, exist_ok=True)

        self.cases: Dict[str, CaseMaster] = {}
        self.tasks: Dict[str, Task] = {}
        self.collectors: Dict[str, dict] = {}  # name -> info
        self.session_locked_by: Optional[str] = None
        self.appraisal_today_count = 0
        self.last_plan_time: Optional[datetime] = None
        self.pending_instructions: List[WorkInstruction] = []
        self.natural_tasks: List[dict] = []
        # 수집기·자연어 작업·대기 지시서를 재시작 후에도 유지하는 상태 파일 (DB 옆)
        self.state_path = self.db_path.with_name("controller_state.json")
        self.policy: dict = {}
        self._load_policy()

        self._init_db()
        self._load_state()
        logger.info(f"관제기 초기화 완료. 기본 저장 경로: {self.base_save_path}")

    def _load_policy(self):
        """작업 정책 로드 (부하분산·관할·프록시 포함)"""
        path = Path("defaults/work_policy.json")
        if path.exists():
            try:
                self.policy = json.loads(path.read_text(encoding="utf-8"))
                logger.info("작업 정책 로드 완료")
            except Exception as e:
                logger.warning(f"정책 로드 실패: {e}")
                self.policy = {}
        else:
            self.policy = {}

    def get_statistics(self) -> dict:
        """수집기 통계 집계"""
        total_success = sum(c.get("total_completed", 0) for c in self.collectors.values())
        total_error = sum(c.get("total_failed", 0) for c in self.collectors.values())
        denom = total_success + total_error
        return {
            "total_collectors": len(self.collectors),
            "active_nodes": sum(1 for c in self.collectors.values() if not c.get("is_busy", False)),
            "total_success": total_success,
            "total_error": total_error,
            "success_rate": round((total_success / denom * 100), 1) if denom else 0.0,
            "nodes": {
                name: {
                    "type": c.get("type"),
                    "speed_score": c.get("speed_score", 1),
                    "success_rate": round(c.get("success_rate", 0) * 100, 1),
                    "weight": c.get("weight", 1),
                    "jurisdictions": c.get("assigned_jurisdictions", []),
                }
                for name, c in self.collectors.items()
            },
        }

    def set_save_path(self, path: str):
        """관제기가 저장 경로를 지정 (ChatGPT 지정 경로 등)"""
        self.base_save_path = Path(path)
        self.base_save_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"저장 경로 변경: {self.base_save_path}")

    def register_collector(self, name: str, ctype: str = "supply", speed_score: float = 1.0,
                           weight: float = 1.0, jurisdictions: Optional[List[str]] = None,
                           proxy: str = "", memo: str = ""):
        self.collectors[name] = {
            "name": name,
            "type": ctype,
            "speed_score": speed_score,
            "success_rate": 1.0,
            "avg_duration": 30.0,
            "total_completed": 0,
            "total_failed": 0,
            "is_busy": False,
            "registered_at": datetime.now().isoformat(),
            "last_assigned_at": None,
            "weight": weight,
            "assigned_jurisdictions": jurisdictions or [],
            "proxy": proxy,
            "memo": memo,
        }
        logger.info(f"수집기 등록: {name} ({ctype}, weight={weight})")
        self._save_state()

    def update_collector(self, name: str, ctype: Optional[str] = None, memo: Optional[str] = None) -> bool:
        """이미 등록된 수집기의 유형·메모만 갱신 (실적 기록은 유지)"""
        c = self.collectors.get(name)
        if c is None:
            return False
        if ctype:
            c["type"] = ctype
        if memo is not None:
            c["memo"] = memo
        self._save_state()
        logger.info(f"수집기 갱신: {name}")
        return True

    # ── 수집기 로그 ──────────────────────────────────────────
    LOG_DIR = Path("logs")
    # 보급형: 접속 로그 + 기본만
    # 관제형: 모든 행동 기록 → 분석 자료

    def _log_collector_event(self, collector_name: str, event_type: str, detail: dict):
        """수집기 유형에 따라 로그 범위 조절"""
        cinfo = self.collectors.get(collector_name, {})
        ctype = cinfo.get("type", "supply")

        self.LOG_DIR.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y%m%d")
        log_file = self.LOG_DIR / f"{day}_{collector_name}.jsonl"

        record = {
            "ts": datetime.now().isoformat(),
            "collector": collector_name,
            "type": ctype,
            "event": event_type,
            "detail": detail,
        }

        if ctype == "supply":
            # 보급형: 접속·기본 결과만
            if event_type not in ("connect", "disconnect", "result_basic"):
                return
        # 관제형은 모든 이벤트 기록

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        if ctype == "control":
            logger.info(f"[관제형 로그] {collector_name} / {event_type}")

    def get_collector_logs(self, collector_name: str, limit: int = 50) -> list:
        """저장된 로그 조회 (관제형은 상세, 보급형은 기본)"""
        if not self.LOG_DIR.exists():
            return []
        logs = []
        for p in sorted(self.LOG_DIR.glob(f"*_{collector_name}.jsonl"), reverse=True):
            try:
                for line in p.read_text(encoding="utf-8").strip().splitlines()[-limit:]:
                    logs.append(json.loads(line))
            except Exception:
                continue
        return logs[-limit:]

    # ── 관리자 인증 및 API 키 ───────────────────────────────
    CRED_FILE = Path("credentials.json")
    APIKEY_FILE = Path("apikeys.enc")

    def set_admin(self, username: str, password: str):
        """관리자 계정 설정 (비밀번호는 해시 저장)"""
        salt = secrets.token_hex(16)
        pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
        data = {"username": username, "salt": salt, "password_hash": pw_hash}
        self.CRED_FILE.write_text(json.dumps(data), encoding="utf-8")
        logger.info(f"관리자 계정 설정: {username}")

    def verify_admin(self, username: str, password: str) -> bool:
        """관리자 인증"""
        if not self.CRED_FILE.exists():
            return False
        data = json.loads(self.CRED_FILE.read_text(encoding="utf-8"))
        if data.get("username") != username:
            return False
        salt = data["salt"]
        pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()
        ok = secrets.compare_digest(pw_hash, data["password_hash"])
        logger.info(f"관리자 인증 {'성공' if ok else '실패'}: {username}")
        return ok

    def _get_fernet(self) -> Fernet:
        """API 키 암호화용 키 (없으면 생성)"""
        key_file = Path(".apikey_secret")
        if not key_file.exists():
            key = Fernet.generate_key()
            key_file.write_bytes(key)
        else:
            key = key_file.read_bytes()
        return Fernet(key)

    def store_credential(self, name: str, username: str, password: str):
        """아이디/암호 암호화 저장 (관제형 전달용)"""
        f = self._get_fernet()
        store = {}
        cred_file = Path("credentials.enc")
        if cred_file.exists():
            try:
                store = json.loads(f.decrypt(cred_file.read_bytes()).decode())
            except Exception:
                store = {}
        store[name] = {"username": username, "password": password}
        cred_file.write_bytes(f.encrypt(json.dumps(store).encode()))
        logger.info(f"자격 증명 저장: {name}")

    def list_credentials(self) -> List[str]:
        """저장된 자격 증명 이름 목록"""
        cred_file = Path("credentials.enc")
        if not cred_file.exists():
            return []
        try:
            f = self._get_fernet()
            store = json.loads(f.decrypt(cred_file.read_bytes()).decode())
            return list(store.keys())
        except Exception:
            return []

    def list_api_keys(self) -> List[str]:
        """저장된 API 키 이름 목록"""
        if not self.APIKEY_FILE.exists():
            return []
        try:
            f = self._get_fernet()
            store = json.loads(f.decrypt(self.APIKEY_FILE.read_bytes()).decode())
            return list(store.keys())
        except Exception:
            return []

    def add_natural_task(self, text: str, memo: str = "") -> dict:
        """자연어 작업 추가 (나중에 Gemini가 해석)"""
        task_id = str(uuid4())[:8]
        task = {
            "id": task_id,
            "text": text,
            "memo": memo,
            "status": "대기",
            "created": datetime.now().isoformat(),
        }
        if not hasattr(self, "natural_tasks"):
            self.natural_tasks = []
        self.natural_tasks.append(task)
        logger.info(f"자연어 작업 추가: {task_id} - {text[:40]}")
        self._save_state()
        return task

    def store_api_key(self, name: str, api_key: str):
        """API 키 저장 (암호화)"""
        f = self._get_fernet()
        store = {}
        if self.APIKEY_FILE.exists():
            try:
                store = json.loads(f.decrypt(self.APIKEY_FILE.read_bytes()).decode())
            except Exception:
                store = {}
        store[name] = api_key
        self.APIKEY_FILE.write_bytes(f.encrypt(json.dumps(store).encode()))
        logger.info(f"API 키 저장: {name}")

    def get_encrypted_api_key_for_collector(self, collector_name: str, key_name: str) -> Optional[dict]:
        """
        관제형 수집기에만 암호화된 API 키 전달
        보급형은 거부
        """
        cinfo = self.collectors.get(collector_name, {})
        if cinfo.get("type") != "control":
            logger.warning(f"API 키 전달 거부 (보급형): {collector_name}")
            return None

        if not self.APIKEY_FILE.exists():
            return None

        f = self._get_fernet()
        try:
            store = json.loads(f.decrypt(self.APIKEY_FILE.read_bytes()).decode())
        except Exception:
            return None

        raw_key = store.get(key_name)
        if not raw_key:
            return None

        # 수집기용 일회성 암호화 (별도 세션 키)
        session_key = Fernet.generate_key()
        sf = Fernet(session_key)
        encrypted = sf.encrypt(raw_key.encode()).decode()

        payload = {
            "collector": collector_name,
            "key_name": key_name,
            "encrypted_key": encrypted,
            "session_key": session_key.decode(),  # 실제 운영 시 별도 채널 권장
            "expires_hint": "즉시 사용 후 폐기",
        }
        self._log_collector_event(collector_name, "api_key_issued", {"key_name": key_name})
        logger.info(f"암호화 API 키 전달: {collector_name} / {key_name}")
        return payload

    # ── 맵 관리 ──────────────────────────────────────────────
    MAP_DIR = Path("maps")
    MAP_SUBMISSION_DIR = Path("map_submissions")

    def get_map_for_site(self, site: str) -> Optional[str]:
        """보급형 수집기 접속 시 수집 대상 사이트에 맞는 맵을 반환"""
        site = site.strip().lower()
        candidates = [
            self.MAP_DIR / f"{site}.md",
            self.MAP_DIR / f"{site.replace('.', '_')}.md",
            self.MAP_DIR / "courtauction.go.kr.md",  # 기본
        ]
        for p in candidates:
            if p.exists():
                content = p.read_text(encoding="utf-8")
                logger.info(f"맵 전송: {site} → {p.name} ({len(content)}자)")
                return content
        logger.warning(f"맵 없음: {site}")
        return None

    def on_collector_connect(self, collector_name: str, target_site: str) -> dict:
        """보급형 수집기 접속 처리: 사이트 확인 후 관련 맵 전송"""
        logger.info(f"[{collector_name}] 접속. 수집 대상 사이트: {target_site}")
        self._log_collector_event(collector_name, "connect", {"site": target_site})
        map_content = self.get_map_for_site(target_site)
        return {
            "collector": collector_name,
            "site": target_site,
            "map": map_content,
            "map_available": map_content is not None,
            "message": "맵 전송 완료" if map_content else "해당 사이트 맵 없음. 기본 규칙 적용",
        }

    def receive_map_submission(self, collector_name: str, site: str, materials: dict) -> dict:
        """
        보급형 수집기가 새로운 맵 형성 자료를 송부하면 저장·분석
        materials 예시: {
            "scene_name": "열람하기 다이얼로그",
            "description": "...",
            "solution": "...",
            "screenshot_path": "...",
            "success": True
        }
        """
        self.MAP_SUBMISSION_DIR.mkdir(parents=True, exist_ok=True)
        submission_id = str(uuid4())[:8]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}_{site.replace('.', '_')}_{collector_name}_{submission_id}.json"
        filepath = self.MAP_SUBMISSION_DIR / filename

        record = {
            "submission_id": submission_id,
            "collector": collector_name,
            "site": site,
            "received_at": datetime.now().isoformat(),
            "materials": materials,
            "status": "received",  # received → analyzed → merged
            "notes": "",
        }
        filepath.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info(f"맵 자료 수신 저장: {filepath.name}")

        # 간단한 분석 (추후 배포 반영용)
        analysis = self._analyze_map_submission(record)
        record["analysis"] = analysis
        record["status"] = "analyzed"
        filepath.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "submission_id": submission_id,
            "saved_to": str(filepath),
            "analysis": analysis,
            "message": "저장 및 분석 완료. 추후 배포에 반영 예정",
        }

    def _analyze_map_submission(self, record: dict) -> dict:
        """신규 맵 자료 간단 분석"""
        materials = record.get("materials", {})
        scene = materials.get("scene_name", "unknown")
        success = materials.get("success", False)
        return {
            "scene": scene,
            "usable": success,
            "priority": "high" if success else "review",
            "recommendation": "맵에 추가 권장" if success else "추가 사례 필요",
            "analyzed_at": datetime.now().isoformat(),
        }

    def pickup_collector_reports(self, report_dir: str = "reports") -> list:
        """수집기가 파일로 남긴 보고를 읽어 처리 (로컬/데모용)"""
        rdir = Path(report_dir)
        if not rdir.exists():
            return []
        results = []
        for p in sorted(rdir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                results.append(data)
                logger.info(f"보고 수신: {data.get('collector')} / {data.get('case_no')} / success={data.get('success')}")
            except Exception as e:
                logger.warning(f"보고 파일 읽기 실패: {p} - {e}")
        return results

    def list_map_submissions(self, site: Optional[str] = None) -> list:
        """저장된 맵 제출 자료 목록"""
        if not self.MAP_SUBMISSION_DIR.exists():
            return []
        items = []
        for p in sorted(self.MAP_SUBMISSION_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if site and data.get("site") != site:
                    continue
                items.append({
                    "file": p.name,
                    "site": data.get("site"),
                    "collector": data.get("collector"),
                    "status": data.get("status"),
                    "received_at": data.get("received_at"),
                })
            except Exception:
                continue
        return items

    def ensure_case(self, case_no: str, court: str) -> CaseMaster:
        if case_no not in self.cases:
            case = CaseMaster(case_no=case_no, court=court)
            self.cases[case_no] = case
            self._save_case(case)
            logger.info(f"사건 마스터 생성: {case_no} ({court})")
        return self.cases[case_no]

    def create_task(self, case_no: str, task_type: TaskType, priority: int = 5) -> Task:
        task = Task(case_no=case_no, task_type=task_type, priority=priority)
        self.tasks[task.id] = task
        self._save_task(task)
        return task

    def generate_instruction(self, collector_name: str, task_type: TaskType, cases: List[str], wait_seconds: int = 5) -> WorkInstruction:
        steps_map = {
            TaskType.APPRAISAL: [
                "물건 상세 페이지 진입",
                "문건 섹션에서 감정평가서 선택",
                "열람하기 다이얼로그 처리 (맵 우선)",
                "충분한 대기",
                "PDF 다운로드",
            ],
            TaskType.SPEC: ["물건 상세 진입", "매각물건명세서 다운로드"],
            TaskType.STATUS: ["물건 상세 진입", "현황조사서 다운로드"],
            TaskType.BASIC: ["사건번호로 진입", "기본정보 추출"],
        }
        instr = WorkInstruction(
            collector_name=collector_name,
            task_type=task_type.value,
            cases=cases,
            menu_steps=steps_map.get(task_type, ["기본 수집"]),
            must_wait_seconds=wait_seconds,
            save_path=str(self.base_save_path),
            session_exclusive=True,
            notes="관제기 지정 저장 경로 사용",
        )
        self.pending_instructions.append(instr)
        self._save_state()
        logger.info(f"지시서 생성: {collector_name} / {task_type.value} / {len(cases)}건 (대기 {wait_seconds}초)")
        return instr

    def get_next_instruction(self, collector_name: str) -> Optional[WorkInstruction]:
        for instr in self.pending_instructions:
            if instr.collector_name == collector_name:
                self.pending_instructions.remove(instr)
                self._save_state()
                return instr
        return None

    def report_result(self, task_id: str, success: bool, error_msg: str = "", data: Optional[dict] = None):
        if task_id not in self.tasks:
            return
        task = self.tasks[task_id]
        task.finished_at = datetime.now().isoformat()
        task.error_msg = error_msg

        case = self.cases.get(task.case_no)
        if success:
            task.status = TaskStatus.SUCCESS
            if case:
                if task.task_type == TaskType.APPRAISAL:
                    case.has_appraisal = True
                elif task.task_type == TaskType.SPEC:
                    case.has_spec = True
                elif task.task_type == TaskType.STATUS:
                    case.has_status = True
                case.updated_at = datetime.now().isoformat()
                self._save_case(case)
            logger.info(f"성공: {task.case_no} / {task.task_type.value}")
        else:
            task.retry_count += 1
            if task.retry_count < task.max_retry:
                task.status = TaskStatus.RETRY
                if task.task_type == TaskType.APPRAISAL:
                    task.wait_seconds = random.randint(30, 90)
            else:
                task.status = TaskStatus.FAILED
            logger.warning(f"실패: {task.case_no} / {task.task_type.value} ({error_msg})")

        self._save_task(task)
        if self.session_locked_by == task.assigned_to:
            self.session_locked_by = None

        # 수집기 성능 지표 갱신
        if task.assigned_to and task.assigned_to in self.collectors:
            self._update_collector_performance(task.assigned_to, success, task)
            # 로그: 관제형은 전체, 보급형은 기본 결과만
            event = "result_full" if self.collectors[task.assigned_to].get("type") == "control" else "result_basic"
            self._log_collector_event(task.assigned_to, event, {
                "task_id": task_id,
                "case_no": task.case_no,
                "task_type": task.task_type.value,
                "success": success,
                "error": error_msg,
            })

    def plan_next_hour(self):
        """1시간마다 복수 수집기에게 속도·성공률 기반으로 업무 분배"""
        now = datetime.now()
        if self.last_plan_time and (now - self.last_plan_time).total_seconds() < 3600:
            logger.info("아직 1시간이 지나지 않음. 계획 스킵")
            return

        available = [c for c in self.collectors.values() if not c.get("is_busy", False)]
        if not available:
            logger.warning("가용 수집기 없음")
            return

        # 속도·성공률·가중치 기반 점수 계산 후 정렬
        def score(c):
            base = c.get("speed_score", 1.0) * c.get("success_rate", 1.0)
            weight = c.get("weight", 1.0)
            return base * weight

        available.sort(key=score, reverse=True)
        logger.info(f"1시간 계획 시작. 가용 수집기 {len(available)}대 (가중치 반영)")

        pending_appraisals = [
            t for t in self.tasks.values()
            if t.task_type == TaskType.APPRAISAL and t.status in (TaskStatus.PENDING, TaskStatus.RETRY)
        ]
        pending_others = [
            t for t in self.tasks.values()
            if t.task_type != TaskType.APPRAISAL and t.status in (TaskStatus.PENDING, TaskStatus.RETRY)
        ]

        # 감정평가서 우선 분배
        max_per_collector = 5
        assigned_count = 0
        collector_idx = 0

        for task in sorted(pending_appraisals, key=lambda t: t.priority, reverse=True):
            if self.appraisal_today_count >= self.APPRAISAL_DAILY_LIMIT:
                logger.info("오늘 감정평가서 일일 한도 도달")
                break
            if assigned_count >= max_per_collector * len(available):
                break

            collector = available[collector_idx % len(available)]
            task.status = TaskStatus.ASSIGNED
            task.assigned_to = collector["name"]
            task.wait_seconds = random.randint(self.APPRAISAL_WAIT_MIN, self.APPRAISAL_WAIT_MAX)
            self._save_task(task)
            self.appraisal_today_count += 1
            assigned_count += 1
            collector_idx += 1

            # 수집기별로 지시서 묶음 생성 (간단히 건당 1개 지시서)
            self.generate_instruction(
                collector["name"],
                TaskType.APPRAISAL,
                [task.case_no],
                wait_seconds=task.wait_seconds,
            )

        # 기타 태스크도 분배
        for task in sorted(pending_others, key=lambda t: t.priority, reverse=True)[:len(available)*3]:
            collector = available[collector_idx % len(available)]
            task.status = TaskStatus.ASSIGNED
            task.assigned_to = collector["name"]
            self._save_task(task)
            self.generate_instruction(collector["name"], task.task_type, [task.case_no])
            collector_idx += 1

        self.last_plan_time = now
        logger.info(f"계획 완료: 감정평가서 {assigned_count}건을 {len(available)}대 수집기에 분배")

    def _update_collector_performance(self, name: str, success: bool, task: Task):
        """결과 보고 시 수집기 속도·성공률 갱신"""
        c = self.collectors.get(name)
        if not c:
            return
        total = c.get("total_completed", 0) + c.get("total_failed", 0)
        if success:
            c["total_completed"] = c.get("total_completed", 0) + 1
        else:
            c["total_failed"] = c.get("total_failed", 0) + 1
        new_total = c["total_completed"] + c["total_failed"]
        c["success_rate"] = c["total_completed"] / max(new_total, 1)

        # 소요 시간이 있으면 평균 갱신 (간단히 대기시간 기반)
        duration = task.wait_seconds or 30
        old_avg = c.get("avg_duration", 30.0)
        c["avg_duration"] = (old_avg * total + duration) / max(new_total, 1)
        # 속도 점수: 성공률 높고 평균 시간이 짧을수록 높음
        c["speed_score"] = round(c["success_rate"] * (60.0 / max(c["avg_duration"], 10)), 2)
        logger.info(f"[{name}] 성능 갱신: 성공률={c['success_rate']:.2f}, 속도점수={c['speed_score']}")
        self._save_state()

    def get_status_summary(self) -> dict:
        total_cases = len(self.cases)
        appraisal_done = sum(1 for c in self.cases.values() if c.has_appraisal)
        pending_appraisal = sum(
            1 for t in self.tasks.values()
            if t.task_type == TaskType.APPRAISAL and t.status in (TaskStatus.PENDING, TaskStatus.RETRY)
        )
        return {
            "총 사건": total_cases,
            "감정평가서 완료": appraisal_done,
            "감정평가서 대기": pending_appraisal,
            "오늘 감정평가서 시도": self.appraisal_today_count,
            "등록 수집기": len(self.collectors),
            "세션 점유": self.session_locked_by or "없음",
            "저장 경로": str(self.base_save_path),
        }

    # DB helpers (simplified)
    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS cases (case_no TEXT PRIMARY KEY, data TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT)")
        conn.commit()
        conn.close()

    def _save_case(self, case: CaseMaster):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO cases (case_no, data) VALUES (?, ?)", (case.case_no, json.dumps(asdict(case), ensure_ascii=False)))
        conn.commit()
        conn.close()

    def _save_task(self, task: Task):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO tasks (id, data) VALUES (?, ?)", (task.id, json.dumps(asdict(task), ensure_ascii=False)))
        conn.commit()
        conn.close()

    def _save_state(self):
        """수집기 목록·자연어 작업·대기 지시서를 파일에 저장 (임시 파일에 쓴 뒤 교체 → 도중에 꺼져도 파일이 깨지지 않음)"""
        data = {
            "version": 1,
            "saved_at": datetime.now().isoformat(),
            "collectors": self.collectors,
            "natural_tasks": self.natural_tasks,
            "pending_instructions": [asdict(i) for i in self.pending_instructions],
        }
        try:
            tmp = self.state_path.with_name(self.state_path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception as e:
            logger.error(f"상태 저장 실패: {e}")

    def _load_state(self):
        """재시작 시 저장된 목록(수집기·자연어 작업·대기 지시서) 복원"""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.collectors = dict(data.get("collectors") or {})
                for c in self.collectors.values():
                    c["is_busy"] = False  # 꺼지기 전 작업 중이던 표시는 해제
                self.natural_tasks = list(data.get("natural_tasks") or [])
                fields = set(WorkInstruction.__dataclass_fields__)
                self.pending_instructions = [
                    WorkInstruction(**{k: v for k, v in d.items() if k in fields})
                    for d in (data.get("pending_instructions") or [])
                ]
                logger.info(f"상태 복원: 수집기 {len(self.collectors)}대, 자연어 작업 {len(self.natural_tasks)}건, "
                            f"대기 지시서 {len(self.pending_instructions)}건")
            except Exception as e:
                # 깨진 파일은 지우지 않고 옆에 보관해 두고 빈 상태로 시작
                bad = self.state_path.with_name(self.state_path.name + ".broken")
                try:
                    self.state_path.replace(bad)
                except Exception:
                    pass
                logger.error(f"상태 파일 읽기 실패({e}) → {bad.name}로 보관하고 빈 상태로 시작")


def main():
    ctrl = AuctionController(base_save_path="data/auction_collected")
    ctrl.register_collector("수집기-A", "control", 1.2)
    ctrl.register_collector("보급형-user01", "supply", 0.9)

    # 테스트 사건
    for i in range(1, 4):
        case_no = f"2026타경{10000 + i}"
        ctrl.ensure_case(case_no, "대전지방법원")
        ctrl.create_task(case_no, TaskType.APPRAISAL, priority=8)

    ctrl.plan_next_hour()
    print("현황:", ctrl.get_status_summary())

    instr = ctrl.get_next_instruction("수집기-A")
    if instr:
        print("지시서:", asdict(instr))


if __name__ == "__main__":
    main()
