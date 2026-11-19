# -*- coding: utf-8 -*-
"""작업별 STOP 파일 (문제 8).

STOP_ALL          : 모든 작업 멈춤
STOP_<작업>       : 그 작업만 멈춤 (예: STOP_court, STOP_map_yanolja)
STOP (예전 파일)   : 법원 작업만 멈춤. 지도·예약 작업은 막지 않음.
"""
from __future__ import annotations

import datetime as dt
import os
import re

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def job_name(job: str) -> str:
    return _SAFE.sub("_", str(job))[:60] or "job"


class StopFiles:
    def __init__(self, root, collector_id=""):
        self.root = root
        self.collector_id = collector_id

    def path(self, job):
        return os.path.join(self.root, "STOP_" + job_name(job))

    def check(self, job, family=None):
        """멈춰야 하면 이유 글, 아니면 None. family: 'court' 등 작업 묶음."""
        fam = family or job_name(job).split("_")[0]
        cands = [("STOP_ALL", "all"), ("STOP_" + job_name(job), "job")]
        if fam and fam != job_name(job):
            cands.append(("STOP_" + fam, "family"))
        if fam == "court":
            cands.append(("STOP", "legacy_court"))
        for fn, why in cands:
            p = os.path.join(self.root, fn)
            if os.path.exists(p):
                try:
                    body = open(p, encoding="utf-8", errors="replace").read(200).strip()
                except OSError:
                    body = ""
                return "%s:%s %s" % (why, fn, body)
        return None

    def write(self, job, why):
        os.makedirs(self.root, exist_ok=True)
        cid = ("[%s] " % self.collector_id) if self.collector_id else ""
        with open(self.path(job), "a", encoding="utf-8") as f:
            f.write("%s %s%s\n" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), cid, why))
        return self.path(job)
