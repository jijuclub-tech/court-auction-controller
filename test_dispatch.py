"""Synthetic two-node tests; no external service, NAS, or GitHub Actions."""
import concurrent.futures
import hashlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
import collector_dispatch as d

class DualCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / 'jobs.sqlite')
        c = self.con()
        try: d.init(c)
        finally: c.close()

    def tearDown(self):
        self.tmp.cleanup()

    def con(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        return c

    def add(self, case='2026ta100', item='1', target=None):
        c = self.con()
        try:
            return d.enqueue(c,dict(source_id='court',court_id='seoul',case_no=case,item_no=item,
                                    doc_type='appraisal',doc_version='v1',target_name=target))
        finally: c.close()

    def take(self,name):
        c = self.con()
        try: return d.claim(c,name)
        finally: c.close()

    def test_two_collectors_one_job(self):
        job = self.add()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a,b = list(pool.map(self.take,['seoul-1','jeju-2']))
        self.assertEqual(sum(x is not None for x in (a,b)),1)
        self.assertEqual(next(x['job_id'] for x in (a,b) if x),job['job_id'])

    def test_20_simultaneous_claims_no_duplicate(self):
        for i in range(30): self.add(case='C'+str(i))
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            out = list(pool.map(self.take,['C'+str(i) for i in range(20)]))
        ids = [x['job_id'] for x in out if x]
        self.assertEqual(len(ids),len(set(ids)))

    def test_ack_and_idempotent_result(self):
        self.add()
        a=self.take('seoul-1')
        self.assertEqual(a,self.take('seoul-1'))
        self.assertIsNone(self.take('jeju-2'))
        c=self.con()
        try:
            self.assertEqual(d.ack(c,'seoul-1',a)['status'],'ACKED')
            report=dict(job_id=a['job_id'],lease_epoch=a['lease_epoch'],event_id='unique-0001',
                        outcome='success',sha256='a'*64,size_bytes=4)
            self.assertEqual(d.report(c,'seoul-1',report)['status'],'REPORTED')
            self.assertEqual(d.report(c,'seoul-1',report)['status'],'DUPLICATE_DELIVERY')
            with self.assertRaises(FileExistsError):d.report(c,'seoul-1',dict(report,size_bytes=5))
        finally: c.close()
        self.assertIsNone(self.take('jeju-2'))

    def test_expired_lease_requires_reconcile(self):
        self.add()
        a=self.take('seoul-1')
        c=self.con()
        try:
            c.execute('UPDATE central_jobs SET lease_until=1 WHERE job_id=?',(a['job_id'],))
            c.commit()
        finally: c.close()
        self.assertIsNone(self.take('jeju-2'))
        c=self.con()
        try:
            self.assertEqual(c.execute('SELECT status FROM central_jobs').fetchone()[0],'REVIEW')
            with self.assertRaises(PermissionError): d.ack(c,'seoul-1',a)
            d.reconcile(c,dict(job_id=a['job_id']))
        finally: c.close()
        b=self.take('jeju-2')
        self.assertEqual(b['lease_epoch'],a['lease_epoch']+1)
        c=self.con()
        try:
            with self.assertRaises(PermissionError):d.ack(c,'seoul-1',a)
        finally: c.close()

    def test_target_and_item_separation(self):
        j=self.add(case='A',item='1',target='jeju-2')
        self.add(case='A',item='2',target='seoul-1')
        self.assertEqual(self.take('seoul-1')['item_no'],'2')
        self.assertEqual(self.take('jeju-2')['job_id'],j['job_id'])

    def test_no_false_nas_verified(self):
        self.add()
        a=self.take('seoul-1')
        c=self.con()
        try:
            d.ack(c,'seoul-1',a)
            d.report(c,'seoul-1',dict(job_id=a['job_id'],lease_epoch=a['lease_epoch'],
                     event_id='example-01',outcome='success',
                     sha256=hashlib.sha256(b'abc').hexdigest(),size_bytes=3))
            old=os.environ.pop('BIDGUARD_NAS_ROOT',None)
            try:
                with self.assertRaises(ValueError):
                    d.verify(c,dict(job_id=a['job_id'],nas_relative_path='file.pdf'))
            finally:
                if old is not None: os.environ['BIDGUARD_NAS_ROOT']=old
            self.assertEqual(c.execute('SELECT status FROM central_jobs').fetchone()[0],'REPORTED')
        finally: c.close()

if __name__ == '__main__': unittest.main()
