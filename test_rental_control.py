"""Offline regression tests for the rental controller. No live devices needed."""
import json
import sqlite3
import time
import unittest
from datetime import datetime, timezone
import collector_dispatch
import rental_control as rent

ROUTES=[{"route_id":"court_appraisal","name":"감정평가서 자료"},{"route_id":"court_results","name":"매각결과 검색"}]

class RentalControlTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.execute("CREATE TABLE kv(k TEXT PRIMARY KEY,v TEXT)")
        self.db.execute("""CREATE TABLE collectors(name TEXT PRIMARY KEY,type TEXT,status TEXT,token_hash TEXT,
         first_seen TEXT,last_seen TEXT,last_state TEXT,version TEXT)""")
        collector_dispatch.init(self.db)
        rent.init(self.db,ROUTES)
        self.db.execute("INSERT INTO collectors(name,type,status,token_hash,last_seen,last_state) VALUES(?,?,?,?,?,?)",
          ("c1","control","approved","sha256-of-device-token",datetime.now(timezone.utc).isoformat(timespec="seconds"),
           json.dumps({"state":"ready","rental_enabled":True})))
        self.db.commit()
        self.token=rent.create_member(self.db,"member1")["access_code"]
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def request(self,mode="one_time",hours=None):
        b={"route_id":"court_appraisal","mode":mode}
        if hours is not None:b["hours"]=hours
        job=rent.request(self.db,"member1",b)
        self.db.commit()
        return job

    def approve(self,job):
        rent.change(self.db,job["job_id"],"approve")
        self.db.commit()

    def test_member_token_is_hashed(self):
        self.assertEqual(rent._member(self.db,"Bearer "+self.token),"member1")
        self.assertIsNone(rent._member(self.db,"Bearer wrong"))
        self.assertNotIn(self.token,str(self.db.execute("SELECT * FROM rental_members").fetchone()[:]))

    def test_controller_priority_over_poll_order(self):
        import json as _json
        self.db.execute("INSERT INTO collectors(name,type,status,token_hash,last_seen,last_state) VALUES(?,?,?,?,?,?)",
          ("c2","control","approved","sha256-of-other-token",
           datetime.now(timezone.utc).isoformat(timespec="seconds"),
           _json.dumps({"state":"ready","rental_enabled":True})))
        self.db.execute("INSERT INTO kv(k,v) VALUES('rental_priority',?)",
                        (_json.dumps({"c1":1,"c2":9}),))
        self.db.commit()
        j=self.request()
        self.approve(j)
        self.assertEqual(rent.poll(self.db,"c1")["action"],"WAIT")
        assigned=rent.poll(self.db,"c2")
        self.assertEqual(assigned["action"],"RUN")
        self.assertEqual(assigned["job"]["collector"],"c2")

    def test_reservation_limit_and_manual_approval(self):
        with self.assertRaises(ValueError):
            self.request("reserved",13)
        j=self.request("reserved",12)
        self.assertEqual(j["duration_limit"],43200)
        self.assertEqual(j["status"],"AWAITING_APPROVAL")
        self.assertEqual(rent.poll(self.db,"c1")["action"],"WAIT")
        self.approve(j)
        p=rent.poll(self.db,"c1")
        self.assertEqual(p["action"],"RUN")
        self.assertEqual(p["job"]["route_id"],"court_appraisal")

    def test_primary_job_has_priority(self):
        j=self.request()
        self.approve(j)
        now=int(time.time())
        self.db.execute("""INSERT INTO central_jobs
        (job_id,work_key,source_id,court_id,case_no,item_no,doc_type,doc_version,target_name,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
          ("primary","unique-primary","court","seoul","case1","1","appraisal","1",None,now,now))
        self.db.commit()
        self.assertEqual(rent.poll(self.db,"c1")["action"],"WAIT")
        self.db.execute("UPDATE central_jobs SET status='VERIFIED' WHERE job_id='primary'")
        self.db.commit()
        self.assertEqual(rent.poll(self.db,"c1")["action"],"RUN")
        self.db.execute("UPDATE central_jobs SET status='PENDING' WHERE job_id='primary'")
        self.db.commit()
        self.assertEqual(rent.poll(self.db,"c1")["action"],"PAUSE")

    def test_pause_resume_preserves_checkpoint_and_lease_epoch(self):
        j=self.request()
        self.approve(j)
        first=rent.poll(self.db,"c1")["job"]
        epoch=first["lease_epoch"]
        ret=rent.report(self.db,"c1",{"job_id":j["job_id"],"lease_epoch":epoch,
                                    "outcome":"started","event_id":"test-start-1"})
        self.assertEqual(ret["status"],"RUNNING")
        self.assertEqual(rent.report(self.db,"c1",{"job_id":j["job_id"],"lease_epoch":epoch,
              "outcome":"progress","checkpoint":2,"event_id":"test-progress-1"})["checkpoint"],2)
        rent.change(self.db,j["job_id"],"pause")
        self.db.commit()
        self.assertEqual(rent.poll(self.db,"c1")["action"],"PAUSE")
        self.assertEqual(rent.report(self.db,"c1",{"job_id":j["job_id"],"lease_epoch":epoch,
              "outcome":"paused","event_id":"test-paused-1"})["status"],"PAUSED")
        rent.change(self.db,j["job_id"],"resume")
        self.db.commit()
        again=rent.poll(self.db,"c1")["job"]
        self.assertEqual(again["checkpoint"],2)
        self.assertGreater(again["lease_epoch"],epoch)
        with self.assertRaises(PermissionError):
            rent.report(self.db,"c1",{"job_id":j["job_id"],"lease_epoch":epoch,
                   "outcome":"completed","event_id":"test-stale-1"})

    def test_report_idempotent(self):
        j=self.request();self.approve(j)
        p=rent.poll(self.db,"c1")["job"]
        b={"job_id":j["job_id"],"lease_epoch":p["lease_epoch"],"outcome":"started","event_id":"test-same-1"}
        rent.report(self.db,"c1",b)
        self.assertEqual(rent.report(self.db,"c1",b)["status"],"DUPLICATE")
        with self.assertRaises(ValueError):
            rent.report(self.db,"c1",{**b,"outcome":"completed"})

    def test_agent_restart_requires_operator_recovery(self):
        j=self.request()
        self.approve(j)
        lease=rent.poll(self.db,"c1","first-session")["job"]
        self.assertEqual(lease["lease_epoch"],1)
        restart=rent.poll(self.db,"c1","newer-session")
        self.assertEqual(restart["action"],"PAUSE")
        self.assertEqual(self.db.execute("SELECT status FROM rental_jobs WHERE job_id=?",
                                        (j["job_id"],)).fetchone()["status"],"RECOVERY_REQUIRED")
        with self.assertRaises(PermissionError):
            rent.report(self.db,"c1",{"job_id":j["job_id"],"lease_epoch":1,
                       "session_id":"newer-session","event_id":"test-wrong-1","outcome":"completed"})
        rent.change(self.db,j["job_id"],"review_resume")
        self.db.commit()
        reassigned=rent.poll(self.db,"c1","newer-session")["job"]
        self.assertEqual(reassigned["lease_epoch"],2)

    def test_expired_lease_never_automatically_replays(self):
        j=self.request()
        self.approve(j)
        lease=rent.poll(self.db,"c1")["job"]
        self.db.execute("UPDATE rental_jobs SET lease_until=? WHERE job_id=?",
                        (int(time.time())-1,j["job_id"]))
        self.db.commit()
        self.assertEqual(rent.reconcile_expired(self.db),1)
        self.db.commit()
        self.assertEqual(self.db.execute("SELECT status FROM rental_jobs WHERE job_id=?",
                                        (j["job_id"],)).fetchone()["status"],"RECOVERY_REQUIRED")
        self.assertEqual(rent.poll(self.db,"c1")["action"],"WAIT")

    def test_running_time_ticked_by_poll(self):
        j=self.request("reserved",1)
        self.approve(j)
        lease=rent.poll(self.db,"c1")["job"]
        rent.report(self.db,"c1",{"job_id":j["job_id"],"lease_epoch":lease["lease_epoch"],
                                 "event_id":"test-start-tick","outcome":"started"})
        self.db.execute("UPDATE rental_jobs SET last_running_at=? WHERE job_id=?",
                        (int(time.time())-70,j["job_id"]))
        self.db.commit()
        rent.poll(self.db,"c1")
        used=self.db.execute("SELECT used_seconds FROM rental_jobs WHERE job_id=?",
                             (j["job_id"],)).fetchone()["used_seconds"]
        self.assertGreaterEqual(used,69)

    def test_unapproved_route_is_rejected(self):
        with self.assertRaises(ValueError):
            rent.request(self.db,"member1",{"route_id":"https://unverified.invalid","mode":"one_time"})

    def test_pending_limit(self):
        for _ in range(3):self.request()
        with self.assertRaises(ValueError):self.request()

if __name__=="__main__":
    unittest.main()
