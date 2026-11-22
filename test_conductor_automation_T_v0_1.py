"""합성 자료만 쓰는 시험. 외부 서비스·GitHub·NAS·실제 봇에 접근하지 않는다."""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

import conductor_automation_T_v0_1 as ca

KST = ca.KST


def at(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=KST)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ops = Path(self.tmp.name) / "ops"
        self.ops.mkdir()
        self.con = sqlite3.connect(str(Path(self.tmp.name) / "c.db"))
        self.con.row_factory = sqlite3.Row
        ca.init(self.con)
        self.bots = ["grok-%02d" % i for i in range(1, 61)]
        self.w("bots.json", {"bots": self.bots})
        self._fetch = ca.http_get_json
        ca.http_get_json = self._no_network

    @staticmethod
    def _no_network(url, headers):
        raise OSError("network disabled in tests")

    def tearDown(self):
        ca.http_get_json = self._fetch
        self.con.close()
        self.tmp.cleanup()

    def w(self, rel, obj):
        p = self.ops / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def verify(self, *names):
        for n in names:
            ca.set_task(self.con, {"task": n, "verified": True, "note": "시험"}, at(2026, 11, 23))

    def runs(self, task=None):
        q = "SELECT actor,mode,task,slot,ok,attempt,date,parity FROM auto_runs"
        return [dict(r) for r in self.con.execute(q + (" WHERE task=?" if task else "") + " ORDER BY id",
                                                  (task,) if task else ())]


class ParityTests(Base):
    def test_parity_and_actor(self):
        t = ca.task_row(self.con, "ai_usage_compare")
        self.assertEqual(ca.parity(date(2026, 11, 23)), "odd")
        self.assertEqual(ca.decide(self.con, t, date(2026, 11, 23))[0], "conductor")
        # 짝수일, 실기 확인 전 -> 지휘자 대행 + 관제기 그림자
        self.assertEqual(ca.decide(self.con, t, date(2026, 11, 24))[:2], ("conductor_fallback", "shadow"))
        self.verify("ai_usage_compare")
        t = ca.task_row(self.con, "ai_usage_compare")
        self.assertEqual(ca.decide(self.con, t, date(2026, 11, 24))[:2], ("controller", "live"))
        # 정한영 지시 예외는 짝수일에도 지휘자
        ca.add_exemption(self.con, {"date": "2026-11-24", "task": "*", "note": "정한영 직접 지시"}, "admin",
                         at(2026, 11, 24))
        self.assertEqual(ca.decide(self.con, t, date(2026, 11, 24))[:2][0], "conductor")

    def test_month_boundary_31_then_1_both_odd(self):
        self.assertEqual(ca.parity(date(2026, 12, 31)), "odd")
        self.assertEqual(ca.parity(date(2027, 1, 1)), "odd")

    def test_verify_requires_note(self):
        with self.assertRaises(ValueError):
            ca.set_task(self.con, {"task": "ai_usage_compare", "verified": True}, at(2026, 11, 23))


class ScheduleTests(Base):
    def test_slots(self):
        s = ca.slots_between({"kind": "every", "hours": 2, "minute": 5}, at(2026, 11, 24), at(2026, 11, 24, 23, 59))
        self.assertEqual(len(s), 12)
        s = ca.slots_between({"kind": "weekly", "weekday": 6, "at": "09:00"}, at(2026, 11, 23), at(2026, 12, 6, 23))
        self.assertEqual([x.date() for x in s], [date(2026, 11, 29), date(2026, 12, 6)])
        s = ca.slots_between({"kind": "daily", "at": "10:00", "from": "2026-11-23", "to": "2026-11-27"},
                             at(2026, 11, 20), at(2026, 11, 30, 23))
        self.assertEqual(len(s), 5)

    def test_odd_day_controller_does_nothing(self):
        out = ca.tick(self.con, at(2026, 11, 23, 18, 1), self.ops)
        self.assertEqual([r for r in out if r["task"] != "parity_compare_report"], [])

    def test_even_unverified_runs_shadow_without_orders(self):
        self.w("ai_usage/2026-11-24/grok-01.json", {"ai_calls": 5})
        out = ca.tick(self.con, at(2026, 11, 24, 18, 1), self.ops)
        r = [x for x in out if x["task"] == "ai_usage_compare"][0]
        self.assertEqual(r["mode"], "shadow")
        self.assertTrue(r["ok"])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM bot_orders").fetchone()[0], 0)
        self.assertTrue(list((self.ops / "out" / "shadow" / "ai_usage_compare").glob("*/orders_preview.json")))

    def test_even_verified_live_and_not_twice(self):
        self.verify("ai_usage_compare")
        ca.tick(self.con, at(2026, 11, 24, 18, 1), self.ops)
        ca.tick(self.con, at(2026, 11, 24, 18, 2), self.ops)
        rows = self.runs("ai_usage_compare")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["actor"], rows[0]["mode"], rows[0]["parity"]), ("controller", "live", "even"))
        self.assertGreater(self.con.execute("SELECT COUNT(*) FROM bot_orders").fetchone()[0], 0)
        self.assertTrue((self.ops / "queue" / "grok-01.json").exists())

    def test_retry_with_backoff_then_give_up(self):
        (self.ops / "bots.json").unlink()        # 명단이 없으면 실패
        self.verify("ai_usage_compare")
        t0 = at(2026, 11, 24, 18, 0)
        ca.tick(self.con, t0, self.ops)
        ca.tick(self.con, t0 + timedelta(minutes=2), self.ops)   # 아직 대기
        ca.tick(self.con, t0 + timedelta(minutes=6), self.ops)   # 2회
        ca.tick(self.con, t0 + timedelta(minutes=30), self.ops)  # 3회
        ca.tick(self.con, t0 + timedelta(minutes=120), self.ops)  # 소진
        rows = self.runs("ai_usage_compare")
        self.assertEqual([r["attempt"] for r in rows], [1, 2, 3])
        self.assertTrue(all(r["ok"] == 0 for r in rows))

    def test_stuck_run_is_retried(self):
        self.verify("ai_usage_compare")
        self.con.execute("""INSERT INTO auto_runs(run_id,date,parity,actor,mode,task,slot,attempt,started)
            VALUES('x','2026-11-24','even','controller','live','ai_usage_compare','2026-11-24T18:00',1,?)""",
                         (ca.iso(at(2026, 11, 24, 18, 0)),))
        self.assertEqual([r for r in ca.tick(self.con, at(2026, 11, 24, 18, 10), self.ops)
                          if r["task"] == "ai_usage_compare"], [])
        out = ca.tick(self.con, at(2026, 11, 24, 18, 40), self.ops)
        self.assertEqual([r["attempt"] for r in out if r["task"] == "ai_usage_compare"], [2])


class HandlerTests(Base):
    def test_ai_usage_penalties(self):
        self.w("bots.json", {"bots": ["a1", "a2", "a3", "a4"]})
        for b, prev, cur in (("a1", 10, 5), ("a2", 10, 10), ("a3", 10, 8)):
            self.w("ai_usage/2026-11-23/%s.json" % b, {"ai_calls": prev})
            self.w("ai_usage/2026-11-24/%s.json" % b, {"ai_calls": cur})
        self.verify("ai_usage_compare")
        ca.tick(self.con, at(2026, 11, 24, 18, 1), self.ops)
        out = next((self.ops / "out" / "live" / "ai_usage_compare").glob("*/summary.json"))
        rows = {r["bot"]: r["penalties"] for r in json.loads(out.read_text(encoding="utf-8"))["rows"]}
        self.assertEqual(rows["a4"], ["미보고"])
        self.assertEqual(rows["a2"], ["미감소", "최하위"])
        self.assertEqual(rows["a1"], [])
        self.assertTrue(next((self.ops / "out" / "live" / "ai_usage_compare").glob("*/chart.svg")).exists())

    def test_health_check(self):
        self.w("bots.json", {"bots": ["b1", "b2", "b3"]})
        self.w("bots/b1/status.json", {"updated_at": ca.iso(at(2026, 11, 24, 9, 0))})
        self.w("bots/b2/status.json", {"updated_at": ca.iso(at(2026, 11, 24, 3, 0)), "state": "blocked"})
        (self.ops / "bots" / "b1" / "stop.flag").write_text("x")
        self.verify("bot_health_check")
        ca.tick(self.con, at(2026, 11, 24, 10, 5), self.ops)
        h = json.loads(next((self.ops / "out" / "live" / "bot_health_check").glob("*/health.json")).read_text())
        alerts = {a["bot"]: a["issues"] for a in h["alerts"]}
        self.assertIn("flag: stop.flag", alerts["b1"])
        self.assertTrue(any(i.startswith("막힘") for i in alerts["b2"]))
        self.assertTrue(any("지남" in i for i in alerts["b2"]))
        self.assertEqual(alerts["b3"], ["상태파일 없음"])

    def test_regional_news_assign_score_rework(self):
        regions = ["대전", "세종", "천안", "공주"]
        self.w("news/regions.json", {"targets": regions})
        self.verify("regional_news_assign", "regional_news_score")
        sunday = date(2026, 11, 29)
        self.assertEqual(ca.parity(sunday), "odd")  # 11/29는 홀수일 -> 관제기는 안 함
        sunday = date(2026, 12, 6)                   # 짝수 일요일
        ca.tick(self.con, at(2026, 12, 6, 9, 1), self.ops)
        plan = json.loads((self.ops / "news/regional/2026-12-06/assignment.json").read_text())["plan"]
        self.assertEqual(len(plan), 60)
        self.assertTrue(all(plan[b] for b in self.bots))
        good = {"items": [{"title": "t%d" % i, "url": "https://n.example/%d" % i, "published": "2026-12-05",
                           "summary": "가" * 45, "region": plan["grok-01"][0]} for i in range(10)]}
        self.w("news/regional/2026-12-06/grok-01.json", good)
        self.w("news/regional/2026-12-06/grok-02.json", {"items": good["items"][:2]})
        ca.tick(self.con, at(2026, 12, 6, 20, 1), self.ops)
        sc = json.loads(next((self.ops / "out/live/regional_news_score").glob("*/scores.json")).read_text())
        by = {r["bot"]: r for r in sc["rows"]}
        self.assertEqual(by["grok-01"]["score"], 100.0)
        self.assertFalse(by["grok-01"]["rework"])
        self.assertTrue(by["grok-02"]["rework"])
        self.assertTrue(by["grok-03"]["rework"])
        n = self.con.execute("SELECT COUNT(*) FROM bot_orders WHERE kind='news_rework'").fetchone()[0]
        self.assertEqual(n, 59)

    def test_world_news_press_list(self):
        sub = {"items": [{"title": "x", "url": "https://a", "published": "2026-11-30", "summary": "s" * 50,
                          "country": "Japan"}] * 1,
               "press": [{"name": "NHK", "url": "https://nhk", "country": "Japan"}]}
        total, parts = ca.score_submission("world", sub, ["Japan", "Korea"], date(2026, 11, 30),
                                           ca._news_cfg("world"))
        self.assertEqual(parts["press_list"], 5.0)
        self.assertLess(total, 70)

    def test_auction_price_fill(self):
        self.w("auction/sold_items.json", {"items": [
            {"court": "대전", "case_no": "2025타경1", "item_no": "1"},
            {"court": "대전", "case_no": "2025타경2", "item_no": "1"},
            {"court": "대전", "case_no": "2025타경3", "item_no": "1", "winning_bid": 9}]})
        self.w("auction/price_results/g1.json", {"items": [
            {"court": "대전", "case_no": "2025타경1", "item_no": "1", "winning_bid": 123000000, "source": "법원"}]})
        self.verify("auction_price_fill")
        ca.tick(self.con, at(2026, 11, 24, 10, 1), self.ops)
        m = json.loads((self.ops / "auction/sold_items_filled.json").read_text())["items"]
        self.assertEqual(m[0]["winning_bid"], 123000000)
        self.assertNotIn("winning_bid", m[1])
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM bot_orders WHERE task='auction_price_fill'")
                         .fetchone()[0], 1)
        # 기간 밖(11/28)에는 일정이 없다
        self.assertEqual(ca.slots_between(ca.task_row(self.con, "auction_price_fill")["schedule"],
                                          at(2026, 11, 28), at(2026, 11, 28, 23)), [])

    def test_issue_digest_with_fake_fetch(self):
        calls = []

        def fake(url, headers):
            calls.append(url)
            return [{"body": "grok-07 이 #42 국가 목록 다시 확인", "created_at": "2026-11-24T01:00:00Z",
                     "html_url": "https://github.com/x/y/issues/42#c1", "issue_url": "https://api/x/42",
                     "user": {"login": "jijuclub-tech"}},
                    {"body": "일반 답변", "created_at": "2026-11-24T01:05:00Z",
                     "html_url": "https://github.com/x/y/issues/43#c2", "issue_url": "https://api/x/43",
                     "user": {"login": "someone"}}]
        self.verify("issue_reply_digest")
        out = ca.tick(self.con, at(2026, 11, 24, 10, 21), self.ops, fetch=fake)
        r = [x for x in out if x["task"] == "issue_reply_digest"][0]
        self.assertEqual(r["items"], 2)
        bots = [x[0] for x in self.con.execute("SELECT bot FROM bot_orders WHERE task='issue_reply_digest'")]
        self.assertEqual(bots, ["grok-07"])
        # 다음 회차에는 같은 댓글을 다시 넣지 않는다
        out = ca.tick(self.con, at(2026, 11, 24, 11, 21), self.ops, fetch=fake)
        self.assertEqual([x["items"] for x in out if x["task"] == "issue_reply_digest"], [0])


class QueueAndReportTests(Base):
    def test_pull_ack(self):
        t = at(2026, 11, 24, 10)
        oid = ca.enqueue_order(self.con, "grok-01", "x", "s", "k", {"a": 1}, t + timedelta(hours=1), t)
        self.assertEqual(ca.enqueue_order(self.con, "grok-01", "x", "s", "k", {"a": 1}, t + timedelta(hours=1), t), oid)
        got = ca.pull_orders(self.con, "grok-01", t)
        self.assertEqual([o["order_id"] for o in got], [oid])
        self.assertEqual(ca.ack_order(self.con, "grok-02", {"order_id": oid, "outcome": "done"}, t)[0], 404)
        self.assertEqual(ca.ack_order(self.con, "grok-01", {"order_id": oid, "outcome": "done"}, t)[0], 200)
        self.assertEqual(ca.pull_orders(self.con, "grok-01", t), [])
        self.assertEqual(ca.ack_order(self.con, "grok-01", {"order_id": oid, "outcome": "failed"}, t)[0], 409)
        # 만료된 지시는 내려가지 않는다
        ca.enqueue_order(self.con, "grok-01", "x", "s2", "k", {}, t + timedelta(minutes=1), t)
        self.assertEqual(ca.pull_orders(self.con, "grok-01", t + timedelta(hours=1)), [])

    def test_conductor_log_and_compare(self):
        self.verify("ai_usage_compare")
        # 11/23(홀) 지휘자 기록, 11/24(짝) 관제기 실행, 11/25(홀) 놓침
        r = ca.log_conductor_run(self.con, {"actor": "conductor", "task": "ai_usage_compare",
                                            "started": "2026-11-23T18:00:00+09:00",
                                            "finished": "2026-11-23T18:20:00+09:00", "ok": True,
                                            "items": 60, "ai_steps": 4}, at(2026, 11, 23, 19))
        self.assertTrue(r["rule_ok"])
        self.assertEqual(r["slot"], "2026-11-23T18:00")
        ca.tick(self.con, at(2026, 11, 24, 18, 1), self.ops)
        # 짝수일에 지휘자가 이유 없이 하면 규칙 위반 표시
        r2 = ca.log_conductor_run(self.con, {"actor": "conductor", "task": "ai_usage_compare",
                                             "started": "2026-11-24T18:30:00+09:00",
                                             "finished": "2026-11-24T18:40:00+09:00", "ok": True},
                                  at(2026, 11, 24, 19))
        self.assertFalse(r2["rule_ok"])
        rep = ca.compare_report(self.con, date(2026, 11, 23), date(2026, 11, 25), at(2026, 11, 25, 23, 59))
        row = [x for x in rep["tasks"] if x["task"] == "ai_usage_compare"][0]
        self.assertEqual(row["odd"]["expected"], 2)
        self.assertEqual(row["odd"]["missed"], 1)
        self.assertEqual(row["odd"]["success_rate"], 50.0)
        self.assertEqual(row["odd"]["avg_seconds"], 1200.0)
        self.assertEqual(row["odd"]["avg_ai_steps"], 4)
        self.assertEqual(row["even"]["success_rate"], 100.0)
        self.assertEqual(row["even"]["avg_ai_steps"], 0)
        self.assertEqual(row["rule_violations"], 1)
        self.assertIn("ai_usage_compare", ca.report_markdown(rep))

    def test_log_rejects_bad_actor(self):
        with self.assertRaises(ValueError):
            ca.log_conductor_run(self.con, {"actor": "controller", "task": "ai_usage_compare",
                                            "started": "2026-11-23T18:00:00+09:00",
                                            "finished": "2026-11-23T18:01:00+09:00"}, at(2026, 11, 23))


class HttpRouteTests(Base):
    class H:
        def __init__(self, con, path_headers, body=None, admin=False, bot=True):
            self.headers = path_headers
            self._b = body or {}
            self.out = None
            self._is_admin, self._is_bot = admin, bot
            self.con = con
            self.connection = None
            self.client_address = ("127.0.0.1", 1)

        def _json(self, code, obj):
            self.out = (code, obj)

        def _admin(self):
            return self._is_admin

        def _bot(self):
            return self._is_bot

        def _body(self):
            return self._b

        def _site_map_db(self):
            con = self.con

            class W:
                def __enter__(s):
                    return con

                def __exit__(s, *a):
                    con.commit()
            return W()

    def test_routes_require_https_and_auth(self):
        import os
        os.environ.pop("CONTROLLER_MAP_ALLOW_HTTP_LOOPBACK", None)
        h = self.H(self.con, {})
        self.assertTrue(ca.route(h, "GET", "/auto/today", {}))
        self.assertEqual(h.out[0], 403)
        h = self.H(self.con, {"X-Forwarded-Proto": "https"}, bot=False)
        ca.route(h, "GET", "/auto/today", {})
        self.assertEqual(h.out[0], 403)
        h = self.H(self.con, {"X-Forwarded-Proto": "https"})
        ca.route(h, "GET", "/auto/admin/status", {})
        self.assertEqual(h.out[0], 403)
        h = self.H(self.con, {"X-Forwarded-Proto": "https"})
        ca.route(h, "GET", "/auto/today", {})
        self.assertEqual(h.out[0], 200)
        self.assertFalse(ca.route(h, "GET", "/api/health", {}))


if __name__ == "__main__":
    unittest.main()
