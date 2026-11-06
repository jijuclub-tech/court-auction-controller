"""
수집기 UI - 전체 기능
- 상태 / 시작·중지
- 로그인 (관제형)
- 설정 (관제기 주소, AI 계정)
- 작업 / 보고
- 로그
"""
from __future__ import annotations

import json
import requests
import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, messagebox, ttk

from core.appraisal import AppraisalDownloader
from core.login import ControlTypeLogin, save_local_credentials, load_local_credentials
from core.access_policy import is_public_institution, should_suppress_option, save_choice

CONFIG_PATH = Path("config.json")
SETTINGS_PATH = Path("settings.json")


class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        try:
            self.log_queue.put(self.format(record))
        except Exception:
            pass


class CollectorUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Court Auction Collector")
        self.root.geometry("860x560")
        self.root.minsize(720, 480)

        self.config = self._load_json(CONFIG_PATH) or {}
        self.settings = self._load_json(SETTINGS_PATH) or {}
        self.name = self.config.get("name", "unknown")
        self.ctype = self.config.get("type", "supply")
        self.running = False
        self.worker = None
        self.log_queue = queue.Queue()
        self.needs_human = False

        self._setup_logging()
        self._build_ui()
        self._poll_log()
        self._refresh_reports()

        if not self.config:
            messagebox.showerror("Error", "config.json not found.\nRun install.bat first.")

    def _load_json(self, path: Path):
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _save_settings(self):
        SETTINGS_PATH.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2), encoding="utf-8")

    def _setup_logging(self):
        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.addHandler(handler)

    def _build_ui(self):
        # Top bar
        top = tk.Frame(self.root, padx=10, pady=6)
        top.pack(fill=tk.X)
        tk.Label(top, text="Collector", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Idle")
        tk.Label(top, textvariable=self.status_var, font=("Segoe UI", 11), fg="#166534").pack(side=tk.RIGHT)

        info = tk.Frame(self.root, padx=10)
        info.pack(fill=tk.X)
        tk.Label(info, text=f"Name: {self.name}").pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(info, text=f"Type: {self.ctype}").pack(side=tk.LEFT, padx=(0, 12))
        self.ctrl_var = tk.StringVar(value="Controller: not connected")
        tk.Label(info, textvariable=self.ctrl_var, fg="#666").pack(side=tk.LEFT)

        # Notebook
        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        self._build_status_tab(nb)
        self._build_login_tab(nb)
        self._build_collect_tab(nb)
        self._build_settings_tab(nb)
        self._build_reports_tab(nb)
        self._build_log_tab(nb)

    def _build_status_tab(self, nb):
        f = tk.Frame(nb, padx=10, pady=10)
        nb.add(f, text="Status")

        tk.Label(f, text="Current task", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.task_var = tk.StringVar(value="-")
        tk.Label(f, textvariable=self.task_var, font=("Segoe UI", 12)).pack(anchor="w", pady=(0, 10))

        self.human_var = tk.StringVar(value="")
        tk.Label(f, textvariable=self.human_var, fg="#b45309", font=("Segoe UI", 11, "bold")).pack(anchor="w")

        btns = tk.Frame(f)
        btns.pack(anchor="w", pady=10)
        self.start_btn = tk.Button(btns, text="Start", width=12, command=self.start)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = tk.Button(btns, text="Stop", width=12, command=self.stop, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        tk.Label(f, text="Site: courtauction.go.kr", fg="#444").pack(anchor="w", pady=(16, 0))
        self.input_var = tk.StringVar(value="Input: Pico2 (HID)")
        tk.Label(f, textvariable=self.input_var, fg="#444").pack(anchor="w")

    def _build_login_tab(self, nb):
        f = tk.Frame(nb, padx=10, pady=10)
        nb.add(f, text="Login")

        if self.ctype != "control":
            tk.Label(f, text="Login is available only for Control type collectors.", fg="#666").pack(anchor="w")
            return

        tk.Label(f, text="Site login (Control type only)", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(f, text="Direct input or use browser saved credentials.", fg="#666").pack(anchor="w", pady=(0, 8))

        tk.Label(f, text="Username").pack(anchor="w")
        self.user_entry = tk.Entry(f, width=40)
        self.user_entry.insert(0, "admin")
        self.user_entry.pack(anchor="w", pady=(0, 6))

        tk.Label(f, text="Password").pack(anchor="w")
        self.pw_entry = tk.Entry(f, width=40, show="*")
        self.pw_entry.insert(0, "admin")
        self.pw_entry.pack(anchor="w", pady=(0, 6))

        self.use_saved = tk.BooleanVar(value=True)
        tk.Checkbutton(f, text="Try browser saved credentials if empty", variable=self.use_saved).pack(anchor="w")

        tk.Button(f, text="Login", width=12, command=self.do_login).pack(anchor="w", pady=10)
        self.login_result = tk.StringVar(value="")
        tk.Label(f, textvariable=self.login_result, fg="#166534").pack(anchor="w")

    def _build_settings_tab(self, nb):
        f = tk.Frame(nb, padx=10, pady=10)
        nb.add(f, text="Settings")

        tk.Label(f, text="Controller address", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.ctrl_entry = tk.Entry(f, width=50)
        self.ctrl_entry.insert(0, self.settings.get("controller_url", ""))
        self.ctrl_entry.pack(anchor="w", pady=(0, 10))

        tk.Label(f, text="AI account (optional)", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(f, text="Personal AI account for this collector.", fg="#666").pack(anchor="w")
        self.ai_entry = tk.Entry(f, width=50)
        self.ai_entry.insert(0, self.settings.get("ai_account", ""))
        self.ai_entry.pack(anchor="w", pady=(0, 10))

        tk.Button(f, text="Save settings", command=self.save_settings).pack(anchor="w", pady=4)
        tk.Button(f, text="Connect to controller", command=self.connect_controller).pack(anchor="w")
        self.settings_msg = tk.StringVar(value="")
        tk.Label(f, textvariable=self.settings_msg, fg="#166534").pack(anchor="w", pady=6)

    def _build_collect_tab(self, nb):
        f = tk.Frame(nb, padx=10, pady=10)
        nb.add(f, text="Collect")

        if self.ctype == "control":
            tk.Label(f, text="관제형 수집기는 관제기의 지시를 수행합니다.", font=("Segoe UI", 11), fg="#444", wraplength=400).pack(anchor="w", pady=20)
            tk.Button(f, text="Start collection", command=self.start).pack(anchor="w", pady=8)
        else:
            tk.Label(f, text="수집할 내용 입력하세요", font=("Segoe UI", 11, "bold")).pack(anchor="w")
            self.case_box = scrolledtext.ScrolledText(f, height=8, font=("Consolas", 10))
            self.case_box.pack(fill=tk.X, pady=6)

            tk.Label(f, text="Task type").pack(anchor="w")
            self.task_type = tk.StringVar(value="appraisal")
            tk.Radiobutton(f, text="Appraisal (감정평가서)", variable=self.task_type, value="appraisal").pack(anchor="w")
            tk.Radiobutton(f, text="Registry (등기부등본)", variable=self.task_type, value="registry").pack(anchor="w")

            tk.Button(f, text="Start collection", command=self.start_collection).pack(anchor="w", pady=8)

        self.collect_msg = tk.StringVar(value="")
        tk.Label(f, textvariable=self.collect_msg, fg="#166534").pack(anchor="w")

    def start_collection(self):
        if self.running:
            messagebox.showinfo("Busy", "Already running.")
            return
        if self.ctype == "control":
            self.start()
            return
        raw = self.case_box.get("1.0", tk.END).strip()
        cases = [c.strip() for c in raw.splitlines() if c.strip()]
        if not cases:
            messagebox.showwarning("Input", "수집할 내용을 입력하세요.")
            return
        self.pending_cases = cases
        self.pending_task = self.task_type.get()
        self.start()

    def _build_reports_tab(self, nb):
        f = tk.Frame(nb, padx=10, pady=10)
        nb.add(f, text="Reports")

        top = tk.Frame(f)
        top.pack(fill=tk.X)
        tk.Label(top, text="Local reports", font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        tk.Button(top, text="Refresh", command=self._refresh_reports).pack(side=tk.RIGHT)

        self.report_list = tk.Listbox(f, height=16)
        self.report_list.pack(fill=tk.BOTH, expand=True, pady=6)

    def _build_log_tab(self, nb):
        f = tk.Frame(nb, padx=10, pady=10)
        nb.add(f, text="Log")
        self.log_box = scrolledtext.ScrolledText(f, wrap=tk.WORD, font=("Consolas", 9))
        self.log_box.pack(fill=tk.BOTH, expand=True)
        self.log_box.config(state=tk.DISABLED)

    def _append_log(self, text: str):
        self.log_box.config(state=tk.NORMAL)
        self.log_box.insert(tk.END, text + "\n")
        self.log_box.see(tk.END)
        self.log_box.config(state=tk.DISABLED)

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.root.after(200, self._poll_log)

    def _refresh_reports(self):
        self.report_list.delete(0, tk.END)
        rdir = Path("reports")
        if not rdir.exists():
            return
        for p in sorted(rdir.glob("*.json"), reverse=True)[:50]:
            self.report_list.insert(tk.END, p.name)

    def save_settings(self):
        self.settings["controller_url"] = self.ctrl_entry.get().strip()
        self.settings["ai_account"] = self.ai_entry.get().strip()
        self._save_settings()
        self.settings_msg.set("Saved.")
        url = self.settings.get("controller_url") or ""
        self.ctrl_var.set(f"Controller: {url or 'not connected'}")

    def connect_controller(self):
        url = self.settings.get("controller_url") or self.ctrl_entry.get().strip()
        if not url:
            self.settings_msg.set("Enter controller address first.")
            return
        url = url.rstrip("/")
        try:
            r = requests.post(
                f"{url}/api/collector/connect",
                json={"name": self.name, "site": "courtauction.go.kr", "type": self.ctype},
                timeout=10,
            )
            if r.status_code == 200:
                self.ctrl_var.set(f"Controller: connected ({url})")
                self.settings_msg.set("Connected.")
                self._fetch_instruction()
                self._fetch_policy()
            else:
                self.settings_msg.set(f"Connect failed: {r.status_code}")
        except Exception as e:
            self.settings_msg.set(f"Connect error: {e}")

    def _fetch_policy(self):
        url = (self.settings.get("controller_url") or "").rstrip("/")
        if not url:
            return
        try:
            r = requests.get(f"{url}/api/policy", timeout=10)
            if r.status_code == 200:
                policy = r.json()
                Path("work_policy.json").write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
                logging.getLogger("Main").info(f"Policy received: {policy.get('goal','')}")
                # 부하분산·관할 정보 있으면 로그
                nodes = policy.get("collector_nodes", [])
                if nodes:
                    logging.getLogger("Main").info(f"정책 노드 수: {len(nodes)}")
                self.task_var.set(policy.get("goal", "Policy loaded"))
        except Exception as e:
            logging.getLogger("Main").warning(f"Policy fetch failed: {e}")

    def _fetch_instruction(self):
        url = (self.settings.get("controller_url") or "").rstrip("/")
        if not url:
            return
        try:
            r = requests.get(f"{url}/api/collector/instruction/{self.name}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                inst = data.get("instruction")
                if inst:
                    self.task_var.set(f"Instruction: {inst}")
                    logging.getLogger("Main").info(f"Instruction: {inst}")
                else:
                    logging.getLogger("Main").info("No instruction")
        except Exception as e:
            logging.getLogger("Main").warning(f"Instruction fetch failed: {e}")

    def do_login(self):
        if self.ctype != "control":
            return
        user = self.user_entry.get().strip() or None
        pw = self.pw_entry.get().strip() or None
        if user and pw:
            save_local_credentials(user, pw)
        try:
            login = ControlTypeLogin(self.name, self.ctype)
            result = login.login(
                site="courtauction.go.kr",
                username=user,
                password=pw,
                use_browser_saved=self.use_saved.get(),
                permission_from_controller=True,
            )
            ok = result.get("success")
            self.login_result.set(f"Result: {ok} ({result.get('reason', result.get('source', ''))})")
            if ok:
                self.status_var.set("Logged in")
        except Exception as e:
            self.login_result.set(f"Error: {e}")

    def _check_display_scale(self) -> float:
        """Windows 디스플레이 배율 확인 (100% = 1.0)"""
        try:
            import ctypes
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            dpi = user32.GetDpiForSystem() if hasattr(user32, "GetDpiForSystem") else 96
            return dpi / 96.0
        except Exception:
            return 1.0

    def _ask_scale_action(self, scale: float) -> str:
        """배율 경고: 변경 / 무시 선택. 반환값: 'change' 또는 'ignore'"""
        dlg = tk.Toplevel(self.root)
        dlg.title("화면 배율 확인")
        dlg.geometry("440x220")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        result = {"action": "ignore"}

        msg = (
            f"현재 Windows 디스플레이 배율: {int(scale * 100)}%\n\n"
            "피코2 클릭이 정확하려면 배율을 100%로 맞춰 주세요.\n"
            "브라우저 확대율(Ctrl+0)과 별개입니다."
        )
        tk.Label(dlg, text=msg, justify="left", wraplength=400).pack(padx=16, pady=(16, 8))

        # 설정 링크
        link = tk.Label(dlg, text="Windows 디스플레이 설정 열기", fg="#1d4ed8", cursor="hand2")
        link.pack(pady=(0, 12))
        link.bind("<Button-1>", lambda e: self._open_display_settings())

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(pady=8)

        def on_change():
            result["action"] = "change"
            self._open_display_settings()
            dlg.destroy()

        def on_ignore():
            result["action"] = "ignore"
            dlg.destroy()

        tk.Button(btn_frame, text="변경", width=10, command=on_change).pack(side=tk.LEFT, padx=8)
        tk.Button(btn_frame, text="무시", width=10, command=on_ignore).pack(side=tk.LEFT, padx=8)

        dlg.wait_window()
        return result["action"]

    def _open_display_settings(self):
        """Windows 디스플레이 설정 열기"""
        try:
            import subprocess
            subprocess.Popen(["cmd", "/c", "start", "ms-settings:display"])
        except Exception:
            pass

    def _show_startup_options(self):
        """시작 시 옵션 메뉴 다이얼로그"""
        dlg = tk.Toplevel(self.root)
        dlg.title("수집기 시작 옵션")
        dlg.geometry("520x640")
        dlg.transient(self.root)
        dlg.grab_set()

        opts = {}

        # 1. 웹페이지 수집 프로그램
        tk.Label(dlg, text="1. 웹페이지 수집 프로그램 사용 여부", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        site = "courtauction.go.kr"
        if is_public_institution(site):
            tk.Label(dlg, text="공공기관 → 자동 미사용 (피코2만)", fg="#666").pack(anchor="w", padx=20)
            opts["use_browser"] = False
        else:
            opts["use_browser"] = tk.BooleanVar(value=False)
            tk.Checkbutton(dlg, text="Selenium / Playwright 사용 (주의: 기록 남음·차단 가능)", variable=opts["use_browser"]).pack(anchor="w", padx=20)

        # 2. 천천히 / 서둘러 + 작업/휴식
        tk.Label(dlg, text="2. 작업 속도 및 휴식", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        pace_frame = tk.Frame(dlg)
        pace_frame.pack(anchor="w", padx=20)
        opts["pace"] = tk.StringVar(value="normal")
        tk.Radiobutton(pace_frame, text="천천히", variable=opts["pace"], value="slow").pack(side=tk.LEFT)
        tk.Radiobutton(pace_frame, text="보통", variable=opts["pace"], value="normal").pack(side=tk.LEFT)
        tk.Radiobutton(pace_frame, text="서둘러", variable=opts["pace"], value="fast").pack(side=tk.LEFT)
        tk.Label(dlg, text="기본: 50분 작업 후 5분 휴식 (로그인 아웃 후 재접속)").pack(anchor="w", padx=20)

        # 3. 야간/주간
        tk.Label(dlg, text="3. 작업 시간대", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        opts["schedule"] = tk.StringVar(value="day")
        sched = tk.Frame(dlg)
        sched.pack(anchor="w", padx=20)
        tk.Radiobutton(sched, text="주간", variable=opts["schedule"], value="day").pack(side=tk.LEFT)
        tk.Radiobutton(sched, text="야간", variable=opts["schedule"], value="night").pack(side=tk.LEFT)
        tk.Radiobutton(sched, text="주간+야간", variable=opts["schedule"], value="both").pack(side=tk.LEFT)

        # 4. 속도 가감
        tk.Label(dlg, text="4. 속도 자동/수동 (1=아주 느림 ~ 5=빠름, 3=보통)", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        opts["speed"] = tk.IntVar(value=3)
        speed_frame = tk.Frame(dlg)
        speed_frame.pack(anchor="w", padx=20)
        for i in range(1, 6):
            tk.Radiobutton(speed_frame, text=str(i), variable=opts["speed"], value=i).pack(side=tk.LEFT)
        tk.Checkbutton(dlg, text="자동 속도 조절", variable=tk.BooleanVar(value=True)).pack(anchor="w", padx=20)

        # 5. 로그인 저장
        tk.Label(dlg, text="5. 로그인 아이디/암호", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        opts["save_login"] = tk.BooleanVar(value=True)
        tk.Checkbutton(dlg, text="아이디/암호 저장 (관제형 전용)", variable=opts["save_login"]).pack(anchor="w", padx=20)
        tk.Label(dlg, text="미저장 시 필요 시 사용자 호출").pack(anchor="w", padx=20)

        # 6. 추가 AI
        tk.Label(dlg, text="6. 추가 AI 사용", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        opts["extra_ai"] = tk.StringVar(value="none")
        ai_frame = tk.Frame(dlg)
        ai_frame.pack(anchor="w", padx=20)
        for name in ["없음", "ChatGPT", "Gemini", "Claude", "Grok", "Local AI"]:
            tk.Radiobutton(ai_frame, text=name, variable=opts["extra_ai"], value=name).pack(side=tk.LEFT)
        tk.Label(dlg, text="선택 시 아이디/암호는 Settings 탭에서 입력").pack(anchor="w", padx=20)

        # 7. 기본 Gemini API
        tk.Label(dlg, text="7. 기본 Gemini API", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 4))
        tk.Label(dlg, text="관제기에서 상위 버전 API 키 수신 (수집기는 보관하지 않음)").pack(anchor="w", padx=20)

        # 8. 기타
        tk.Label(dlg, text="8. 기타 옵션 (추후 확장)").pack(anchor="w", padx=12, pady=(12, 4))

        def on_ok():
            # 값 저장
            self.startup_opts = {
                "use_browser": opts.get("use_browser") if isinstance(opts.get("use_browser"), bool) else opts.get("use_browser", tk.BooleanVar()).get(),
                "pace": opts["pace"].get(),
                "schedule": opts["schedule"].get(),
                "speed": opts["speed"].get(),
                "save_login": opts["save_login"].get(),
                "extra_ai": opts["extra_ai"].get(),
            }
            self.settings["startup_opts"] = self.startup_opts
            self._save_settings()
            logging.getLogger("Main").info(f"시작 옵션: {self.startup_opts}")
            dlg.destroy()
            self._proceed_start()

        def on_cancel():
            dlg.destroy()

        btn_frame = tk.Frame(dlg)
        btn_frame.pack(pady=16)
        tk.Button(btn_frame, text="저장 닫기", width=14, command=on_ok).pack(side=tk.LEFT, padx=8)
        tk.Button(btn_frame, text="취소", width=10, command=on_cancel).pack(side=tk.LEFT)

        dlg.wait_window()

    def start(self):
        if self.running or not self.config:
            return
        self._show_startup_options()

    def _proceed_start(self):
        """옵션 확인 후 실제 시작"""
        if self.running or not self.config:
            return

        site = "courtauction.go.kr"
        use_browser = self.startup_opts.get("use_browser", False) if hasattr(self, "startup_opts") else False
        if is_public_institution(site):
            use_browser = False

        self.input_var.set(f"Input: {'Selenium/Playwright' if use_browser else 'Pico2 (HID)'}")
        logging.getLogger("Main").info(f"최종 입력 방식: use_browser={use_browser}")

        self.running = True
        self.status_var.set("Running")
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.human_var.set("")
        self.worker = threading.Thread(target=self._run_job, daemon=True)
        self.worker.start()

    def stop(self):
        self.running = False
        self.status_var.set("Stopping")
        self.stop_btn.config(state=tk.DISABLED)

    def _run_job(self):
        logger = logging.getLogger("Main")
        try:
            logger.info(f"=== Start: {self.name} ({self.ctype}) ===")
            self.root.after(0, lambda: self.task_var.set("Preparing"))

            if self.ctype == "control":
                try:
                    ControlTypeLogin(self.name, self.ctype)
                    logger.info("Control type: login module ready")
                except PermissionError as e:
                    logger.error(str(e))

            if not self.running:
                return

            downloader = AppraisalDownloader(self.name)
            cases = getattr(self, "pending_cases", None) or []
            if self.ctype == "control" and not cases:
                logger.info("관제형: 관제기 지시 대기")
                self.root.after(0, lambda: self.task_var.set("관제기 지시 대기"))
                return
            task = getattr(self, "pending_task", "appraisal")
            for case_no in cases:
                if not self.running:
                    break
                self.root.after(0, lambda c=case_no: self.task_var.set(f"{task}: {c}"))
                logger.info(f"Collect {task}: {case_no}")
                if task == "appraisal":
                    result = downloader.download_appraisal(case_no, must_wait=5)
                else:
                    logger.info("Registry: security code step (AI or human)")
                    result = {"case_no": case_no, "success": True, "task": "registry"}

                if result.get("needs_human"):
                    self.needs_human = True
                    self.root.after(0, lambda: self.human_var.set("User action required"))
                    logger.warning("Needs human")
                    break

                logger.info(f"Result: {json.dumps(result, ensure_ascii=False)}")

            self.root.after(0, self._refresh_reports)
            logger.info("Done.")
        except Exception as e:
            logger.error(f"Error: {e}")
        finally:
            self.running = False
            self.root.after(0, self._on_job_done)

    def _on_job_done(self):
        self.status_var.set("Idle")
        self.task_var.set("-")
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)


def main():
    try:
        root = tk.Tk()
        root.title("Court Auction Collector")
        CollectorUI(root)
        print("[INFO] UI started. Close the window to exit.")
        root.mainloop()
    except Exception as e:
        print(f"[ERROR] Failed to start UI: {e}")
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")


if __name__ == "__main__":
    main()
