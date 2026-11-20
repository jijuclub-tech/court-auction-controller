"""Offline tests: independent Gemini model/key choices, authorized AI planning.

Run: python3 -m unittest -v test_independent_ai_policy
No live Gemini requests, real keys, hardware or GitHub Actions.
"""
import base64
import json
import os
import sqlite3
import unittest
from unittest.mock import patch
import independent_ai_policy as a

SITES=[{"site_id":"courtauction","name":"법원경매정보","url":"https://www.courtauction.go.kr/"}]
PLAN={"summary":"자료 목록 확인","site":"법원경매정보","court_or_region":"서울",
      "documents":["appraisal"],"schedule":"once","daily_limit":10,
      "needs_clarification":False,"clarification":"","input_mode":"mouse_only"}

class Handler:
    def __init__(self,db,body=None,admin=False,name="col-test"):
        self.con=db
        self.payload=body or {}
        self.admin=admin
        self.name=name
        self.calls=0
        self.headers={"X-Collector-Token":"fake-device-credential-123"}
        self.client_address=("127.0.0.1",54000)
        self.connection=None
        self.reply=None
    def db_module(self): return self.con
    def _admin(self):return self.admin
    def _body(self):
        self.calls+=1
        return self.payload
    def _collector_ok(self,con,name):
        return con.execute("SELECT * FROM collectors WHERE name=? AND status='approved'",
                           (name,)).fetchone()
    def _json(self,status,data):self.reply=(status,data)

class IndependentAITests(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(":memory:")
        self.con.row_factory=sqlite3.Row
        self.con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY,v TEXT NOT NULL)")
        self.con.execute("""CREATE TABLE collectors(name TEXT PRIMARY KEY,
                         type TEXT,status TEXT,token_hash TEXT NOT NULL)""")
        self.con.execute("INSERT INTO collectors VALUES('col-test','supply','approved','FAKEHASH')")
        self.con.execute("""CREATE TABLE learn_books(site_id TEXT,version INTEGER,status TEXT,
                         trial_collector TEXT,body TEXT)""")
        a.init(self.con,SITES)
        self.con.commit()
        self.addCleanup(self.con.close)
        self.env=patch.dict(os.environ,{"GEMINI_API_KEY":"test-controller-key-not-real",
                     "BIDGUARD_AI_WRAP_KEY":base64.b64encode(b"K"*32).decode()})
        self.env.start();self.addCleanup(self.env.stop)

    def configure(self,**overrides):
        obj=dict(a.DEFAULTS)
        obj.update({"enabled":True,"daily_calls":3})
        obj.update(overrides)
        ret=a._choose_config(self.con,obj)
        self.con.commit()
        return ret

    def test_default_disabled_and_model_catalog(self):
        self.assertEqual(a._settings(self.con)["model"],"gemini-3.1-pro-preview")
        self.assertFalse(a._settings(self.con)["enabled"])
        with self.assertRaises(ValueError):
            a._choose_config(self.con,{**a.DEFAULTS,"model":"unapproved-model"})

    def test_encrypted_key_is_not_plaintext_in_database(self):
        try:
            a._save_secret(self.con,"controller_primary","sk-not-real-very-long-test-key")
        except RuntimeError as exc:
            if "cryptography" in str(exc):
                self.skipTest("cryptography dependency not installed in test environment")
            raise
        value=self.con.execute("SELECT sealed_key FROM independent_ai_key_store").fetchone()[0]
        self.assertNotIn("sk-not-real",value)
        self.assertEqual(a._secret(self.con,"controller_primary"),"sk-not-real-very-long-test-key")
        self.assertTrue(a._key_status(self.con)["controller_primary"])

    def test_unauthorized_admin_rejected(self):
        h=Handler(self.con,admin=False)
        self.assertTrue(a.route(h,"GET","/api/admin/independent-ai",{}))
        self.assertEqual(h.reply[0],403)

    def test_policy_switch_and_options_hide_key(self):
        self.configure(key_source="controller_primary")
        h=Handler(self.con)
        self.assertTrue(a.route(h,"GET","/api/collector/independent-ai/options",
                               {"name":"col-test","site_id":"courtauction"}))
        self.assertEqual(h.reply[0],200)
        self.assertEqual(h.reply[1]["settings"]["model"],"gemini-3.1-pro-preview")
        self.assertIn("courtauction",[x["site_id"] for x in h.reply[1]["sites"]])
        self.assertNotIn("test-controller-key-not-real",str(h.reply))

    def test_no_unauthorized_collector(self):
        self.configure()
        self.con.execute("UPDATE collectors SET token_hash=''")
        h=Handler(self.con)
        a.route(h,"GET","/api/collector/independent-ai/options",{"name":"col-test"})
        self.assertEqual(h.reply[0],403)

    def test_one_read_of_post_body_and_daily_limit(self):
        self.configure(daily_calls=1)
        with patch.object(a,"_call_gemini",return_value=(dict(PLAN),{"total_tokens":50})):
            body={"name":"col-test","site_id":"courtauction","instruction":"서울 경매 감정평가서 조사",
                  "request_key":"request-one-00001"}
            h=Handler(self.con,body)
            a.route(h,"POST","/api/collector/independent-ai/plan",{})
            self.assertEqual(h.calls,1)
            self.assertEqual(h.reply[0],200)
            self.assertEqual(h.reply[1]["plan"]["summary"],"자료 목록 확인")
            h2=Handler(self.con,{**body,"request_key":"request-two-00002"})
            a.route(h2,"POST","/api/collector/independent-ai/plan",{})
            self.assertEqual(h2.reply[0],400)

    def test_central_key_never_returned_from_admin_settings(self):
        h=Handler(self.con,admin=True)
        a.route(h,"GET","/api/admin/independent-ai",{})
        self.assertEqual(h.reply[0],200)
        self.assertIn("key_status",h.reply[1])
        self.assertNotIn("test-controller-key-not-real",str(h.reply))

if __name__=="__main__":
    unittest.main()
