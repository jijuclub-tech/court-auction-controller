"""S-0.3.24 commands (wired into bidguard_cli.py):

  pico-check        Pico 2 S-0.2.14 present? PING build, CAPS, MOVE_CFG (require_arm=1 set), WHERE, cursor. No ARM.
  pico-calibrate    5-point calibration on the desktop (the Pico moves the real cursor; nothing is clicked).
  human-check --step extension|home-hover
                    live test steps the user watches: local test page (one click on its own test button + fake captcha
                    detection, never clicked) / court home page: hover the top menu only, no click.
  requeue-docs      re-download queue for run 958ce62ea901 (looked up in the ledger, table printed first).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .bridge import ExtensionMissing
from .pico_input import HumanTakeover, PicoMissing
from .setup import CALIB, CalibrationMissing, HumanSession, pico_settings


def _say(m):
    print(m, file=sys.stderr, flush=True)


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False, default=str))


def _countdown(n, what):
    for i in range(int(n), 0, -1):
        _say(f'  {what} in {i}s ... (hands off the mouse and keyboard)')
        time.sleep(1)


def _alarm(screen, kind='human'):
    try:
        from core.alerts import Alerts
        Alerts().human_needed('BLOCK' if kind != 'captcha' else 'CAPTCHA', 'S-0.3.24 setup problem')
    except Exception:  # noqa: BLE001
        if screen is not None:
            screen.beep([(1200, 300), (0, 150), (1200, 300)])


def cmd_pico_check(a, cfg):
    s = HumanSession(cfg)
    try:
        inp = s.open_pico(need_calibration=False)
        lk = inp.link
        out = {'ok': True, 'port': lk.port, 'build': lk.version, 'caps': list(lk.caps), 'move_cfg': inp.move_cfg,
               'where': lk.where(), 'cursor': s.screen.cursor(), 'screen': s.screen.screen_size(),
               'dpi_scale': s.screen.dpi_scale(), 'monitors': s.screen.monitors(),
               'settings': pico_settings('.'), 'calibration': str(CALIB) if Path(CALIB).exists() else None}
        _say(f"[pico] {lk.port} {lk.version} require_arm={inp.move_cfg.get('require_arm')} caps={','.join(lk.caps)}")
        _emit(out)
        return 0
    except PicoMissing as e:
        _alarm(s.screen)
        _emit({'ok': False, 'error': 'PICO_MISSING: %s' % e})
        return 2
    except Exception as e:  # noqa: BLE001
        _emit({'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e)[:300])})
        return 1
    finally:
        s.close()


def cmd_pico_calibrate(a, cfg):
    from .calibration import run_calibration
    s = HumanSession(cfg)
    try:
        inp = s.open_pico(need_calibration=False)
        _say('[calibrate] The Pico 2 will move the mouse pointer to 5 points (4 corners + centre) and 1 check point.')
        _say('[calibrate] Nothing is clicked. Do not touch the mouse until it says DONE.')
        _countdown(a.delay, 'calibration starts')
        tol = float(a.tol)
        with inp.session('calibrate'):
            cal, meta = run_calibration(inp, s.screen, tol_px=tol, say=_say)
        _say(f"[calibrate] residual max {meta['residual_max_px']} px, check point error {meta['verify']['error_px']} px "
             f"(limit {tol}) -> {'OK' if meta['ok'] else 'NOT OK'}")
        if meta['ok']:
            try:
                from .pico_profiles import save_profile, profile_key
                dest = save_profile(cal, s.screen, '.')
                _say(f'[calibrate] DONE - saved profile {dest} ({profile_key(s.screen)}) and {CALIB}')
            except Exception as e:  # noqa: BLE001
                cal.save(CALIB)
                _say(f'[calibrate] profile save failed ({type(e).__name__}); saved {CALIB} only')
        else:
            Path(CALIB).with_suffix('.failed.json').write_text(json.dumps(cal.to_dict(), indent=1), encoding='utf-8')
            _say('[calibrate] NOT saved - run it again (Windows display scale / resolution must not change)')
        _emit({'ok': meta['ok'], **meta})
        return 0 if meta['ok'] else 1
    except HumanTakeover as e:
        _emit({'ok': False, 'error': 'mouse touched during calibration: %s' % e})
        return 1
    except PicoMissing as e:
        _alarm(s.screen)
        _emit({'ok': False, 'error': 'PICO_MISSING: %s' % e})
        return 2
    except Exception as e:  # noqa: BLE001
        _emit({'ok': False, 'error': '%s: %s' % (type(e).__name__, str(e)[:300])})
        return 1
    finally:
        s.close()


def _focus_hint(screen, wait):
    ok, fg = screen.chrome_foreground()
    end = time.time() + wait
    while not ok and time.time() < end:
        _say('  -> click once on the Chrome window title bar yourself (Chrome must be in front)')
        time.sleep(2)
        ok, fg = screen.chrome_foreground()
    return ok


def cmd_human_check(a, cfg):
    s = HumanSession(cfg)
    res = {'ok': False, 'step': a.step, 'checks': []}

    def chk(name, ok, detail=''):
        res['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
        _say(f"  [{'PASS' if ok else 'FAIL'}] {name} {detail}")
        return ok
    try:
        d = s.start(need_calibration=True)
        chk('pico S-0.2.14 + require_arm=1', s.inp.move_cfg.get('require_arm') == 1, s.inp.link.version)
        chk('extension connected', True, json.dumps(getattr(s.bridge, 'ext_info', {})))
        if not _focus_hint(s.screen, 60):
            chk('Chrome in front', False)
            _emit(res)
            return 1
        _countdown(a.delay, 'the Pico starts')
        if a.step == 'extension':
            url = 'http://127.0.0.1:%d/test/index.html' % int((cfg.get('human_chrome') or {}).get('bridge_port', 17321))
            d.open(url)
            info = d._top_result('info') or {}
            chk('test page open', 'test/index.html' in info.get('url', ''), info.get('url', ''))
            chk('navigator.webdriver is false', info.get('webdriver') is False)
            d.check_zoom()
            chk('zoom 100% (devicePixelRatio = Windows scale)', True, str(info.get('dpr')))
            t, m = d._first(['id:hover'])
            d.inp.act(lambda: (d._point_at(m, 'hover test', verify=False), d.inp.hover(800)), 'hover test')
            chk('hover (no click)', True)
            d.click(['id:btn'], 'test button')
            time.sleep(0.6)
            txt = (d._top_result('text') or '')
            chk('click arrived as a real (trusted) click', 'clicked isTrusted=true' in txt,
                next((ln for ln in txt.splitlines() if 'clicked' in ln), ''))
            st = d.captcha_park()
            chk('fake captcha found inside the nested frame', st.get('found'), json.dumps(st.get('anchor'), default=str))
            chk('pointer parked NEXT TO it (not clicked)', bool(st.get('parked_at')), str(st.get('parked_at')))
            v = d.verifier.last or {}
            chk('screenshot check before the click', v.get('ok') is not False, str(v.get('shot')))
        elif a.step == 'home-hover':
            home = cfg['home_url']
            d.open(home)
            d.wait_present(cfg.get('home_ready', ['id:mf_wfm_header_anc_auctnGdsMain']), 60, 'home')
            chk('court home page open', True, d.url()[:80])
            d.check_zoom()
            pops = d._top_result('popups') or []
            if pops:
                chk('notice popups (left open in this step - no click)', True, ','.join(pops))
            step = next((x for x in cfg['routes'][cfg.get('default_route')]['steps'] if x.get('do') == 'menu'), None)
            tt, top, note = d._find_onscreen(step['top'])
            if top is None:
                d.scroll_to_top()
                tt, top, note = d._find_onscreen(step['top'])
            if not chk('top menu item on screen', top is not None, note):
                _emit(res)
                return 1
            d.inp.act(lambda: (d._point_at(top, 'hover top menu', verify=False), d.inp.hover(1200)), 'hover top menu')
            t, m = d._wait_visible(step['target'], 3)
            chk('menu opened by hover (nothing clicked)', m is not None, t or '')
            snap = d._snap()
            info = snap['frames'][0]['info']
            d.inp.act(lambda: d.inp.move_px(*d.to_px(snap, 0, info['vw'] * 0.85, info['vh'] * 0.8)), 'move away')
        res['ok'] = all(c['ok'] for c in res['checks'])
        _say('[human-check] ' + ('ALL PASS' if res['ok'] else 'SOME CHECKS FAILED'))
        _emit(res)
        return 0 if res['ok'] else 1
    except (PicoMissing, ExtensionMissing, CalibrationMissing) as e:
        _alarm(s.screen)
        res['error'] = '%s: %s' % (type(e).__name__, e)
        _emit(res)
        return 2
    except Exception as e:  # noqa: BLE001
        res['error'] = '%s: %s' % (type(e).__name__, str(e)[:300])
        _emit(res)
        return 1
    finally:
        s.close()


def cmd_requeue_docs(a, cfg):
    from . import requeue as RQ
    data_dir = Path(a.data_dir)
    run_id = a.run_id
    st = Path(a.state_dir) / 'court_live_longrun.json'
    cur = None
    try:
        cur = json.loads(st.read_text(encoding='utf-8')).get('run_id')
    except (OSError, ValueError):
        pass
    kinds_all = [k['kind'] for k in cfg.get('documents', {}).get('kinds', [])] or ['appraisal', 'sale_spec', 'survey']
    groups = [('captcha', a.captcha_tokens.split(',') if a.captcha_tokens else RQ.CAPTCHA_FAILED, ['appraisal']),
              ('first_run', a.first_tokens.split(',') if a.first_tokens else RQ.FIRST_RUN, None)]
    rows, items = RQ.build(data_dir, groups, kinds_all)
    _say(RQ.table(rows))
    found = sum(1 for r in rows if r['found'])
    missing = [r['token'] for r in rows if not r['found']]
    _say(f'[requeue] tokens matched: {found}; not found: {missing or "-"}; items to queue: {len(items)}')
    if cur and cur != run_id:
        _say(f'[requeue] note: the current long-run state is {cur}, not {run_id} - entries are written for {run_id}; '
             f'start the long run with --resume-run {run_id}')
    out = {'ok': True, 'run_id': run_id, 'current_run': cur, 'matched': found, 'not_found': missing,
           'queued': [{'key': e['key'], 'kinds': e['kinds'], 'groups': e['groups']} for e in items]}
    if not a.dry_run and items:
        p = RQ.write(data_dir, run_id, items)
        out['written'] = str(p)
        _say(f'[requeue] written {p} and doc_retry.json (backups *.bak_<time>)')
    _emit(out)
    return 0
