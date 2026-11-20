"""Offline tests for controller exploration versioning and remote authentication.

No real collector, Gemini, NAS, Drive or public website access required.
Run: python3 -m unittest -v test_exploration_registry
"""
import hashlib
import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import exploration_registry as e

SITE={"site_id":"courtauction","name":"법원경매정보",
      "url":"https://www.courtauction.go.kr/"}
TOKEN="fake-collector-authorization-code"
def snapshot(page="search",structure="first"):
    return {"url":"https://www.courtauction.go.kr/search",
            "captured_at":datetime.now(timezone.utc).isoformat(),
            "page_kind":page,"structure_sha256":hashlib.sha256(structure.encode()).hexdigest(),
            "elements":[{"name":"search","selector":"#search"},
                        {"name":"results","selector":".results"}]}

class Handler:
    def __init__(self,con,admin=False,name="independent-1",token=TOKEN):
        self.con=con
        self.headers={"X-Collector-Token":token}
        self.client_address=("127.0.0.1",4141)
        self.connection=None
        self.admin=admin
        self.reply=None
        self.payload={}
    def db_module(self):return self.con
    def _admin(self):return self.admin
    def _json(self,status,body):self.reply=(status,body)
    def _body(self):return self.payload

class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.con=sqlite3.connect(":memory:")
        self.con.row_factory=sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        self.con.execute("CREATE TABLE kv(k TEXT PRIMARY KEY,v TEXT)")
        self.con.execute("""CREATE TABLE collectors
            (name TEXT PRIMARY KEY,status TEXT,token_hash TEXT,type TEXT)""")
        self.con.execute("INSERT INTO collectors VALUES(?,?,?,?)",
            ("independent-1","approved",hashlib.sha256(TOKEN.encode()).hexdigest(),"supply"))
        e.init(self.con,[SITE])
        self.con.commit()
        self.addCleanup(self.con.close)

    def submit(self,structure="first",request_id="test-request-00001"):
        return e.propose(self.con,"independent-1",request_id,
                         "courtauction",snapshot(structure=structure))

    def test_new_proposal_requires_admin_before_publication(self):
        p=self.submit()
        self.assertEqual(p["status"],"PENDING")
        with patch.object(e.learning,"for_visit",return_value={"version":0,"book":None}):
            data=e.sync(self.con,"independent-1","courtauction")
        self.assertEqual(data["version"],0)
        self.assertNotIn("navigation",data)
        review=e.decide(self.con,p["proposal_id"],"approve")
        self.assertEqual(review["version"],1)
        with patch.object(e.learning,"for_visit",return_value={"version":0,"book":None}):
            current=e.sync(self.con,"independent-1","courtauction")
            same=e.sync(self.con,"independent-1","courtauction",have_version=1)
        self.assertEqual(current["navigation"]["page_kind"],"search")
        self.assertEqual(current["sha256"],hashlib.sha256(e._canonical(
                         current["navigation"])).hexdigest())
        self.assertTrue(same["unchanged"])
        self.assertNotIn("navigation",same)

    def test_updates_keep_earlier_approved_version(self):
        first=self.submit()
        e.decide(self.con,first["proposal_id"],"approve")
        newer=self.submit(structure="second",request_id="test-request-00002")
        e.decide(self.con,newer["proposal_id"],"approve")
        rows=self.con.execute("SELECT version FROM exploration_versions ORDER BY version").fetchall()
        self.assertEqual([r[0] for r in rows],[1,2])
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("DELETE FROM exploration_versions")
        self.con.rollback()

    def test_same_navigation_new_timestamp_does_not_churn_versions(self):
        initial=self.submit()
        e.decide(self.con,initial["proposal_id"],"approve")
        later=snapshot()
        later["captured_at"]=datetime.now(timezone.utc).isoformat()
        second=e.propose(self.con,"independent-1","test-request-00003",
                         "courtauction",later)
        reviewed=e.decide(self.con,second["proposal_id"],"approve")
        self.assertEqual(reviewed["version"],1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM exploration_versions").fetchone()[0],1)

    def test_idempotency_and_site_key_reuse(self):
        first=self.submit()
        again=self.submit()
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["proposal_id"],first["proposal_id"])
        with self.assertRaises(ValueError):
            self.submit(structure="different",request_id="test-request-00001")
        self.assertEqual(self.con.execute("SELECT count(*) FROM exploration_proposals").fetchone()[0],1)

    def test_bad_snapshot_cannot_be_proposed(self):
        invalid=[
          {**snapshot(),"password":"test"},
          {**snapshot(),"url":"http://www.courtauction.go.kr/search"},
          {**snapshot(),"url":"https://outside.example.com/search"},
          {**snapshot(),"url":"https://www.courtauction.go.kr/search?session=secret"},
          {**snapshot(),"elements":[{"name":"other","selector":"input[name=password]"}]},
        ]
        for n,s in enumerate(invalid):
            with self.subTest(n=n),self.assertRaises(ValueError):
                e.propose(self.con,"independent-1","test-request-%05d"%n,
                          "courtauction",s)

    def test_admin_permissions_and_registered_collector_token(self):
        h=Handler(self.con)
        h.payload={"name":"independent-1","request_key":"test-auth-req-0001",
                   "site_id":"courtauction","snapshot":snapshot()}
        e.route(h,"POST","/api/collector/exploration/propose",{})
        self.assertEqual(h.reply[0],200)
        h.headers["X-Collector-Token"]="wrong-token-123456"
        e.route(h,"GET","/api/collector/exploration/catalog",{"name":"independent-1"})
        self.assertEqual(h.reply[0],403)
        h.headers["X-Collector-Token"]=TOKEN
        e.route(h,"GET","/api/admin/exploration/state",{})
        self.assertEqual(h.reply[0],403)
        h.admin=True
        e.route(h,"GET","/api/admin/exploration/state",{})
        self.assertEqual(h.reply[0],200)
        self.assertEqual(len(h.reply[1]["pending"]),1)
        h.client_address=("198.51.100.2",4444)
        e.route(h,"GET","/api/admin/exploration/state",{})
        self.assertEqual(h.reply[0],403)

    def test_catalog_includes_sites_with_learning_only(self):
        site=e.catalog(self.con)[0]
        self.assertEqual(site["site_id"],"courtauction")
        self.assertEqual(site["version"],0)

if __name__=="__main__":
    unittest.main()
