"""Offline regression for central members and four-bucket point accounting.

Run: python3 -m unittest -v test_member_core test_rental_control
All data is synthetic; no network, live member, payment, NAS or Github Actions.
"""
import os
import sqlite3
import unittest
from unittest.mock import patch
import member_core as m

class MemberCoreTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.db.row_factory=sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        m.init(self.db)
        self.db.commit()
        self.member=m.create_member(self.db,member_id="member_001",status="pilot")["member_id"]
        self.db.commit()
        self.env=patch.dict(os.environ,{"BIDGUARD_MEMBER_TEST_MODE":"1"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.db.close)

    def grant(self,point_type,units,key):
        ret=m.test_grant(self.db,self.member,point_type,units,key)
        self.db.commit()
        return ret

    def charge(self,amount,key,service="COLLECT_HTML",overdraft_units=0):
        ret=m.charge_internal(self.db,self.member,service,amount,key,
                              overdraft_units=overdraft_units)
        self.db.commit()
        return ret

    def test_member_created_without_real_points(self):
        p=m.profile(self.db,self.member)
        self.assertEqual(p["status"],"pilot")
        self.assertEqual(p["roles"],["member"])
        self.assertEqual(p["points_units"],{"P":0,"J":0,"K":0,"S":0})

    def test_four_point_buckets_and_spending_order(self):
        self.grant("S",3000,"credit-s-00001")
        self.grant("P",5000,"credit-p-00001")
        self.grant("J",4000,"credit-j-00001")
        self.grant("K",2000,"credit-k-00001")
        self.charge(10000,"charge-order-00001")
        self.assertEqual(m.wallet(self.db,self.member),{"P":0,"J":2000,"K":2000,"S":0})
        ledger=self.db.execute("""SELECT point_type,delta_units FROM point_entries
                WHERE transaction_id=(SELECT transaction_id FROM point_transactions
                 WHERE request_key='charge-order-00001')
                ORDER BY entry_id""").fetchall()
        self.assertEqual([(r["point_type"],r["delta_units"]) for r in ledger],
                         [("S",-3000),("P",-5000),("J",-2000)])

    def test_idempotent_credit_and_charge(self):
        first=self.grant("P",10000,"credit-dedupe-0001")
        again=self.grant("P",10000,"credit-dedupe-0001")
        self.assertFalse(first["duplicate"])
        self.assertTrue(again["duplicate"])
        a=self.charge(550,"charge-dedupe-0001")
        b=self.charge(550,"charge-dedupe-0001")
        self.assertFalse(a["duplicate"])
        self.assertTrue(b["duplicate"])
        self.assertEqual(m.wallet(self.db,self.member)["P"],9450)
        with self.assertRaises(ValueError):
            self.charge(600,"charge-dedupe-0001")

    def test_insufficient_balance_rolls_back_every_entry(self):
        self.grant("S",50,"credit-small-0001")
        before=self.db.execute("SELECT COUNT(*) FROM point_entries").fetchone()[0]
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT_POINTS"):
            self.charge(100,"charge-too-much-0001")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM point_entries").fetchone()[0],before)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM point_transactions").fetchone()[0],1)
        self.assertEqual(m.wallet(self.db,self.member)["S"],50)

    def test_s_not_usable_in_shopping(self):
        self.grant("S",1000,"credit-shop-0001")
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT_POINTS"):
            self.charge(1000,"shopping-charge-0001",service="SHOPPING_PURCHASE")
        self.assertEqual(m.wallet(self.db,self.member)["S"],1000)

    def test_collector_specific_overdraft_limit(self):
        self.charge(20000,"collector-overdraft-001",overdraft_units=20000)
        self.assertEqual(m.wallet(self.db,self.member)["P"],-20000)
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT_POINTS"):
            self.charge(1,"collector-overdraft-002",overdraft_units=20000)
        with self.assertRaises(ValueError):
            self.charge(1,"other-overdraft-00001",service="GAME_ROUND",
                        overdraft_units=20000)

    def test_server_role_and_suspend_member(self):
        self.assertIn("collector_user",m.grant_role(self.db,self.member,"collector_user","admin"))
        self.assertEqual(m.set_member_status(self.db,self.member,"suspended","admin"),"suspended")
        with self.assertRaises(ValueError):
            self.grant("P",100,"credit-suspend-0001")
        with self.assertRaises(ValueError):
            m.set_member_status(self.db,self.member,"active","admin")

    def test_verified_identity_links_only_with_secret(self):
        with self.assertRaises(ValueError):
            m.link_verified_identity(self.db,self.member,"google","external-uid","short")
        m.link_verified_identity(self.db,self.member,"kakao","provider-subject","secret"*8)
        profile=m.profile(self.db,self.member)
        self.assertEqual(profile["status"],"active")
        self.assertNotIn("provider-subject",str(self.db.execute("SELECT * FROM member_identities").fetchall()))

    def test_append_only_ledger_and_audit(self):
        self.grant("P",100,"credit-immutable-001")
        for sql in ["DELETE FROM point_entries","UPDATE point_entries SET delta_units=0",
                    "DELETE FROM point_transactions","DELETE FROM member_events"]:
            with self.assertRaises(sqlite3.IntegrityError):
                self.db.execute(sql)
            self.db.rollback()

    def test_test_grant_disabled_without_explicit_mode(self):
        self.env.stop()
        try:
            with self.assertRaises(PermissionError):
                m.test_grant(self.db,self.member,"P",100,"credit-not-on-00001")
        finally:
            self.env.start()

    def test_admin_api_requires_authorization(self):
        class Handler:
            def __init__(self,con,allowed):
                self.con=con
                self.allowed=allowed
                self.reply=None
                self.headers={}
            def _admin(self):
                return self.allowed
            def db_module(self):
                return self.con
            def _body(self):
                return {}
            def _json(self,status,obj):
                self.reply=(status,obj)
        h=Handler(self.db,False)
        self.assertTrue(m.route(h,"GET","/api/admin/members",{}))
        self.assertEqual(h.reply[0],403)
        h.allowed=True
        self.assertTrue(m.route(h,"GET","/api/admin/members/detail",
                                {"member_id":self.member}))
        self.assertEqual(h.reply[0],200)
        self.assertNotIn("access_code",str(h.reply[1]))

if __name__=="__main__":
    unittest.main()
