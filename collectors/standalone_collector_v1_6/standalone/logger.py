# -*- coding: utf-8 -*-
"""JSONL 기록. 쓰기 전에 비밀값을 *** 로 바꾼다."""
from __future__ import annotations

import datetime as dt
import json
import os


class JsonLog:
    def __init__(self, log_dir, job, redactor=None, echo=True, now=dt.datetime.now, collector_id=""):
        self.dir, self.job, self.red, self.echo, self.now = log_dir, job, redactor, echo, now
        self.collector_id = collector_id
        os.makedirs(log_dir, exist_ok=True)
        self.path = os.path.join(log_dir, "%s_%s.jsonl" % (job, now().strftime("%Y%m%d")))
        self.events = []

    def __call__(self, ev, **k):
        rec = {"t": self.now().strftime("%Y-%m-%d %H:%M:%S"), "collector_id": self.collector_id, "job": self.job, "ev": ev}
        rec.update(k)
        if self.red:
            rec = self.red.obj(rec)
        self.events.append(rec)
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.echo:
            print(line, flush=True)
        return rec
