# -*- coding: utf-8 -*-
"""단독 수집기 실행 입구.

  python -m standalone keys                 키 입력(선택: DPAPI 저장)
  python -m standalone keys --forget        저장된 키 모두 지우기
  python -m standalone sync                 최신 DB 받기만
  python -m standalone run <사이트JSON> [--job 이름] [--minutes 30]
  python -m standalone stop <작업|all>      멈춤 파일 만들기(사람이 지워야 다시 돎)
  python -m standalone status               쉬는 시간·429·멈춤 파일 보기
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import VERSION
from .config import load
from .secrets_store import SecretStore, Redactor, NAMES
from .logger import JsonLog
from .stops import StopFiles, job_name
from .pacing import RateState, Pacer
from .screen_guard import ScreenGuard, default_errors_json
from .snippets import SnippetBank
from .storage import Storage
from .power import PowerWatch
from .ai_helper import AIHelper
from .kb_sync import KBSync
from .click_guard import ClickGuard
from .isleep import InterruptibleSleep, StopRequested
from .account_lock import AccountLock


def build(job, spec, cfg, hw, secrets, log, now=None, sleep=None, kb=None, redactor=None, refpack=None):
    import time
    now = now or time.time
    base_sleep = sleep or time.sleep
    site_key = spec.get("rate_key") or spec.get("family") or job.split("_")[0]
    from .config import merge_site_pacing
    pacing = merge_site_pacing(cfg["pacing"], spec.get("pacing"))   # 사이트 JSON 은 더 느리게만 (검수 H1)
    cfg = dict(cfg, pacing=pacing)
    family = spec.get("family") or job.split("_")[0]
    stops = StopFiles(cfg["stop_dir"], cfg.get("collector_id", ""))
    power = PowerWatch(cfg["power"], log=log)
    isleep = InterruptibleSleep(stops, job, family, power, sleep_fn=base_sleep, now_fn=now)   # STOP 보는 잠자기 (M2)
    rs = RateState(os.path.join(cfg["state_dir"], "rate_state_%s.json" % job_name(site_key)), now,
                   rest_min_on_corrupt=pacing.get("rate_rest_min", 120), log=log)
    from .runner import Runner
    guard = ScreenGuard(default_errors_json(cfg["kb_dir"]), spec.get("guard_site", "court"))
    ai = AIHelper(secrets, cfg["ai"], log, redactor=redactor) if spec.get("ai", True) else None
    runner_holder = {}
    hw.click_guard = ClickGuard(hw.grab, ocr=lambda im, b: hw.ocr_text(im, b), log=lambda r: log("click_guard", **r),
                                kh_dir=os.path.join(cfg["state_dir"], "click_guard"), write_stop=False,
                                shot_ok=lambda: not runner_holder.get("r") or not runner_holder["r"].in_login,
                                redact=(redactor.text if redactor else None))
    acct = None
    if spec.get("login"):
        acct = AccountLock(cfg["state_dir"], cfg.get("collector_id", ""), "pending",
                           windows=cfg.get("court_account_window", []),
                           lock_min=cfg.get("court_account_lock_min", 180), kb=kb, now=now)
    r = Runner(job, spec, hw, cfg, log=log, stops=stops, guard=guard,
               pacer=Pacer(pacing, rs, now, isleep), rate_state=rs,
               bank=SnippetBank(cfg["kb_dir"], site=spec.get("site"), scale_cfg=cfg.get("snippet_scales"), mem=refpack,
                                gallery_max=(cfg.get("refpack") or {}).get("gallery_max_per_step", 3)),
               storage=Storage(cfg["storage"], cfg["state_dir"], log), power=power,
               ai=ai, secrets=secrets, sleep=isleep, now=now, account_lock=acct, refpack=refpack,
               console=None)   # N3: 무인 실행 — 캡차 답은 answer.json 으로만(콘솔 input 으로 멈추지 않게)
    runner_holder["r"] = r
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(prog="standalone")
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keys"); k.add_argument("--forget", action="store_true")
    sub.add_parser("sync")
    r = sub.add_parser("run"); r.add_argument("site"); r.add_argument("--job"); r.add_argument("--minutes", type=int)
    s = sub.add_parser("stop"); s.add_argument("job")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    cfg = load()
    secrets = SecretStore()
    red = Redactor(secrets)

    if a.cmd == "keys":
        if a.forget:
            secrets.forget_saved()
            print("저장된 키를 지웠어요.")
            return 0
        dpapi_key = str((cfg.get("crypto") or {}).get("key_source", "config")).lower() == "dpapi"
        for n in NAMES:
            if n == "DATA_KEY" and not dpapi_key:
                continue                       # 자료 키는 key_source=dpapi 일 때만 묻는다(지금은 시험 키)
            secrets.get(n, required=True)
        # 첫 실행 때 물어보는 켜고/끄기 항목 (값은 config.json 에 손으로 넣어요 — keys 는 비밀값만 저장)
        try:
            ntp_on = input("멈춤 진단에 NTP 확인을 켤까요? (기본 켜짐) [Y/n]: ").strip().lower() not in ("n", "no", "아니오")
            head_on = input("완료 사이트에 한해 상대 서버 HEAD 확인을 켤까요? 법원·iros 는 제외(기본 꺼짐) [y/N]: ").strip().lower() in ("y", "yes", "예")
            print("→ config.json 에 넣어 주세요:  \"ntp_check\": %s" % ("true" if ntp_on else "false"))
            print("→ 사이트 JSON(법원·iros 제외)에:  \"site_alive_check\": %s" % ("true" if head_on else "false"))
            if not (cfg.get("kb_server") or {}).get("url"):
                print("→ 관제기(사무실 서버) 주소 kb_server.url 을 config.json 에 꼭 넣어 주세요. 없으면 작업을 시작하지 않아요(v1.6).")
        except (EOFError, OSError):
            pass
        print("입력 끝. 값은 화면·기록에 안 나와요.")
        return 0
    if a.cmd == "stop":
        st = StopFiles(cfg["stop_dir"], cfg.get("collector_id", ""))
        p = os.path.join(cfg["stop_dir"], "STOP_ALL") if a.job == "all" else st.path(a.job)
        open(p, "a", encoding="utf-8").write("사람이 멈춤\n")
        print("멈춤 파일:", p)
        return 0
    if a.cmd == "status":
        import time
        d = cfg["state_dir"]
        print("collector_id:", cfg.get("collector_id") or "(없음! 시작 거부됨)")
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if f.startswith("rate_state_"):
                print(f, open(os.path.join(d, f), encoding="utf-8").read())
        sd = cfg["stop_dir"]
        for f in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
            if f == "STOP" or f.startswith("STOP_"):
                p = os.path.join(sd, f)
                first = (open(p, encoding="utf-8", errors="replace").read().splitlines() or [""])[0]
                age_h = (time.time() - os.path.getmtime(p)) / 3600
                print("멈춤 파일: %s  (%.1f시간 전) %s" % (f, age_h, first[:80]))
        return 0

    spec = json.load(open(a.site, encoding="utf-8")) if a.cmd == "run" else {}
    job = job_name(a.job or spec.get("job") or spec.get("site", "job")) if a.cmd == "run" else "sync"
    log = JsonLog(cfg["log_dir"], job, red, collector_id=cfg.get("collector_id", ""))
    kb = KBSync(cfg["kb_dir"], cfg["kb_server"], secrets, VERSION, cfg.get("machine", ""), log, collector_id=cfg.get("collector_id", ""))
    if a.cmd == "sync":
        print(json.dumps(kb.sync(), ensure_ascii=False))
        return 0
    if _is_nas(cfg["stop_dir"]):
        log("stop_dir_on_nas", path=cfg["stop_dir"])
        print("경고: stop_dir 가 공유 폴더(NAS) 같아요. 한 STOP 이 여러 수집기를 함께 멈출 수 있어요.")
    if not cfg.get("collector_id"):
        print("collector_id 가 비어 있어요. config.json 에 넣어 주세요(예: nonmetro-01). 시작하지 않아요.")
        return 2
    # v1.6(t850u·t851u): 자료 키. 2026-12-01 00:00 KST 뒤에도 시험 키면 경고하고 멈춘다.
    key = check_data_key(job, cfg, secrets, log)
    if key is None:
        return 5
    # v1.6(t844u): 온라인 전제 — 관제기 접속·DB 받기·사이트 참고자료(화보 포함) 메모리 적재가 안 되면 시작하지 않는다.
    from .refpack import file_ui
    ui = file_ui(cfg["state_dir"])
    got = prepare_online(job, spec, cfg, kb, log, ui, key=key)
    if got is None:
        return 4
    client, pack = got
    if a.minutes:
        spec["max_minutes"] = a.minutes
    from .hw import PicoHW
    try:
        hw = PicoHW(cfg["pico"], log)
    except Exception as e:  # noqa: BLE001  (검수 P11: 오류 전문 대신 한 줄 안내)
        client.finish(pack, collector_id=cfg.get("collector_id", ""))
        log("pico_connect_fail", err=type(e).__name__)
        print("피코2 연결 실패(%s): USB 연결·COM 포트(config pico.port)를 확인하세요. 작업 시작 안 함." % type(e).__name__)
        return 6
    runner = None
    try:
        runner = build(job, spec, cfg, hw, secrets, log, kb=kb, redactor=red, refpack=pack)
        ui("작업 중: %s (메모리 자료만 사용)" % job, state="running")
        try:
            res = runner.run()
        except StopRequested as e:
            res = {"end": "stop_file", "why": e.why}
        except Exception as e:  # noqa: BLE001  (검수 M7: 종류 이름만, 값·traceback 금지)
            log("crash", err=type(e).__name__, msg=red.text(str(e))[:120])
            res = {"end": "crash", "err": type(e).__name__}
    finally:
        hw.close()
        rank = _rank_rates(runner.locator.bank) if runner is not None else None
        rep = client.finish(pack, rank=rank, dup_dist=(cfg.get("refpack") or {}).get("dup_dist", 6),
                            protect_top_pct=(cfg.get("refpack") or {}).get("protect_top_pct", 20),
                            collector_id=cfg.get("collector_id", ""))
        ui("끝: 메모리 해제(정리 %d건)" % rep.get("removed_count", 0), state="idle")
    print(json.dumps(res, ensure_ascii=False))
    if res["end"] in ("done", "run_cap"):
        return 0
    if res["end"] in ("captcha_human_required", "captcha_fail", "vk_login_star_mismatch", "account_busy"):
        return 10                      # 사람 확인 필요 (bat 이 이 코드로 알림)
    return 3


def check_data_key(job, cfg, secrets, log, now=None):
    """자료 암호화 키 준비. 갱신 시각(2026-12-01 00:00 KST) 뒤 시험 키면 STOP·경고 후 None."""
    import time
    from .crypto import key_from_config, KeyUpgradeRequired, KEY_MARK, set_default_key
    try:
        key = key_from_config(cfg, secrets)
        key.check_upgrade((now or time.time)())
    except KeyUpgradeRequired:
        why = "key_upgrade_required " + KEY_MARK
        StopFiles(cfg["stop_dir"], cfg.get("collector_id", "")).write(job, why)
        log("not_started", why="key_upgrade_required")
        print("경고: 2026-12-01 00:00 KST 가 지났는데 자료 암호화 키가 아직 시험 키예요. "
              "config.json crypto.key_source 를 \"dpapi\" 로 바꾸고 keys 로 DATA_KEY 를 넣으세요. 작업 시작 안 함.")
        return None
    except Exception as e:  # noqa: BLE001
        StopFiles(cfg["stop_dir"], cfg.get("collector_id", "")).write(job, "data_key_missing " + type(e).__name__)
        log("not_started", why="data_key_missing")
        print("자료 키를 못 읽었어요(%s). keys 로 DATA_KEY 를 넣으세요. 작업 시작 안 함." % type(e).__name__)
        return None
    if key.is_test:
        log("data_key_test", note=KEY_MARK)
    set_default_key(key)
    return key


def prepare_online(job, spec, cfg, kb, log, ui, key=None):
    """관제기 접속 필수. 성공하면 (client, MemoryPack), 실패하면 STOP 파일(사유)을 남기고 None."""
    from .refpack import start_job, RefPackError
    try:
        return start_job(kb, cfg, spec.get("site_id") or spec.get("site") or job, cfg["state_dir"], log, ui, key=key)
    except RefPackError as e:
        StopFiles(cfg["stop_dir"], cfg.get("collector_id", "")).write(job, "server_required %s %s" % (e.reason, e.detail))
        log("not_started", why=e.reason, detail=e.detail)
        ui("관제기 접속·자료 받기 실패: %s (작업 시작 안 함)" % e.reason, state="stopped")
        print(json.dumps({"end": "server_required", "why": e.reason}, ensure_ascii=False))
        return None


def _rank_rates(bank):
    """조각 그림 파일별 성공률 → {메모리 경로: 성공률} (정리 때 성공률 상위 보호용)."""
    out = {}
    if bank is None:
        return out
    for name, files in (bank.rank or {}).items():
        if name.startswith("__") or not isinstance(files, dict):
            continue
        for fn, r in files.items():
            if isinstance(r, dict):
                ok, fail = r.get("ok", 0), r.get("fail", 0)
                if ok + fail:
                    out["%s/%s" % (bank.mrel, fn)] = ok / float(ok + fail)
    return out


def _is_nas(path):
    p = str(path or "")
    return p.startswith("\\") or p.startswith("//") or (len(p) > 1 and p[0] in "XxYyZz" and p[1] == ":")


if __name__ == "__main__":
    sys.exit(main())
